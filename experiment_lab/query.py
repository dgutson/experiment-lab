"""DuckDB SQL over the lab's database, for q and the pages: lab.db attached
read-only as the catalog lab, then the views of lab.sql and of the view files
in the data folder's lab.toml.

DuckDB reads lab.db through its own copy of SQLite, and two copies of SQLite in
one process do not see each other's locks
(https://www.sqlite.org/howtocorrupt.html, section 2.2.1). So a process must
not have lab.db open with Python's sqlite3 while DuckDB reads it: q and the
pages never open it with sqlite3, and the writers (lab.py) never use DuckDB.
"""
import json
import sys
from pathlib import Path

import duckdb

from experiment_lab.settings import database, settings

LAB_SQL = Path(__file__).with_name("lab.sql")


def connect(data, keep_going=True):
    """A new in-memory DuckDB database with the views. With keep_going, a
    statement of a view file that fails is reported on stderr and the others
    still run."""
    path = database(data)
    if not path.exists():
        raise FileNotFoundError(f"no database {path}; rec experiment or a logging script makes it")
    con = duckdb.connect()
    con.execute(f"ATTACH '{str(path).replace("'", "''")}' AS lab (TYPE sqlite, READ_ONLY)")
    run_files(con, view_files(data), keep_going)
    return con


def view_files(data):
    """lab.sql, then the view files that the data folder's lab.toml lists."""
    return [LAB_SQL, *settings(data)[1]]


def run_files(con, paths, keep_going=True):
    for path in paths:
        for statement in con.extract_statements(path.read_text()):
            try:
                con.execute(statement)
            except duckdb.Error as e:
                if not keep_going:
                    raise
                print(f"{path.name}: {str(e).splitlines()[0]}", file=sys.stderr)


def run(con, sql):
    """Runs the statements of sql; the last one's result, None if it returns no rows."""
    statements = con.extract_statements(sql)
    if not statements:
        raise ValueError("no statement")
    for statement in statements[:-1]:
        con.execute(statement)
    result = con.execute(statements[-1])
    return result if result.description else None


def view_columns(con):
    """Each view, with its columns joined by ', '."""
    return con.execute("""SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position)
        FROM information_schema.columns WHERE table_catalog = current_database() AND table_schema = 'main'
        GROUP BY table_name ORDER BY table_name""").fetchall()


def cell(v):
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


def table(result, limit, cut=None):
    """The result's column names, its first limit rows as text with cells cut
    at cut characters (None: whole), whether each column is numeric, whether
    rows were left out, and whether cells were cut."""
    columns = [d[0] for d in result.description]
    rows = result.fetchmany(limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    numeric = [bool(rows) and all(isinstance(r[i], (int, float)) and not isinstance(r[i], bool)
                                  for r in rows if r[i] is not None) for i in range(len(columns))]
    text = [[cell(v) for v in r] for r in rows]
    if cut is None or all(len(v) <= cut for r in text for v in r):
        cut = None
    else:
        text = [[v if len(v) <= cut else v[:cut - 1] + "…" for v in r] for r in text]
    return {"columns": columns, "rows": text, "numeric": numeric, "more": more, "limit": limit, "cut": cut}
