"""lab.db's version, and the upgrades that bring an older lab.db to the tables
of this code.

lab.db's version is the experiment-lab version whose tables it has. Each
upgrade is a function decorated with @version("major.minor.patch"): for that
version, it changes the tables of the version of the upgrade below it into
that version's. latest(), the highest of these versions, is the version of a
lab.db that this code makes or upgrades. An older lab.db goes through the
upgrades above its version, lowest first (pending). A new lab.db gets the
tables of schema.sql and latest() directly, so schema.sql's tables must be
those that the upgrades make from a lab.db of version 0.0.0.

The version is kept in SQLite's PRAGMA user_version, an integer, as
major * 1000000 + minor * 1000 + patch, so each part is below 1000; a lab.db
with none set is of version 0.0.0.

A change to the tables raises the version in pyproject.toml and adds an
upgrade: a function named _ decorated with that version, which must be above
every other upgrade's. An upgrade:
- runs inside the transaction of upgrade(), so it uses db.execute, never
  executescript, which commits;
- holds its own copy of the SQL it runs instead of reading schema.sql, so that
  a later change to schema.sql does not change what it does;
- does not change once on main, since lab.db files that it upgraded exist.

A process that reads lab.db through DuckDB must not open it with sqlite3
(query.py's docstring), so it has lab.db made or upgraded in a child process
(upgrade_in_child, upgrade_attached).

It uses only Python's standard library, as lab.py, which imports it, does;
DuckDB is imported for the type checkers alone.
"""
import contextlib
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from experiment_lab.settings import database

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

SCHEMA = Path(__file__).with_name("schema.sql")


class Version(NamedTuple):
    """An experiment-lab version; versions compare as their parts do."""
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def stored(self) -> int:
        """As PRAGMA user_version holds it."""
        return (self.major * 1000 + self.minor) * 1000 + self.patch


type Upgrade = Callable[[sqlite3.Connection, Path], None]  # lab.db open as the connection, and its data folder

UPGRADES: dict[Version, Upgrade] = {}  # by the version whose tables each makes


def parsed(text: str) -> Version:
    """The version text writes as major.minor.patch, each part below 1000."""
    parts = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})", text, re.ASCII)
    if not parts:
        raise ValueError(f"{text!r} is not a version major.minor.patch, each part below 1000")
    return Version(int(parts[1]), int(parts[2]), int(parts[3]))


def version(text: str) -> Callable[[Upgrade], Upgrade]:
    """Makes the function it decorates the upgrade for version text."""
    to = parsed(text)
    if to in UPGRADES:
        raise ValueError(f"two upgrades for version {to}")

    def register(function: Upgrade) -> Upgrade:
        UPGRADES[to] = function
        return function
    return register


def latest() -> Version:
    """The version of a lab.db that this code makes or upgrades: the highest upgrade's."""
    return max(UPGRADES, default=Version(0, 0, 0))


def pending(found: Version) -> list[Version]:
    """The versions of the upgrades that a lab.db of version found lacks, lowest first."""
    return sorted(to for to in UPGRADES if to > found)


def from_stored(number: int) -> Version:
    """The version that PRAGMA user_version holds as number."""
    return Version(number // 1000000, number // 1000 % 1000, number % 1000)


def lab_version(db: sqlite3.Connection) -> Version:
    """The version of the lab.db open as db."""
    return from_stored(int(db.execute("PRAGMA user_version").fetchone()[0]))


def check_version(data: Path, found: Version) -> None:
    """Refuses the data folder's lab.db, of version found, if newer than latest()."""
    if found > latest():
        raise ValueError(f"{database(data)} has the tables of experiment-lab {found}, newer than those of this"
                         f" one, {latest()}: update experiment-lab (README, Updating)")


def backup(data: Path, found: Version) -> Path:
    """Copies the data folder's lab.db, of version found, to lab.db.v<found>
    there, through connections of its own, which read lab.db while the
    upgrade's connection holds the write lock."""
    copy = data / f"lab.db.v{found}"
    with (contextlib.closing(sqlite3.connect(database(data))) as source,
          contextlib.closing(sqlite3.connect(copy)) as target):
        source.backup(target)
    return copy


def upgrade(db: sqlite3.Connection, data: Path) -> None:
    """Brings the data folder's lab.db, open as db, to latest(). A new one gets
    the tables of schema.sql. An older one is copied (backup), then the
    upgrades it lacks run, lowest first, in one transaction, stderr naming the
    version of each and, after the commit, where the copy is. A newer one is
    refused."""
    last = latest()
    if lab_version(db) == last:
        return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone():
        # each statement makes only what is missing, so two processes may make lab.db at once
        db.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA.read_text()}\nPRAGMA user_version = {last.stored()};\nCOMMIT;")
        return
    db.execute("BEGIN IMMEDIATE")
    with db:  # commits, or rolls back on an exception
        found = lab_version(db)  # again, now that no other process writes
        check_version(data, found)
        versions = pending(found)
        if not versions:  # another process upgraded it
            return
        copy = backup(data, found)
        for n, to in enumerate(versions, 1):
            print(f"upgrading lab.db to version {to} ({n} of {len(versions)})", file=sys.stderr)
            UPGRADES[to](db, data)
        db.execute(f"PRAGMA user_version = {last.stored()}")
    print(f"upgraded lab.db from version {found} to {last}; its copy from before is {copy}", file=sys.stderr)


def upgrade_in_child(data: Path) -> bool:
    """Makes or upgrades the data folder's lab.db in a child process
    (python -m experiment_lab.lab), which prints why it failed. True if it
    succeeded."""
    return not subprocess.run([sys.executable, "-m", "experiment_lab.lab"],
                              env=dict(os.environ, EXPERIMENT_LAB_DATA=str(data)), check=False).returncode


def upgrade_attached(con: "DuckDBPyConnection", data: Path) -> bool:
    """For the DuckDB database con, with the data folder's lab.db attached as
    the catalog lab: refuses a newer lab.db, and upgrades an older one in a
    child process. True if it upgraded, after which con's catalog lab still
    lists the tables from before."""
    ((number,),) = con.execute("FROM sqlite_query('lab', 'PRAGMA user_version')").fetchall()
    found = from_stored(int(number))
    check_version(data, found)
    if found == latest():
        return False
    if not upgrade_in_child(data):
        raise ValueError(f"could not upgrade {database(data)}")
    return True


# The upgrades, in any order.

@version("0.3.0")
def _(db: sqlite3.Connection, data: Path) -> None:
    """The table files, with a row for each file in files/<run id>/ of a run.
    A lab.db of version 0.0.0 may have it already."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'files'").fetchone():
        return
    db.execute("CREATE TABLE files (run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,"
               " name TEXT NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY (run, name))")
    ids = {rid for (rid,) in db.execute("SELECT id FROM runs")}
    db.executemany("INSERT INTO files (run, name, bytes) VALUES (?, ?, ?)",
                   [(d.name, p.relative_to(d).as_posix(), p.stat().st_size)
                    for d in (data / "files").glob("*") if d.name in ids for p in d.rglob("*") if p.is_file()])
