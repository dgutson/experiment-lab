"""Read-only DuckDB connections with lab.sql's views, for the lab pages.

MLflow writes its database through Python's sqlite3 in this process. DuckDB
reads SQLite files through its own copy of SQLite, and two copies of SQLite in
one process do not see each other's locks
(https://www.sqlite.org/howtocorrupt.html, section 2.2.1), so a DuckDB read of
the live file could see a write half done. The pages therefore read a
snapshot that Python's sqlite3 copies with SQLite's backup API. MLflow's
tables are copied from it into the schema mlflow of a DuckDB database file,
lab.sql adds its views, and each page opens that file read-only. The file is
built again whenever MLflow's database or lab.sql has changed.
"""
import atexit
import contextlib
import os
import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path

import duckdb

VIEWS = Path(__file__).resolve().parents[2] / "lab.sql"
# The MLflow tables lab.sql reads; q.py defines the same ones as views.
MLFLOW_TABLES = ("experiments", "experiment_tags", "runs", "tags", "params", "latest_metrics")
CONFIG = {"threads": 2, "enable_external_access": False, "lock_configuration": True}


class Store:
    def __init__(self, database):
        self.source = sqlite3.connect(f"file:{database}?mode=ro", uri=True, isolation_level=None,
                                      check_same_thread=False)
        self.folder = tempfile.mkdtemp(prefix="mlflow-lab-")
        atexit.register(shutil.rmtree, self.folder, ignore_errors=True)
        self.lock = threading.Lock()
        self.database = None
        self.version = None
        self.builds = 0

    def refresh(self):
        version = (self.source.execute("PRAGMA data_version").fetchone()[0], VIEWS.stat().st_mtime_ns)
        if self.database and version == self.version:
            return
        snapshot = os.path.join(self.folder, "mlflow.db")
        target = sqlite3.connect(snapshot)
        self.source.backup(target)
        target.close()
        self.builds += 1
        database = os.path.join(self.folder, f"lab-{self.builds}.duckdb")
        con = duckdb.connect(database)
        try:
            con.execute(f"ATTACH '{snapshot}' AS ml (TYPE sqlite, READ_ONLY)")
            con.execute("CREATE SCHEMA mlflow")
            for table in MLFLOW_TABLES:
                con.execute(f"CREATE TABLE mlflow.{table} AS FROM ml.{table}")
            con.execute("DETACH ml")
            for statement in con.extract_statements(VIEWS.read_text()):
                con.execute(statement)
        finally:
            con.close()
            os.remove(snapshot)
        if self.database:
            os.remove(self.database)
        self.database, self.version = database, version

    @contextlib.contextmanager
    def connect(self):
        """A read-only connection to lab.sql's views over the current data,
        unable to read or write other files. One at a time, so that a database
        file is never replaced while a connection reads it."""
        with self.lock:
            self.refresh()
            con = duckdb.connect(self.database, read_only=True, config=CONFIG)
            try:
                yield con
            finally:
                con.close()
