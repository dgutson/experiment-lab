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
from pathlib import Path

import duckdb

from experiment_lab.settings import database

LAB_SQL = Path(__file__).with_name("lab.sql")


def connect(data, project=None):
    """A new in-memory DuckDB database with lab.db attached and the views of
    lab.sql and of the project's view files; returns it and make_views' sources."""
    con = attach(data)
    return con, make_views(con, project)


def attach(data):
    """A new in-memory DuckDB database with lab.db attached as the catalog lab."""
    path = database(data)
    if not path.exists():
        raise FileNotFoundError(f"no database {path}; rec project or a logging script makes it")
    con = duckdb.connect()
    con.execute(f"ATTACH '{str(path).replace("'", "''")}' AS lab (TYPE sqlite, READ_ONLY)")
    return con


def projects(con):
    """Each project's name and folder."""
    return con.execute("SELECT name, folder FROM lab.experiments WHERE parent IS NULL ORDER BY name").fetchall()


def version(con, project):
    """What the views are made from: lab.sql and the project's view files, each
    with its modification time (0 if missing)."""
    found = con.execute("SELECT views FROM lab.experiments WHERE name = ? AND parent IS NULL", [project]).fetchone()
    paths = [LAB_SQL, *(Path(path) for path in (found[0] if found else "").split("; ") if path)]
    return [(path, path.stat().st_mtime_ns if path.exists() else 0) for path in paths]


def make_views(con, project):
    """Runs lab.sql, then the project's view files. A statement that fails is
    skipped and the others still run. Returns the sources: per file, its path,
    modification time (read before the file) and the errors of its statements."""
    return [{"path": path, "mtime": mtime, "errors": run_file(con, path)} for path, mtime in version(con, project)]


def run_file(con, path):
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


def errors(sources):
    """The errors of the sources, each as "file: error"."""
    return [f"{s['path'].name}: {e}" for s in sources for e in s["errors"]]


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
