"""Writing to the lab's database, lab.db in the data folder: projects, the
experiments below them, and runs with their params, metrics, tags and files.
It uses only Python's standard library, so a logging script can import it from
any Python 3.12 with PYTHONPATH=<clone of experiment-lab>.

    from experiment_lab.lab import Lab
    lab = Lab()                                  # $EXPERIMENT_LAB_DATA, else ~/lab-data
    lab.project("my-project", "What the project is.")   # once, from the project's folder
    lab.experiment("rate test", "What it measures and why.")
    with lab.run("rate test", "run 1", params={"rate": 48000}) as run:
        run.log_metrics({"accuracy": 0.91}, step=3)
        run.set_tags({"outcome": "pass"})
        run.log_file("plot.png")                 # copied to files/<run id>/plot.png

The experiments form a tree whose roots are the projects. The project of the
current folder is the one whose folder holds it, or holds the main worktree of
its git repository; a new experiment goes directly below that project unless a
parent is given, and from a project's folder only that project's experiments
are used.

A run ends FINISHED when its with block ends, FAILED on an exception, KILLED
on Ctrl-C; without a with block, call end(). Several processes may write at
once without a server: the database is in WAL mode, every write is one short
transaction, and a writer waits up to a minute for another's to end.
"""
import contextlib
import shutil
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from functools import cached_property
from pathlib import Path
from typing import NamedTuple, Self

from experiment_lab.settings import data_folder, database

SCHEMA = Path(__file__).with_name("schema.sql")
# the experiment given as the parameter, and those below it, as the table below
BELOW = ("WITH RECURSIVE below(name) AS (SELECT ? UNION ALL"
         " SELECT e.name FROM experiments e JOIN below ON e.parent = below.name)")

type StrPath = str | Path


class Project(NamedTuple):
    name: str
    folder: str


def now() -> int:
    return int(time.time() * 1000)


def working_folders() -> list[Path]:
    """The current folder, then, inside a git repository, its main worktree,
    so that the other worktrees of a repository share its project."""
    here = Path.cwd().resolve()
    try:
        git = subprocess.run(["git", "worktree", "list", "--porcelain"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return [here]
    return [here, Path(git.stdout.split("\n", 1)[0].removeprefix("worktree ")).resolve()]


def holding(projects: Iterable[Project]) -> str | None:
    """Of the projects, the one whose folder holds the current folder or its
    repository's main worktree, the innermost if several; None if none."""
    folders = working_folders()
    found = [(len(Path(folder).parts), name) for name, folder in projects
             if any(f.is_relative_to(folder) for f in folders)]
    return max(found)[1] if found else None


def absolute(path: StrPath) -> str:
    return str(Path(path).expanduser().resolve())


def defaults(name: str, changes: dict[str, str | None]) -> dict[str, str | None]:
    """The changes that make a new project: it needs a description; its folder
    is by default the main worktree of the current git repository, else the
    current folder, and its files folder its folder."""
    if not changes["description"]:
        raise ValueError(f"a new project needs a description: {name}")
    folder = changes["folder"] or str(working_folders()[-1])
    return {**changes, "folder": folder, "files": changes["files"] or folder}


def check_folder(db: sqlite3.Connection, name: str, folder: str | None) -> None:
    """Refuses folder for the project name if it is another project's."""
    taken = folder and db.execute("SELECT name FROM experiments WHERE folder = ? AND name <> ?",
                                  (folder, name)).fetchone()
    if taken:
        raise ValueError(f"{folder} is the folder of the project {taken[0]}")


def update(db: sqlite3.Connection, name: str, changes: Mapping[str, object]) -> None:
    """Sets those columns of the experiment's row that changes gives as not None."""
    for column, value in changes.items():
        if value is not None:
            db.execute(f"UPDATE experiments SET {column} = ? WHERE name = ?", (value, name))


class Lab:
    def __init__(self, data: StrPath | None = None) -> None:
        """Opens the data folder's lab.db, making the folder and the tables if missing."""
        self.data = Path(data) if data else data_folder()
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database(self.data), timeout=60, isolation_level=None)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA.read_text())

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One transaction, started at once so that it waits for any other writer."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    @cached_property
    def current(self) -> str | None:
        """The project of the current folder (holding); None if none."""
        return holding(Project(*row) for row in self.db.execute("SELECT name, folder FROM experiments"
                                                                 " WHERE parent IS NULL"))

    def project(self, name: str, description: str | None = None, *, folder: StrPath | None = None,
                files: StrPath | None = None, views: Sequence[StrPath] | None = None) -> None:
        """Makes the project, an experiment with no parent, if there is none of
        that name, or changes what is given. A new project needs a description
        (one line: what the project is). Its folder is by default the main
        worktree of the current git repository, else the current folder; files,
        the folder its files page shows, is by default its folder; views lists
        its view files."""
        changes = {"description": description or None, "folder": folder and absolute(folder),
                   "files": files and absolute(files), "views": None if views is None else "; ".join(map(absolute, views))}
        with self.write() as db:
            found = db.execute("SELECT parent FROM experiments WHERE name = ?", (name,)).fetchone()
            if found is None:
                changes = defaults(name, changes)
            elif found[0] is not None:
                raise ValueError(f"{name} is an experiment below {found[0]}, not a project")
            check_folder(db, name, changes["folder"])
            if found is None:
                db.execute("INSERT INTO experiments (name, created, folder, files) VALUES (?, ?, ?, ?)",
                           (name, now(), changes["folder"], changes["files"]))
            update(db, name, changes)
        self.__dict__.pop("current", None)

    def experiment(self, name: str, description: str | None = None, *, parent: str | None = None,
                   dead_end: bool | None = None) -> list[str]:
        """Makes the experiment if there is none of that name, below parent (by
        default the project of the current folder), or changes what is given:
        its description (Markdown: what it measures and why, which a new
        experiment needs and a search finds), its parent (moving it with the
        experiments below it), whether it is a dead end (an idea that failed).
        Returns the names from its project down to it."""
        with self.write() as db:
            found = db.execute("SELECT parent FROM experiments WHERE name = ?", (name,)).fetchone()
            if found is None:
                if not description:
                    raise ValueError(f"a new experiment needs a description: {name}")
                parent = parent or self.here()
            else:
                self.check_experiment(name)
            if parent is not None:
                self.check_parent(name, parent, is_project=found is not None and found[0] is None)
            if found is None:
                db.execute("INSERT INTO experiments (name, parent, created) VALUES (?, ?, ?)", (name, parent, now()))
            update(db, name, {"parent": parent, "description": description or None, "dead_end": dead_end})
        return self.path(name)

    def check_parent(self, name: str, parent: str, is_project: bool) -> None:
        """Refuses parent as the parent of the experiment name: it must exist in
        the project of the current folder, name may not be a project, and parent
        may not be name or below it."""
        self.check_experiment(parent)
        if is_project:
            raise ValueError(f"{name} is a project, which has no parent")
        if name in self.path(parent):
            raise ValueError(f"{parent} is {name} or below it")

    def here(self) -> str:
        """The project of the current folder; a ValueError if none holds it."""
        if self.current is None:
            folder = working_folders()[-1]
            raise ValueError(f"no project holds {folder}; make it first:"
                             f' rec project {folder.name} --text "what the project is, in one line"')
        return self.current

    def path(self, name: str | None) -> list[str]:
        """The names from the experiment's project down to it."""
        names = []
        while name is not None:
            found = self.db.execute("SELECT parent FROM experiments WHERE name = ?", (name,)).fetchone()
            if found is None:
                raise ValueError(f"no experiment {name!r}; make it with rec experiment or Lab.experiment")
            names.insert(0, name)
            name = found[0]
        return names

    def check_experiment(self, name: str) -> None:
        """Refuses an experiment that does not exist, or that is in another
        project than the one of the current folder."""
        project = self.path(name)[0]
        if self.current and project != self.current:
            raise ValueError(f"{name!r} is in the project {project}, not in {self.current}, whose folder this is")

    def run(self, experiment: str, name: str, *, note: str = "", params: Mapping[str, object] | None = None,
            tags: Mapping[str, object] | None = None, start: int | None = None) -> "Run":
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
    def __init__(self, lab: Lab, rid: str) -> None:
        self.lab, self.id = lab, rid

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind: type[BaseException] | None, *_: object) -> None:
        self.end("FINISHED" if kind is None else "KILLED" if issubclass(kind, KeyboardInterrupt) else "FAILED")

    def put(self, table: str, values: Mapping[str, object]) -> None:
        with self.lab.write() as db:
            db.executemany(f"INSERT INTO {table} (run, key, value) VALUES (?, ?, ?)"
                           " ON CONFLICT (run, key) DO UPDATE SET value = excluded.value",
                           [(self.id, k, str(v)) for k, v in values.items()])

    def log_params(self, params: Mapping[str, object]) -> None:
        self.put("params", params)

    def set_tags(self, tags: Mapping[str, object]) -> None:
        self.put("tags", tags)

    def log_metrics(self, metrics: Mapping[str, float], step: int = 0, at: int | None = None) -> None:
        with self.lab.write() as db:
            db.executemany("INSERT INTO metrics (run, key, value, step, time) VALUES (?, ?, ?, ?, ?)",
                           [(self.id, k, float(v), step, at or now()) for k, v in metrics.items()])

    def set_note(self, text: str) -> None:
        with self.lab.write() as db:
            db.execute("UPDATE runs SET note = ? WHERE id = ?", (text, self.id))

    def log_file(self, path: StrPath, name: str | None = None) -> Path:
        """Copies the file to files/<run id>/<name> in the data folder (name:
        the file's own) and returns the copy's path."""
        target = self.lab.data / "files" / self.id / (name or Path(path).name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    def end(self, status: str = "FINISHED") -> None:
        with self.lab.write() as db:
            db.execute("UPDATE runs SET status = ?, end_time = ? WHERE id = ?", (status, now(), self.id))


if __name__ == "__main__":  # makes the data folder's lab.db, for lab-server
    try:
        Lab().close()
    except (OSError, sqlite3.Error) as e:
        raise SystemExit(f"error: {e}")
