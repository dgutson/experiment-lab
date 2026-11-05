"""DuckDB SQL over the lab's database, for q and the pages: lab.db attached
read-only as the catalog lab, then the views of lab.sql, which hold one
project's rows, and of that project's view files, so that projects may use the
same view names. With no project, lab.sql's views hold every project's rows.

DuckDB reads lab.db through its own copy of SQLite, and two copies of SQLite in
one process do not see each other's locks
(https://www.sqlite.org/howtocorrupt.html, section 2.2.1). So a process must
not have lab.db open with Python's sqlite3 while DuckDB reads it: q and the
pages never open it with sqlite3, lab.py never uses DuckDB, and rec closes its
sqlite3 connection (Lab.closed) while it runs a record's sql: evidence.
"""
import contextlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict

import duckdb
from duckdb import DuckDBPyConnection

from experiment_lab.db_upgrades import upgrade_attached
from experiment_lab.lab import Project
from experiment_lab.settings import database

LAB_SQL = Path(__file__).with_name("lab.sql")
# the statements that would act outside the transaction that trial rolls back
REFUSED = (duckdb.StatementType.TRANSACTION, duckdb.StatementType.ATTACH, duckdb.StatementType.DETACH)
PRINTED = (duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN)  # q prints their rows, also before the last

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


class LastResult(NamedTuple):
    """The last statement's result, None if it returns no rows, and the
    sentence naming the earlier SELECT statements, whose rows it leaves out
    ('' if none)."""
    result: DuckDBPyConnection | None
    hidden_note: str

    def rows(self) -> DuckDBPyConnection:
        """result; a ValueError if the statement returns no rows."""
        if self.result is None:
            raise ValueError("the statement returns no rows")
        return self.result


class Printed(NamedTuple):
    """A statement's result that q prints: its number among the statements,
    from 1, and whether q prints another statement's result too."""
    number: int
    result: DuckDBPyConnection
    several: bool


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


def attached(path: Path) -> DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"ATTACH {literal(str(path))} AS lab (TYPE sqlite, READ_ONLY)")
    return con


def attach(data: Path) -> DuckDBPyConnection:
    """A new in-memory DuckDB database with lab.db attached as the catalog lab,
    and the macro data_folder(), the data folder's absolute path. An older
    lab.db is upgraded first, a newer one refused (upgrade_attached)."""
    path = database(data)
    if not path.exists():
        raise FileNotFoundError(f"no database {path}; rec project or a logging script makes it")
    con = attached(path)
    if upgrade_attached(con, data):
        con.close()
        con = attached(path)
    con.execute(f"CREATE MACRO data_folder() AS {literal(str(data.resolve()))}")
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
    """Makes the macro current_project(), the project whose rows lab.sql's views
    hold (NULL: every project's), and runs lab.sql, then the project's view
    files. A statement that fails is skipped and the others still run."""
    con.execute(f"CREATE MACRO current_project() AS {literal(project) if project else 'NULL::VARCHAR'}")
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


def run(con: DuckDBPyConnection, sql: str) -> LastResult:
    """Runs the statements of sql. A SET returns rows too, so only an earlier
    statement of type SELECT counts as one whose rows are left out."""
    statements = con.extract_statements(sql)
    if not statements:
        raise ValueError("no statement")
    for statement in statements[:-1]:
        con.execute(statement)
    result = con.execute(statements[-1])
    hidden = [str(i) for i, s in enumerate(statements[:-1], 1) if s.type == duckdb.StatementType.SELECT]
    note = (f"The rows of statement{'s' if len(hidden) > 1 else ''} {', '.join(hidden)} are not shown;"
            " only the last statement's are.") if hidden else ""
    return LastResult(result if result.description else None, note)


def run_each(con: DuckDBPyConnection, sql: str) -> Iterator[Printed]:
    """Runs the statements of sql, giving the result of each SELECT or EXPLAIN
    and of the last statement, each before the next statement runs, which ends
    it. A SET or CREATE returns rows too, so only those types count before the
    last; a COPY's, the count of rows it wrote, never."""
    statements = con.extract_statements(sql)
    if not statements:
        raise ValueError("no statement")
    printed = [i for i, s in enumerate(statements, 1)
               if s.type in PRINTED or (i == len(statements) and s.type != duckdb.StatementType.COPY)]
    for i, statement in enumerate(statements, 1):
        result = con.execute(statement)
        if i in printed and result.description:
            yield Printed(i, result, len(printed) > 1)


def lock(con: DuckDBPyConnection) -> None:
    """Turns off con's access to files and locks its configuration, for SQL
    that a page or a record gives, so that rec judges a sql: item as the
    record page runs it."""
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")


@contextlib.contextmanager
def trial(con: DuckDBPyConnection, sql: str) -> Iterator[LastResult]:
    """Runs sql (run) in a transaction that is rolled back after the block,
    refusing a statement of REFUSED, as the pages run SQL from the query box
    and sql: evidence and rec checks a sql: item."""
    if any(s.type in REFUSED for s in con.extract_statements(sql)):
        raise ValueError("BEGIN, COMMIT, ROLLBACK, ATTACH and DETACH are not run here")
    con.begin()
    try:
        yield run(con, sql)
    finally:
        con.rollback()


def evidence_runs(con: DuckDBPyConnection, sql: str) -> list[str]:
    """The run ids in the column run of the rows that the sql: evidence item
    sql returns, run as trial runs it, each once; none without that column. A
    ValueError naming the item if it fails."""
    try:
        with trial(con, sql) as last:
            rows = last.rows()
            names = [d[0] for d in rows.description]
            if "run" not in names:
                return []
            column = names.index("run")
            return list(dict.fromkeys(str(row[column]) for row in rows.fetchall()))
    except (duckdb.Error, ValueError) as e:
        raise ValueError(f"evidence 'sql:{sql}': {e}") from e


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
