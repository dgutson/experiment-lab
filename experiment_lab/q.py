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
--max of them (40), with cells cut at 80 characters unless --full.
"""
import argparse
import sys

import duckdb

from experiment_lab import query
from experiment_lab.lab import holding
from experiment_lab.settings import data_folder


def show(t):
    rows = [[v.replace("\n", " ") for v in r] for r in t["rows"]]
    widths = [max([len(n)] + [len(r[i]) for r in rows]) for i, n in enumerate(t["columns"])]

    def fmt(values):
        return " | ".join(v.rjust(w) if num else v.ljust(w)
                          for v, w, num in zip(values, widths, t["numeric"])).rstrip()

    print(fmt(t["columns"]))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))
    print(f"({len(rows)} rows{', more not shown (--max N shows more)' if t['more'] else ''})")


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--project")
    p.add_argument("--max", type=int, default=40)
    p.add_argument("--full", action="store_true")
    p.add_argument("sql", nargs="?")
    a = p.parse_args()
    sql = a.sql if a.sql is not None else sys.stdin.read()
    try:
        con = query.attach(data_folder())
        projects = query.projects(con)
        if a.project and a.project not in [name for name, _ in projects]:
            sys.exit(f"error: no project {a.project}")
        for error in query.errors(query.make_views(con, a.project or holding(projects))):
            print(error, file=sys.stderr)
        if sql.strip() == ".views":
            for name, columns in query.view_columns(con):
                print(f"{name}: {columns}")
            return
        result = query.run(con, sql)
        if result is None:
            print("ok")
            return
        show(query.table(result, a.max, None if a.full else 80))
    except (duckdb.Error, FileNotFoundError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
