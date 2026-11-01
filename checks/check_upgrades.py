"""Checks that the upgrades of experiment_lab/db_upgrades.py make the tables of
schema.sql: a lab.db made from schema.sql, and lab.db files of version 0.0.0
(schema-0.0.0.sql, with and without the table files) brought to latest() by
the upgrades, must have the same tables and indexes. Their SQL is compared
without comments and with the spaces made uniform, so a column, an index, a
constraint or a CHECK that differs is reported. Also checks that latest() is
not above the version in pyproject.toml.

usage: uv run python checks/check_upgrades.py
"""
import contextlib
import difflib
import io
import re
import sqlite3
import sys
import tempfile
import tomllib
from pathlib import Path

from experiment_lab import db_upgrades
from experiment_lab.settings import database

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
TABLES_0_0_0 = Path(__file__).with_name("schema-0.0.0.sql")

type Schema = list[str]  # a lab.db's tables and indexes, a line for each column and constraint


def uniform(sql: str) -> str:
    """sql without comments, without spaces around parentheses and commas, and
    with every other run of spaces one space."""
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"\s*([(),])\s*", r"\1", re.sub(r"\s+", " ", sql)).strip()


def parts(sql: str) -> list[str]:
    """sql split at the commas inside its first parentheses and no others, and
    before the parenthesis that closes them: a table's columns and constraints
    one by one."""
    found, start, depth = [], 0, 0
    for i, char in enumerate(sql):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if (char, depth) in ((",", 1), (")", 0)):
            found.append(sql[start:i])
            start = i + (char == ",")
    return [*found, sql[start:]]


def schema(db: sqlite3.Connection) -> Schema:
    """db's tables, a line for each column and constraint, and its indexes, a
    line for each."""
    return [f"{kind} {name}: {part}"
            for kind, name, sql in db.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
            for part in (parts(uniform(sql)) if kind == "table" else [uniform(sql or "")])]


def upgraded(folder: Path, tables: str) -> Schema:
    """The schema of a new lab.db in folder made from the SQL tables, if any,
    then brought to latest() by db_upgrades.upgrade, whose lines on stderr are
    left out."""
    folder.mkdir()
    db = sqlite3.connect(database(folder), isolation_level=None)
    db.executescript(tables)
    with contextlib.redirect_stderr(io.StringIO()):
        db_upgrades.upgrade(db, folder)
    found = schema(db)
    db.close()
    return found


def differences(made: Schema, name: str, tables: str, temporary: Path) -> list[str]:
    """How the schema of a lab.db of 0.0.0 in the form name, made from tables
    and upgraded, differs from made, the schema of a new lab.db."""
    return list(difflib.unified_diff(made, upgraded(temporary / name, tables), "a new lab.db",
                                     f"a lab.db of 0.0.0 {name}, upgraded", lineterm=""))


def main() -> None:
    tables = TABLES_0_0_0.read_text()
    with tempfile.TemporaryDirectory() as temporary:
        made = upgraded(Path(temporary, "new"), "")
        found = (differences(made, "with files", tables, Path(temporary))
                 + differences(made, "without files", tables + "\nDROP TABLE files;", Path(temporary)))
    released = db_upgrades.parsed(tomllib.loads(PYPROJECT.read_text())["project"]["version"])
    if db_upgrades.latest() > released:
        found.append(f"the upgrade for {db_upgrades.latest()} is above pyproject.toml's version, {released}")
    if found:
        sys.exit("\n".join(found))
    print(f"schema.sql's tables are those that the upgrades make from 0.0.0, to {db_upgrades.latest()}")


if __name__ == "__main__":
    main()
