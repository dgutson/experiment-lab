"""lab.db's version, and the steps that upgrade an older lab.db one version at
a time.

The version is SQLite's PRAGMA user_version; a lab.db with none set is of
version 0. STEPS[i] takes lab.db from version i to i + 1, and VERSION, the
number of steps, is the version of a lab.db that this code makes or upgrades.
A new lab.db gets the tables of schema.sql and VERSION directly, so
schema.sql's tables must be those that the steps make from version 0.

A change to the tables adds a step at the end of STEPS, named after the
version it makes (to_2, to_3, ...). A step:
- runs inside the upgrade's transaction, so it uses db.execute, never
  executescript, which commits;
- holds its own copy of the SQL it runs instead of reading schema.sql, so that
  a later change to schema.sql does not change what it does;
- does not change once on main, since lab.db files that it upgraded exist.

It uses only Python's standard library, as lab.py, which imports it, does.
"""
import contextlib
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from experiment_lab.settings import database

SCHEMA = Path(__file__).with_name("schema.sql")

type Step = Callable[[sqlite3.Connection, Path], None]  # lab.db open as the connection, and its data folder


def to_1(db: sqlite3.Connection, data: Path) -> None:
    """The table files, with a row for each file in files/<run id>/ of a run.
    A lab.db of version 0 may have it already."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'files'").fetchone():
        return
    db.execute("CREATE TABLE files (run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,"
               " name TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY (run, name))")
    ids = {rid for (rid,) in db.execute("SELECT id FROM runs")}
    db.executemany("INSERT INTO files (run, name, bytes) VALUES (?, ?, ?)",
                   [(d.name, p.relative_to(d).as_posix(), p.stat().st_size)
                    for d in (data / "files").glob("*") if d.name in ids for p in d.rglob("*") if p.is_file()])


STEPS: list[Step] = [to_1]
VERSION = len(STEPS)


def version(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def backup(data: Path, found: int) -> Path:
    """Copies the data folder's lab.db, of version found, to lab.db.v<found>
    there, through connections of its own, which read lab.db while the
    upgrade's connection holds the write lock."""
    copy = data / f"lab.db.v{found}"
    with (contextlib.closing(sqlite3.connect(database(data))) as source,
          contextlib.closing(sqlite3.connect(copy)) as target):
        source.backup(target)
    return copy


def upgrade(db: sqlite3.Connection, data: Path) -> None:
    """Brings the data folder's lab.db, open as db, to VERSION. A new one gets
    the tables of schema.sql. An older one is copied (backup), then the steps
    it lacks run in one transaction, and stderr says where the copy is. A newer
    one is refused."""
    if version(db) == VERSION:
        return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone():
        # each statement makes only what is missing, so two processes may make lab.db at once
        db.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA.read_text()}\nPRAGMA user_version = {VERSION};\nCOMMIT;")
        return
    db.execute("BEGIN IMMEDIATE")
    with db:  # commits, or rolls back on an exception
        found = version(db)  # again, now that no other process writes
        if found > VERSION:
            raise ValueError(f"{database(data)} is of version {found}, newer than this experiment-lab's"
                             f" {VERSION}: update experiment-lab (README, Updating)")
        if found == VERSION:  # another process upgraded it
            return
        copy = backup(data, found)
        for step in STEPS[found:]:
            step(db, data)
        db.execute(f"PRAGMA user_version = {VERSION}")
    print(f"upgraded lab.db from version {found} to {VERSION}; its copy from before is {copy}", file=sys.stderr)
