"""Runs SQL over the views of the lab's database and prints the result as a
compact table.

usage: q [--project P] [--max N] [--full] "SQL"   (SQL from stdin when omitted)
       q [--project P] .views                     (each view and its columns)

The database is lab.db in the data folder ($EXPERIMENT_LAB_DATA, else
~/lab-data), attached read-only as the catalog lab with the tables
lab.experiments, lab.runs, lab.params, lab.tags, lab.metrics (every value
logged) and lab.records. The views are those of experiment_lab/lab.sql, then
those of the view files of the project of the current folder (the one whose
folder holds it or its git repository's main worktree), or of --project; a
statement of a view file that fails is printed on stderr and skipped. lab.sql's
views have every project's rows; their column project tells them apart.
Several statements may be given; the last one's rows are printed, at most
--max of them (40), with cells cut at 80 characters unless --full. A result of
one row and one column prints that cell alone, whole and with its line breaks,
as in q "SELECT text FROM records WHERE id = '...'".
"""
import argparse
import sys

import duckdb
from duckdb import DuckDBPyConnection

from experiment_lab import query
from experiment_lab.lab import holding
from experiment_lab.settings import data_folder


def show(t: query.Table, cut: int | None) -> None:
    """The table with its cells cut at cut characters (None: whole), each on one line."""
    rows = [[v.replace("\n", " ") for v in query.shorten(r, cut)] for r in t["rows"]]
    widths = [max([len(n)] + [len(r[i]) for r in rows]) for i, n in enumerate(t["columns"])]

    def fmt(values: list[str]) -> str:
        return " | ".join(v.rjust(w) if num else v.ljust(w)
                          for v, w, num in zip(values, widths, t["numeric"])).rstrip()

    print(fmt(t["columns"]))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))
    print(f"({len(rows)} rows{', more not shown (--max N shows more; without ORDER BY, other rows may come first)'
                              if t['more'] else ''})")


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--project")
    p.add_argument("--max", type=int, default=40)
    p.add_argument("--full", action="store_true")
    p.add_argument("sql", nargs="?")
    a = p.parse_args()
    if a.max < 1:
        p.error("--max must be 1 or more")
    return a


def make_views(con: DuckDBPyConnection, project: str | None) -> None:
    """Makes the views of lab.sql and of the view files of project, else of the
    project of the current folder; prints the errors of the view files on stderr."""
    projects = query.projects(con)
    if project and project not in [p.name for p in projects]:
        raise ValueError(f"no project {project}")
    for error in query.errors(query.make_views(con, project or holding(projects))):
        print(error, file=sys.stderr)


def answer(con: DuckDBPyConnection, sql: str, limit: int, cut: int | None) -> None:
    if sql.strip() == ".views":
        for name, columns in query.view_columns(con):
            print(f"{name}: {columns}")
        return
    result = query.run(con, sql)
    if result is None:
        print("ok")
        return
    t = query.table(result, limit)
    if len(t["columns"]) == 1 and len(t["rows"]) == 1 and not t["more"]:
        print(t["rows"][0][0], end="" if t["rows"][0][0].endswith("\n") else "\n")
    else:
        show(t, cut)


def main() -> None:
    a = arguments()
    sql = a.sql if a.sql is not None else sys.stdin.read()
    try:
        con = query.attach(data_folder())
        make_views(con, a.project)
        answer(con, sql, a.max, None if a.full else 80)
    except (duckdb.Error, FileNotFoundError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
