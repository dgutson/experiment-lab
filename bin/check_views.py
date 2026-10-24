# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.6"]
# ///
"""Compares every view of every project on the data folder's lab.db read two
ways: with lab.db attached, as q and the pages read it, and with its tables
copied into DuckDB first. Prints each view's row count and a checksum of its
sorted rows; the two ways must agree. Before raising the DuckDB pin in pyproject.toml, set the new
version above and run this and sqlite_filter_bug.py while nothing is logged
to the lab.

usage: uv run --script bin/check_views.py
"""
import sys
from pathlib import Path
from typing import NamedTuple

import duckdb
from duckdb import DuckDBPyConnection

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # experiment_lab, with this script's DuckDB
from experiment_lab import query
from experiment_lab.settings import data_folder, database


class Sum(NamedTuple):
    rows: int
    md5: str  # of the rows as text, sorted


def checksum(con: DuckDBPyConnection, view: str) -> Sum:
    (row,) = con.execute(f"SELECT count(*), md5(coalesce(string_agg(t::VARCHAR, '|' ORDER BY t::VARCHAR), ''))"
                         f' FROM "{view}" t').fetchall()
    return Sum(*row)


def read(copy: bool, project: str | None) -> dict[str, Sum]:
    data = data_folder()
    if copy:
        con = duckdb.connect()
        con.execute(f"ATTACH '{database(data)}' AS source (TYPE sqlite, READ_ONLY)")
        con.execute("ATTACH ':memory:' AS lab")
        for (table,) in con.execute("SELECT table_name FROM information_schema.tables"
                                    " WHERE table_catalog = 'source'").fetchall():
            con.execute(f"CREATE TABLE lab.{table} AS FROM source.{table}")
        con.execute("DETACH source")
        sources = query.make_views(con, project)
    else:
        con, sources = query.connect(data, project)
    if query.errors(sources):
        sys.exit("\n".join(query.errors(sources)))
    rows = {view: checksum(con, view) for view, _ in query.view_columns(con)}
    con.close()
    return rows


same = True
for project in [name for name, _ in query.projects(query.attach(data_folder()))] or [None]:
    direct, copied = read(False, project), read(True, project)
    for name in direct:
        agree = "same" if direct[name] == copied[name] else "DIFFERENT"
        print(f"duckdb {duckdb.__version__}  {project}  {name:16}  direct {direct[name][0]:5} {direct[name][1]:32}"
              f"  copied {copied[name][0]:5} {copied[name][1]:32}  {agree}")
    same &= direct == copied
if not same:
    sys.exit("the two reads differ")
print("the two reads agree")
