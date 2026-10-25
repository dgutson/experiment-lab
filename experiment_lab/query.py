"""DuckDB SQL over the lab's database, for q and the pages: lab.db attached
read-only as the catalog lab, then the views of lab.sql and of one project's
view files, so that projects may use the same view names.

DuckDB reads lab.db through its own copy of SQLite, and two copies of SQLite in
one process do not see each other's locks
(https://www.sqlite.org/howtocorrupt.html, section 2.2.1). So a process must
not have lab.db open with Python's sqlite3 while DuckDB reads it: q and the
pages never open it with sqlite3, and the writers (lab.py) never use DuckDB.
"""
import json
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple, TypedDict

import duckdb
from duckdb import DuckDBPyConnection

from experiment_lab.lab import Project
from experiment_lab.settings import database

LAB_SQL = Path(__file__).with_name("lab.sql")

type Row = list[str]


class Stamp(NamedTuple):
    """A file the views are made from, and its modification time (0 if missing)."""
    path: Path
    mtime: int


class Source(TypedDict):
    """A file the views were made from: its path, its modification time, read
    before the file, and the errors of its statements."""
    path: Path
    mtime: int
    errors: list[str]


class Database(NamedTuple):
    con: DuckDBPyConnection
    sources: list[Source]


class View(NamedTuple):
    name: str
    columns: str  # joined by ', '


class Table(TypedDict):
    """A result's column names, its first limit rows as text with cells cut at
    cut characters (None: whole), whether each column is numeric, and whether
    rows were left out."""
    columns: list[str]
    rows: list[Row]
    numeric: list[bool]
    more: bool
    limit: int
    cut: int | None


def connect(data: Path, project: str | None = None) -> Database:
    """A new in-memory DuckDB database with lab.db attached and the views of
    lab.sql and of the project's view files, and make_views' sources."""
    con = attach(data)
    return Database(con, make_views(con, project))


def literal(text: str) -> str:
    """text as an SQL string."""
    return "'" + text.replace("'", "''") + "'"


def identifier(name: str) -> str:
    """name as a quoted SQL name, of a view for example."""
    return '"' + name.replace('"', '""') + '"'


def attach(data: Path) -> DuckDBPyConnection:
    """A new in-memory DuckDB database with lab.db attached as the catalog lab."""
    path = database(data)
    if not path.exists():
        raise FileNotFoundError(f"no database {path}; rec project or a logging script makes it")
    con = duckdb.connect()
    con.execute(f"ATTACH {literal(str(path))} AS lab (TYPE sqlite, READ_ONLY)")
    return con


def projects(con: DuckDBPyConnection) -> list[Project]:
    return [Project(*row) for row in con.execute("SELECT name, folder FROM lab.experiments WHERE parent IS NULL"
                                                 " ORDER BY name").fetchall()]


def version(con: DuckDBPyConnection, project: str | None) -> list[Stamp]:
    """What the views are made from: lab.sql and the project's view files."""
    found = con.execute("SELECT views FROM lab.experiments WHERE name = ? AND parent IS NULL", [project]).fetchone()
    paths = [LAB_SQL, *(Path(path) for path in (found[0] if found else "").split("; ") if path)]
    return [Stamp(path, path.stat().st_mtime_ns if path.exists() else 0) for path in paths]


def make_views(con: DuckDBPyConnection, project: str | None) -> list[Source]:
    """Runs lab.sql, then the project's view files. A statement that fails is
    skipped and the others still run."""
    return [Source(path=path, mtime=mtime, errors=run_file(con, path)) for path, mtime in version(con, project)]


def run_file(con: DuckDBPyConnection, path: Path) -> list[str]:
    """Runs the file's statements; returns the errors of those that failed."""
    try:
        statements = con.extract_statements(path.read_text())
    except (OSError, duckdb.Error) as e:
        return [str(e).splitlines()[0]]
    errors = []
    for statement in statements:
        try:
            con.execute(statement)
        except duckdb.Error as e:
            errors.append(str(e).splitlines()[0])
    return errors


def errors(sources: Iterable[Source]) -> list[str]:
    """The errors of the sources, each as "file: error"."""
    return [f"{s['path'].name}: {e}" for s in sources for e in s["errors"]]


def run(con: DuckDBPyConnection, sql: str) -> DuckDBPyConnection | None:
    """Runs the statements of sql; the last one's result, None if it returns no rows."""
    statements = con.extract_statements(sql)
    if not statements:
        raise ValueError("no statement")
    for statement in statements[:-1]:
        con.execute(statement)
    result = con.execute(statements[-1])
    return result if result.description else None


def view_columns(con: DuckDBPyConnection) -> list[View]:
    return [View(*row) for row in con.execute("""SELECT table_name,
            string_agg(column_name, ', ' ORDER BY ordinal_position)
        FROM information_schema.columns WHERE table_catalog = current_database() AND table_schema = 'main'
        GROUP BY table_name ORDER BY table_name""").fetchall()]


def cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) >= 0.001 else f"{v:.3g}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, default=str, ensure_ascii=False)
    return str(v)


def is_numeric(values: Iterable[object]) -> bool:
    """Whether every value that is not null is a number."""
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values if v is not None)


def longest(rows: Iterable[Row]) -> int:
    return max((len(v) for r in rows for v in r), default=0)


def shorten(row: Row, cut: int | None) -> Row:
    """The row's cells cut at cut characters (None: whole)."""
    return [v if cut is None or len(v) <= cut else v[:cut - 1] + "…" for v in row]


def table(result: DuckDBPyConnection, limit: int, cut: int | None = None) -> Table:
    """The result as a Table: a column is numeric when every value in it that is
    not null is a number; cut is None when no cell is longer."""
    columns = [d[0] for d in result.description]
    rows = result.fetchmany(limit + 1)
    text = [[cell(v) for v in r] for r in rows[:limit]]
    if cut is not None and longest(text) <= cut:
        cut = None
    return Table(columns=columns, rows=[shorten(r, cut) for r in text],
                 numeric=[is_numeric(values) for values in zip(*rows[:limit])] or [False] * len(columns),
                 more=len(rows) > limit, limit=limit, cut=cut)
