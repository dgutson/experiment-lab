# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.6"]
# ///
"""Runs SQL over the views of MLflow's database and prints the result as a
compact table.

usage: q [--max N] [--full] "SQL"   (SQL from stdin when omitted)
       q .views                     (each view and its columns)

The database is mlflow.db in the data folder ($MLFLOW_LAB_DATA, else ~/mlflow),
attached read-only as ml. MLflow's tables are views over it in the schema
mlflow, and the views are those of mlflow_lab/lab.sql, then those of the files
listed under views in the data folder's lab.toml. Several statements may be
given; the last one's rows are printed, at most --max of them (40), with cells
cut at 80 characters unless --full.
"""
import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

import duckdb

DATA = Path(os.environ.get("MLFLOW_LAB_DATA", "~/mlflow")).expanduser()
LAB_SQL = Path(__file__).resolve().parents[1] / "mlflow_lab" / "lab.sql"
# The MLflow tables lab.sql reads; mlflow_lab/store.py copies the same ones.
MLFLOW_TABLES = ("experiments", "experiment_tags", "runs", "tags", "params", "latest_metrics")


def settings(data):
    """The data folder's lab.toml: the folder /lab/file shows (None without
    one) and the view files run after lab.sql. mlflow_lab/__init__.py reads it
    the same way."""
    path = data / "lab.toml"
    found = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(found) - {"files", "views"}
    if unknown:
        raise ValueError(f"{path}: unknown settings {', '.join(sorted(unknown))}")

    def resolve(p):
        return (data / Path(p).expanduser()).resolve()

    files = resolve(found["files"]) if "files" in found else None
    return files, [resolve(p) for p in found.get("views", [])]


def connect(kind="VIEW", keep_going=True):
    """MLflow's tables as views over the attached file (kind VIEW) or as tables
    copied from it (TABLE), and the views on them. With keep_going, a view that
    fails is reported and the others are still made."""
    database = DATA / "mlflow.db"
    if not database.exists():
        sys.exit(f"error: no MLflow database {database}; start the server with start.sh")
    con = duckdb.connect()
    con.execute(f"ATTACH '{database}' AS ml (TYPE sqlite, READ_ONLY)")
    con.execute("CREATE SCHEMA mlflow")
    for table in MLFLOW_TABLES:
        con.execute(f"CREATE {kind} mlflow.{table} AS FROM ml.{table}")
    for path in [LAB_SQL, *settings(DATA)[1]]:
        for statement in con.extract_statements(path.read_text()):
            try:
                con.execute(statement)
            except duckdb.Error as e:  # a missing batch must not hide the other views
                if not keep_going:
                    raise
                print(f"{path.name}: {str(e).splitlines()[0]}", file=sys.stderr)
    return con


def cell(v, full):
    if v is None:
        s = ""
    elif isinstance(v, bool):
        s = str(v).lower()
    elif isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            s = str(int(v))
        elif abs(v) >= 0.001:
            s = f"{v:.4f}".rstrip("0").rstrip(".")
        else:
            s = f"{v:.3g}"
    elif isinstance(v, (list, dict)):
        s = json.dumps(v, default=str, ensure_ascii=False)
    else:
        s = str(v)
    s = s.replace("\n", " ")
    return s if full or len(s) <= 80 else s[:79] + "…"


def show(names, rows, limit, full):
    shown = [[cell(v, full) for v in row] for row in rows[:limit]]
    widths = [max([len(n)] + [len(r[i]) for r in shown]) for i, n in enumerate(names)]
    numeric = [bool(rows) and all(isinstance(r[i], (int, float)) and not isinstance(r[i], bool)
                                  for r in rows[:limit] if r[i] is not None) for i in range(len(names))]

    def fmt(values):
        return " | ".join(v.rjust(w) if num else v.ljust(w) for v, w, num in zip(values, widths, numeric)).rstrip()

    print(fmt(names))
    print("-+-".join("-" * w for w in widths))
    for r in shown:
        print(fmt(r))
    more = len(rows) - len(shown)
    print(f"({len(rows)} rows{f', {more} not shown' if more else ''})")


def views(con):
    rows = con.execute("""SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position)
                          FROM information_schema.columns
                          WHERE table_catalog = current_database() AND table_schema = 'main'
                          GROUP BY table_name ORDER BY table_name""").fetchall()
    for name, columns in rows:
        print(f"{name}: {columns}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--max", type=int, default=40)
    p.add_argument("--full", action="store_true")
    p.add_argument("sql", nargs="?")
    a = p.parse_args()
    sql = a.sql if a.sql is not None else sys.stdin.read()
    con = connect()
    if sql.strip() == ".views":
        views(con)
        return
    statements = con.extract_statements(sql)
    for statement in statements[:-1]:
        con.execute(statement)
    result = con.execute(statements[-1])
    if result.description is None:
        print("ok")
        return
    show([d[0] for d in result.description], result.fetchall(), a.max, a.full)


if __name__ == "__main__":
    try:
        main()
    except duckdb.Error as e:
        sys.exit(f"error: {e}")
