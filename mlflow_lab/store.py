"""Read-only DuckDB connections with the views of lab.sql and lab.toml's view
files, for the lab pages.

MLflow writes its database through Python's sqlite3 in this process. DuckDB
reads SQLite files through its own copy of SQLite, and two copies of SQLite in
one process do not see each other's locks
(https://www.sqlite.org/howtocorrupt.html, section 2.2.1), so a DuckDB read of
the live file could see a write half done. The pages therefore read a
snapshot that Python's sqlite3 copies with SQLite's backup API into the folder
lab-cache next to the database. MLflow's
tables are copied from it into the schema mlflow of a DuckDB database file,
lab.sql and the view files add their views, and each page opens that file
read-only. The file is built again whenever MLflow's database or a view file
has changed.
"""
import atexit
import contextlib
import os
import shutil
import sqlite3
import threading
from pathlib import Path

import duckdb

LAB_SQL = Path(__file__).resolve().parent / "lab.sql"
# The MLflow tables lab.sql reads; bin/q.py defines the same ones as views.
MLFLOW_TABLES = ("experiments", "experiment_tags", "runs", "tags", "params", "latest_metrics")
CONFIG = {"threads": 2, "enable_external_access": False, "lock_configuration": True}


class Store:
    def __init__(self, database, views=()):
        """views: the SQL files run after lab.sql, in order."""
        self.source = sqlite3.connect(f"file:{database}?mode=ro", uri=True, isolation_level=None,
                                      check_same_thread=False)
        self.views = [LAB_SQL, *views]
        # lab-cache next to the database, emptied at start, since a server stopped
        # by a signal never runs atexit; so one server per database.
        self.folder = os.path.join(os.path.dirname(database), "lab-cache")
        shutil.rmtree(self.folder, ignore_errors=True)
        os.mkdir(self.folder)
        atexit.register(shutil.rmtree, self.folder, ignore_errors=True)
        self.lock = threading.Lock()
        self.database = None
        self.version = None
        self.builds = 0

    def refresh(self):
        version = (self.source.execute("PRAGMA data_version").fetchone()[0],
                   tuple(path.stat().st_mtime_ns for path in self.views))
        if self.database and version == self.version:
            return
        snapshot = os.path.join(self.folder, "mlflow.db")
        target = sqlite3.connect(snapshot)
        self.source.backup(target)
        target.close()
        self.builds += 1
        database = os.path.join(self.folder, f"lab-{self.builds}.duckdb")
        con = duckdb.connect(database)
        built = False
        try:
            con.execute(f"ATTACH '{snapshot}' AS ml (TYPE sqlite, READ_ONLY)")
            con.execute("CREATE SCHEMA mlflow")
            for table in MLFLOW_TABLES:
                con.execute(f"CREATE TABLE mlflow.{table} AS FROM ml.{table}")
            con.execute("DETACH ml")
            for path in self.views:
                for statement in con.extract_statements(path.read_text()):
                    con.execute(statement)
            built = True
        finally:
            con.close()
            os.remove(snapshot)
            if not built:  # a broken view file fails every request until it is fixed
                os.remove(database)
        if self.database:
            os.remove(self.database)
        self.database, self.version = database, version

    @contextlib.contextmanager
    def connect(self):
        """A read-only connection to the views over the current data,
        unable to read or write other files. One at a time, so that a database
        file is never replaced while a connection reads it."""
        with self.lock:
            self.refresh()
            con = duckdb.connect(self.database, read_only=True, config=CONFIG)
            try:
                yield con
            finally:
                con.close()
