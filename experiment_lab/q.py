"""Runs SQL over the views of the lab's database and prints the result as a
compact table.

usage: q [--project P | --all] [--max N] [--full] "SQL"   (SQL from stdin when omitted)
       q [--project P | --all] .views                     (each view and its columns)
       q [--project P | --all] [--max N] .keys            (the keys of the runs)

The database is lab.db in the data folder ($EXPERIMENT_LAB_DATA, else
~/lab-data), attached read-only as the catalog lab with the tables
lab.experiments, lab.runs, lab.params, lab.tags, lab.metrics (every value
logged), lab.files (the files logged for runs) and lab.records. The views are those of experiment_lab/lab.sql, then
those of the view files of the project of the current folder (the one whose
folder holds it or the folder at the same place in its git repository's main
worktree), or of --project; a
statement of a view file that fails is printed on stderr and skipped. lab.sql's
views hold that project's rows only; with --all, they hold every project's and
no view file runs. From a folder no project holds, q needs --project or --all.
Several statements may be given; the last one's rows are printed, at most
--max of them (40), with cells cut at 80 characters unless --full. A result of
one row and one column prints that cell alone, whole and with its line breaks,
as in q "SELECT text FROM records WHERE id = '...'". An error about a column
that is not found names the views that have it, and earlier statements of
type SELECT, whose rows are not printed, are named on stderr. .keys lists each key of the
tags, params and metrics of the runs in the view runs, with the number of runs
that have it.
"""
import argparse
import re
import sys

import duckdb
from duckdb import DuckDBPyConnection

from experiment_lab import query
from experiment_lab.lab import working_project
from experiment_lab.settings import data_folder

KEYS = """WITH own AS (SELECT r.id FROM lab.runs r JOIN experiments x ON x.name = r.experiment)
SELECT 'tags' AS "table", key, count(*) AS runs FROM lab.tags WHERE run IN (FROM own) GROUP BY key
UNION ALL SELECT 'params', key, count(*) FROM lab.params WHERE run IN (FROM own) GROUP BY key
UNION ALL SELECT 'metrics', key, count(DISTINCT run) FROM lab.metrics WHERE run IN (FROM own) GROUP BY key
ORDER BY "table" DESC, key"""


def show(t: query.Table, cut: int | None, ordered: bool) -> None:
    """The table with its cells cut at cut characters (None: whole), each on one
    line; ordered: the query's ORDER BY gives every row its place."""
    rows = [[v.replace("\n", " ") for v in query.shorten(r, cut)] for r in t["rows"]]
    widths = [max([len(n)] + [len(r[i]) for r in rows]) for i, n in enumerate(t["columns"])]

    def fmt(values: list[str]) -> str:
        return " | ".join(v.rjust(w) if num else v.ljust(w)
                          for v, w, num in zip(values, widths, t["numeric"])).rstrip()

    print(fmt(t["columns"]))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))
    unordered = "" if ordered else "; without ORDER BY, other rows may come first"
    print(f"({len(rows)} rows{f', more not shown (--max N shows more{unordered})' if t['more'] else ''})")


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = p.add_mutually_exclusive_group()
    which.add_argument("--project")
    which.add_argument("--all", action="store_true")
    p.add_argument("--max", type=int, default=40)
    p.add_argument("--full", action="store_true")
    p.add_argument("sql", nargs="?")
    a = p.parse_args()
    if a.max < 1:
        p.error("--max must be 1 or more")
    return a


def project_of(con: DuckDBPyConnection, a: argparse.Namespace) -> str | None:
    """The project whose rows the views hold: --project, else the project of
    the current folder; None, every project, with --all."""
    if a.all:
        return None
    project = working_project(query.projects(con), a.project)
    if project is None:
        raise ValueError("no project holds this folder; --project P names one, --all reads every project")
    return project


def views_with(con: DuckDBPyConnection, error: duckdb.BinderException) -> str:
    """For an error about a column that is not found, a line naming the views
    that have that column; '' if none has it."""
    found = re.search(r'(?:Referenced column|column named) "([^"]+)"', str(error))
    if not found:
        return ""
    names = [v.name for v in query.view_columns(con) if found[1] in v.columns.split(", ")]
    if not names:
        return ""
    return f"\ncolumn {found[1]} is in the view{'s' if len(names) > 1 else ''} {', '.join(names)}"


def sql_result(con: DuckDBPyConnection, sql: str) -> DuckDBPyConnection | None:
    """The result of sql's last statement, None if it returns no rows; prints
    on stderr which earlier statements' rows are left out."""
    try:
        last = query.run(con, sql)
    except duckdb.BinderException as e:
        raise ValueError(f"{e}{views_with(con, e)}") from e
    if last.hidden_note:
        print(last.hidden_note, file=sys.stderr)
    return last.result


def answer(con: DuckDBPyConnection, sql: str, limit: int, cut: int | None) -> None:
    if sql.strip() == ".views":
        for name, columns in query.view_columns(con):
            print(f"{name}: {columns}")
        return
    asks_keys = sql.strip() == ".keys"
    result = con.execute(KEYS) if asks_keys else sql_result(con, sql)
    if result is None:
        print("ok")
        return
    t = query.table(result, limit)
    if len(t["columns"]) == 1 and len(t["rows"]) == 1 and not t["more"]:
        print(t["rows"][0][0], end="" if t["rows"][0][0].endswith("\n") else "\n")
    else:
        show(t, cut, ordered=asks_keys)


def main() -> None:
    a = arguments()
    sql = a.sql if a.sql is not None else sys.stdin.read()
    try:
        con = query.attach(data_folder())
        for error in query.errors(query.make_views(con, project_of(con, a))):
            print(error, file=sys.stderr)
        answer(con, sql, a.max, None if a.full else 80)
    except (duckdb.Error, FileNotFoundError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
