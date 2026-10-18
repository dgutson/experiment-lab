# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.6"]
# ///
"""Compares every view of q on the data folder's mlflow.db read two ways: with
MLflow's tables as views over the attached file, as q reads them, and as tables
copied from it, as the /lab pages read them. Prints each view's row count and a
checksum of its sorted rows; the two ways must agree. Before raising the
DuckDB pin in q.py and in mlflow-lab, set the new version above and run this
and sqlite_filter_bug.py while nothing is logged to MLflow.

usage: uv run --script check_views.py
"""
import sys

import duckdb

import q


def read(kind):
    con = q.connect(kind, keep_going=False)
    views = [name for (name,) in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog = current_database() "
        "AND table_schema = 'main' ORDER BY table_name").fetchall()]
    rows = {view: con.execute(f"SELECT count(*), md5(coalesce(string_agg(t::VARCHAR, '|' ORDER BY t::VARCHAR), ''))"
                              f' FROM "{view}" t').fetchone() for view in views}
    con.close()
    return rows


direct, copied = read("VIEW"), read("TABLE")
for name in direct:
    same = "same" if direct[name] == copied[name] else "DIFFERENT"
    print(f"duckdb {duckdb.__version__}  {name:16}  direct {direct[name][0]:5} {direct[name][1]:32}"
          f"  copied {copied[name][0]:5} {copied[name][1]:32}  {same}")
if direct != copied:
    sys.exit("the two reads differ")
print("the two reads agree")
