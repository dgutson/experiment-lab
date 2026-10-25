# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.6"]
# ///
"""Checks that DuckDB reads attached SQLite tables correctly: a filter on two
maps built from two attached SQLite tables must count the 50 rows that match
(DuckDB 1.5.5 counts 0). The view runs of lab.sql filters such maps. Before
raising the DuckDB pin in pyproject.toml, set the new version above and run
this and check_views.py; both must pass.

usage: uv run --script sqlite_filter_bug.py
"""
import os
import sqlite3
import tempfile

import duckdb

QUERY = """WITH t AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS tags
                      FROM {s}tags GROUP BY run_uuid),
     p AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS params
           FROM {s}params GROUP BY run_uuid)
SELECT count(*) FROM {s}runs LEFT JOIN t USING (run_uuid) LEFT JOIN p USING (run_uuid)
WHERE tags['dir'] = 'a' AND params['phase'] = 'tune'"""

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "runs.db")
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE runs(run_uuid TEXT PRIMARY KEY);
        CREATE TABLE tags(run_uuid TEXT, key TEXT, value TEXT, PRIMARY KEY (key, run_uuid));
        CREATE TABLE params(run_uuid TEXT, key TEXT, value TEXT, PRIMARY KEY (key, run_uuid));""")
    for i in range(100):
        db.execute("INSERT INTO runs VALUES (?)", (f"r{i}",))
        db.execute("INSERT INTO tags VALUES (?, 'dir', ?)", (f"r{i}", "a" if i % 2 else "b"))
        db.execute("INSERT INTO params VALUES (?, 'phase', ?)", (f"r{i}", "tune" if i % 2 else "eq"))
    db.commit()
    db.close()
    con = duckdb.connect()
    con.execute(f"ATTACH '{path}' AS ml (TYPE sqlite, READ_ONLY)")
    ((attached,),) = con.execute(QUERY.format(s="ml.")).fetchall()
    con.execute("CREATE TABLE runs AS FROM ml.runs; CREATE TABLE tags AS FROM ml.tags; CREATE TABLE params AS FROM ml.params")
    ((copied,),) = con.execute(QUERY.format(s="")).fetchall()
print(f"duckdb {duckdb.__version__}: attached SQLite {attached}, copies {copied}, expected 50:",
      "fixed" if attached == 50 else "bug present")
