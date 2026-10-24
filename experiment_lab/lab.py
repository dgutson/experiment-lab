"""Writing to the lab's database, lab.db in the data folder: experiments, and
runs with their params, metrics, tags and files. It uses only Python's standard
library, so a logging script can import it from any Python 3.12 with
PYTHONPATH=<clone of experiment-lab>.

    from experiment_lab.lab import Lab
    lab = Lab()                                  # $EXPERIMENT_LAB_DATA, else ~/lab-data
    lab.experiment("my project", description="What it measures.")
    with lab.run("my project", "run 1", params={"rate": 48000}) as run:
        run.log_metrics({"accuracy": 0.91}, step=3)
        run.set_tags({"outcome": "pass"})
        run.log_file("plot.png")                 # copied to files/<run id>/plot.png

A run ends FINISHED when its with block ends, FAILED on an exception, KILLED
on Ctrl-C; without a with block, call end(). Several processes may write at
once without a server: the database is in WAL mode, every write is one short
transaction, and a writer waits up to a minute for another's to end.
"""
import contextlib
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from experiment_lab.settings import data_folder, database

SCHEMA = Path(__file__).with_name("schema.sql")


def now():
    return int(time.time() * 1000)


class Lab:
    def __init__(self, data=None):
        """Opens the data folder's lab.db, making the folder and the tables if missing."""
        self.data = Path(data) if data else data_folder()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database(self.data), timeout=60, isolation_level=None)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA.read_text())

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def write(self):
        """One transaction, started at once so that it waits for any other writer."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def experiment(self, name, description=None):
        """Makes the experiment if there is none of that name, and sets its
        description when one is given. A new experiment needs a description
        (Markdown: what it measures and why), which is what finds it in a search."""
        with self.write() as db:
            if description:
                db.execute("INSERT INTO experiments (name, description, created) VALUES (?, ?, ?)"
                           " ON CONFLICT (name) DO UPDATE SET description = excluded.description",
                           (name, description, now()))
            elif not db.execute("SELECT 1 FROM experiments WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"a new experiment needs a description: {name}")

    def check_experiment(self, name):
        if not self.db.execute("SELECT 1 FROM experiments WHERE name = ?", (name,)).fetchone():
            raise ValueError(f"no experiment {name!r}; make it with Lab.experiment or rec experiment")

    def run(self, experiment, name, *, note="", params=None, tags=None, start=None):
        """Starts a run, RUNNING, in an existing experiment."""
        self.check_experiment(experiment)
        run = Run(self, uuid.uuid4().hex)
        with self.write() as db:
            db.execute("INSERT INTO runs (id, experiment, name, status, start_time, note) VALUES (?, ?, ?, ?, ?, ?)",
                       (run.id, experiment, name, "RUNNING", start or now(), note))
        if params:
            run.log_params(params)
        if tags:
            run.set_tags(tags)
        return run


class Run:
    def __init__(self, lab, rid):
        self.lab, self.id = lab, rid

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        self.end("FINISHED" if kind is None else "KILLED" if issubclass(kind, KeyboardInterrupt) else "FAILED")

    def put(self, table, values):
        with self.lab.write() as db:
            db.executemany(f"INSERT INTO {table} (run, key, value) VALUES (?, ?, ?)"
                           " ON CONFLICT (run, key) DO UPDATE SET value = excluded.value",
                           [(self.id, k, str(v)) for k, v in values.items()])

    def log_params(self, params):
        self.put("params", params)

    def set_tags(self, tags):
        self.put("tags", tags)

    def log_metrics(self, metrics, step=0, at=None):
        with self.lab.write() as db:
            db.executemany("INSERT INTO metrics (run, key, value, step, time) VALUES (?, ?, ?, ?, ?)",
                           [(self.id, k, float(v), step, at or now()) for k, v in metrics.items()])

    def set_note(self, text):
        with self.lab.write() as db:
            db.execute("UPDATE runs SET note = ? WHERE id = ?", (text, self.id))

    def log_file(self, path, name=None):
        """Copies the file to files/<run id>/<name> in the data folder (name:
        the file's own) and returns the copy's path."""
        target = self.lab.data / "files" / self.id / (name or Path(path).name)
        target.parent.mkdir(parents=True, exist_ok=True)
        return shutil.copy2(path, target)

    def end(self, status="FINISHED"):
        with self.lab.write() as db:
            db.execute("UPDATE runs SET status = ?, end_time = ? WHERE id = ?", (status, now(), self.id))


if __name__ == "__main__":  # makes the data folder's lab.db, for lab-server
    Lab().close()
