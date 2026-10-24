"""Moves a data folder of mlflow-lab, whose runs and records an MLflow server
kept in mlflow.db, into a new lab.db, then checks the copy.

usage: lab-from-mlflow OLD_DATA_FOLDER

The new data folder is $EXPERIMENT_LAB_DATA, else ~/lab-data; it must have no
lab.db yet. mlflow.db and artifacts/ are only read, so the MLflow server may keep
running; what it writes after the read starts is not copied. What is copied:

- the experiments, with their descriptions (the experiment tag
  mlflow.note.content), except MLflow's Default experiment when it has no runs;
- the runs, with their params, every metric value and their tags; the tag
  mlflow.note.content becomes the run's note, and mlflow.runName, a copy of the
  run's name, is dropped;
- runs with a record.id tag become records, their record.* tags the record's
  fields; evidence items mlflow:<run id> become run:<run id>. A record run that
  holds more than the record (params, metrics, other tags, files) is also
  copied as a run, with the same id, and the record's evidence gains run:<id>;
- links to MLflow's pages in descriptions, notes and record texts
  (#/experiments/N and #/experiments/N/runs/ID) become links to the lab's pages;
- a run's files under artifacts/ that have no other hard link, to
  files/<run id>/; a file with another hard link is left out, since the run's
  own folder holds it;
- lab.toml, when the new data folder has none.

Deleted experiments and runs are left out. The check compares, per table, the
rows the old database should give with the rows copied, and, through DuckDB,
the rows of the old views (mlflow-lab's lab.sql over mlflow.db) with the new
ones: runs, records, and every view of the view files. Both databases are
opened with Python's sqlite3 and closed again before DuckDB reads them (see
query.py).
"""
import argparse
import collections
import os
import re
import shutil
import sqlite3
import stat
import sys
import urllib.parse
from pathlib import Path

import duckdb

from experiment_lab import query, records
from experiment_lab.lab import Lab
from experiment_lab.settings import data_folder, database, settings

NOTE, NAME = "mlflow.note.content", "mlflow.runName"
LINK = re.compile(r"(?:https?://(?:127\.0\.0\.1|localhost):\d+/)?#/experiments/(\d+)(?:/runs/([0-9a-f]{32}))?")
ARTIFACTS = "mlflow-artifacts:/"
# mlflow-lab's views runs and records, over MLflow's tables in the schema mlflow
OLD_VIEWS = """
CREATE VIEW runs AS
WITH t AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS tags FROM mlflow.tags GROUP BY run_uuid),
     p AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS params FROM mlflow.params GROUP BY run_uuid),
     m AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS metrics FROM mlflow.latest_metrics GROUP BY run_uuid)
SELECT r.run_uuid AS run, e.name AS experiment, r.name, r.status, r.start_time, t.tags, p.params, m.metrics
FROM mlflow.runs r JOIN mlflow.experiments e USING (experiment_id)
LEFT JOIN t USING (run_uuid) LEFT JOIN p USING (run_uuid) LEFT JOIN m USING (run_uuid)
WHERE r.lifecycle_stage = 'active';
CREATE VIEW records AS SELECT tags['record.id'] AS id, strftime(to_timestamp(start_time / 1000), '%Y-%m-%dT%H:%M') AS time,
    tags['record.experiment'] AS experiment, tags['record.kind'] AS kind,
    regexp_replace(name, '^' || tags['record.kind'] || ': ', '') AS title, tags['mlflow.note.content'] AS text,
    tags['record.by'] AS author, tags['record.status'] AS status, tags['record.answers'] AS answers,
    tags['record.evidence'] AS evidence, run
    FROM runs WHERE tags['record.kind'] IS NOT NULL;
"""


class Move:
    """What the old data folder holds, in the new tables' form."""

    def __init__(self, old):
        self.old = old
        db = sqlite3.connect(f"file:{old / 'mlflow.db'}?mode=ro", uri=True, isolation_level=None)
        db.execute("BEGIN")  # one snapshot for every read
        rows = db.execute("""SELECT e.experiment_id, e.name, coalesce(t.value, ''), coalesce(e.creation_time, 0),
                (SELECT count(*) FROM runs r WHERE r.experiment_id = e.experiment_id AND r.lifecycle_stage = 'active')
            FROM experiments e LEFT JOIN experiment_tags t ON t.experiment_id = e.experiment_id AND t.key = ?
            WHERE e.lifecycle_stage = 'active'""", (NOTE,)).fetchall()
        self.names = {str(eid): name for eid, name, *_ in rows}
        self.experiments = [(name, self.relink(text), created) for _, name, text, created, runs in rows
                            if runs or name != "Default"]
        live = {r[0]: r for r in db.execute("""SELECT r.run_uuid, e.name, r.name, r.status, r.start_time,
                r.end_time, r.artifact_uri FROM runs r JOIN experiments e USING (experiment_id)
            WHERE r.lifecycle_stage = 'active' AND e.lifecycle_stage = 'active'""")}

        def per_run(sql):
            found = collections.defaultdict(list)
            for run, *values in db.execute(sql):
                if run in live:
                    found[run].append(values)
            return found

        tags = per_run("SELECT run_uuid, key, value FROM tags")
        params = per_run("SELECT run_uuid, key, value FROM params")
        metrics = per_run("SELECT run_uuid, key, CASE WHEN is_nan THEN NULL ELSE value END, step, timestamp FROM metrics")
        db.close()
        self.runs, self.params, self.tags, self.metrics, self.records, self.files = [], [], [], [], [], []
        self.companions, self.left_out = set(), []
        for rid, (_, experiment, name, status, start, end, uri) in live.items():
            t = dict(tags[rid])
            files = self.files_of(rid, uri)
            other = {k: v for k, v in t.items() if k not in (NOTE, NAME) and not k.startswith("record.")}
            if "record.id" in t:
                self.records.append(self.record(experiment, name, start, t))
                if not (params[rid] or metrics[rid] or other or files):
                    continue
                self.companions.add(rid)
                self.records[-1]["evidence"].append(f"run:{rid}")
            self.runs.append((rid, experiment, name, status, start, end,
                              "" if rid in self.companions else self.relink(t.get(NOTE, ""))))
            self.params += [(rid, k, v) for k, v in params[rid]]
            self.tags += [(rid, k, v) for k, v in other.items()]
            self.metrics += [(rid, *m) for m in metrics[rid]]
            self.files += files

    def relink(self, text):
        def lab_page(m):
            if m[2]:
                return f"/lab/run/{m[2]}"
            return f"/lab/experiment/{urllib.parse.quote(self.names[m[1]])}" if m[1] in self.names else m[0]

        return LINK.sub(lab_page, text)

    def record(self, experiment, name, start, t):
        return {"id": t["record.id"], "experiment": experiment, "label": t.get("record.experiment") or experiment,
                "kind": t.get("record.kind"), "title": name.removeprefix(f"{t.get('record.kind')}: "),
                "text": self.relink(t.get(NOTE, "")), "author": t.get("record.by") or "",
                "status": t.get("record.status"), "answers": t.get("record.answers") or "", "evidence": evidence(t.get("record.evidence")),
                "time": start}

    def files_of(self, rid, uri):
        """The run's files to copy, as (source, path under files/<run id>/)."""
        if not uri or not uri.startswith(ARTIFACTS):
            return []
        folder = self.old / "artifacts" / uri.removeprefix(ARTIFACTS)
        found = []
        for parent, _, names in os.walk(folder):
            for name in names:
                path = Path(parent, name)
                s = path.lstat()
                if stat.S_ISREG(s.st_mode) and s.st_nlink == 1:
                    found.append((path, Path(rid) / path.relative_to(folder)))
                else:
                    self.left_out.append(path)
        return found

    def write(self, data):
        lab = Lab(data)
        with lab.write() as db:
            db.executemany("INSERT INTO experiments (name, description, created) VALUES (?, ?, ?)", self.experiments)
            db.executemany("INSERT INTO runs (id, experiment, name, status, start_time, end_time, note)"
                           " VALUES (?, ?, ?, ?, ?, ?, ?)", self.runs)
            db.executemany("INSERT INTO params (run, key, value) VALUES (?, ?, ?)", self.params)
            db.executemany("INSERT INTO tags (run, key, value) VALUES (?, ?, ?)", self.tags)
            db.executemany("INSERT INTO metrics (run, key, value, step, time) VALUES (?, ?, ?, ?, ?)", self.metrics)
            for r in self.records:
                records.save(db, r)
        lab.close()
        for source, path in self.files:
            (data / "files" / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, data / "files" / path)
        if not (data / "lab.toml").exists() and (self.old / "lab.toml").exists():
            shutil.copy2(self.old / "lab.toml", data / "lab.toml")


def evidence(text):
    """The items of an old record's evidence, mlflow:<run id> as run:<run id>."""
    return ["run:" + item.removeprefix("mlflow:") if item.startswith("mlflow:") else item for item in records.split(text)]


def same(v):
    """v in a form that compares by value: maps as sorted pairs, an empty map as none."""
    if isinstance(v, dict):
        return tuple(sorted((k, same(x)) for k, x in v.items())) or None
    if isinstance(v, (list, tuple)):
        return tuple(same(x) for x in v)
    return v


def compare(name, old_rows, new_rows):
    old, new = collections.Counter(map(same, old_rows)), collections.Counter(map(same, new_rows))
    print(f"{name:20} {old.total():8} {new.total():8}  {'same' if old == new else 'DIFFERENT'}")
    for row in list((old - new).keys())[:3]:
        print(f"    only old: {str(row)[:300]}")
    for row in list((new - old).keys())[:3]:
        print(f"    only new: {str(row)[:300]}")
    return old == new


def check(move, data):
    view_files = settings(data)[1]
    old = duckdb.connect()
    old.execute(f"ATTACH '{move.old / 'mlflow.db'}' AS ml (TYPE sqlite, READ_ONLY)")
    old.execute("CREATE SCHEMA mlflow")
    for table in ("experiments", "experiment_tags", "runs", "tags", "params", "metrics", "latest_metrics"):
        old.execute(f"CREATE VIEW mlflow.{table} AS FROM ml.{table}")
    old.execute(OLD_VIEWS)
    query.run_files(old, view_files, keep_going=False)
    new = query.connect(data, keep_going=False)
    old.execute("CREATE SCHEMA moved")  # out of main, so out of the views compared below
    old.execute("CREATE TABLE moved.companions AS SELECT unnest(?::VARCHAR[]) AS run", [sorted(move.companions)])
    old.execute("""CREATE VIEW moved.copied AS SELECT run FROM main.runs WHERE tags['record.id'] IS NULL
                   UNION ALL FROM moved.companions""")
    expected = old.execute(f"""SELECT
        (SELECT count(*) FROM mlflow.experiments e WHERE lifecycle_stage = 'active'
            AND (name <> 'Default' OR EXISTS (FROM runs WHERE experiment = e.name))),
        (SELECT count(*) FROM moved.copied),
        (SELECT count(*) FROM mlflow.params WHERE run_uuid IN (FROM moved.copied)),
        (SELECT count(*) FROM mlflow.tags WHERE run_uuid IN (FROM moved.copied)
            AND key NOT IN ('{NOTE}', '{NAME}') AND key NOT LIKE 'record.%'),
        (SELECT count(*) FROM mlflow.metrics WHERE run_uuid IN (FROM moved.copied)),
        (SELECT count(*) FROM records)""").fetchone() or ()
    tables = ("experiments", "runs", "params", "tags", "metrics", "records")
    print(f"{'table':20} {'expected':>8} {'copied':>8}")
    ok = True
    for table, n in zip(tables, expected):
        copied = (new.execute(f"SELECT count(*) FROM lab.{table}").fetchone() or (0,))[0]
        print(f"{table:20} {n:8} {copied:8}  {'same' if n == copied else 'DIFFERENT'}")
        ok &= n == copied
    print(f"\n{'view':20} {'old rows':>8} {'new rows':>8}")
    columns = "id, time, experiment, kind, title, text, author, status, answers, evidence"
    ok &= compare("records", [
        (*r[:5], move.relink(r[5] or ""), r[6] or "", r[7], r[8] or "",
         "; ".join([*evidence(r[9]), *([f"run:{r[10]}"] if r[10] in move.companions else [])]))
        for r in old.execute(f"SELECT {columns}, run FROM records").fetchall()],
        new.execute(f"SELECT {columns} FROM records").fetchall())
    run_columns = "run, experiment, name, status, start_time"
    ok &= compare("runs", [
        (*r[:5], {k: v for k, v in (r[5] or {}).items() if k not in (NOTE, NAME)}, r[6], r[7],
         move.relink((r[5] or {}).get(NOTE, "")))
        for r in old.execute(f"SELECT {run_columns}, tags, params, metrics FROM runs"
                             " WHERE tags['record.id'] IS NULL").fetchall()],
        new.execute(f"SELECT {run_columns}, tags, params, metrics, note FROM runs"
                    " WHERE run NOT IN (SELECT unnest(?::VARCHAR[]))", [sorted(move.companions)]).fetchall())
    ok &= compare("experiments", [(n, move.relink(d or "")) for n, d in old.execute(
        """SELECT e.name, t.value FROM mlflow.experiments e LEFT JOIN mlflow.experiment_tags t
           ON t.experiment_id = e.experiment_id AND t.key = 'mlflow.note.content' WHERE e.lifecycle_stage = 'active'
           AND (e.name <> 'Default' OR EXISTS (FROM runs WHERE experiment = e.name))""").fetchall()],
        new.execute("SELECT name, description FROM lab.experiments").fetchall())
    for view, _ in query.view_columns(old):
        if view not in ("runs", "records"):
            ok &= compare(view, old.execute(f'FROM "{view}"').fetchall(), new.execute(f'FROM "{view}"').fetchall())
    return ok


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("old", type=Path, metavar="OLD_DATA_FOLDER")
    old = p.parse_args().old.expanduser()
    data = data_folder()
    if not (old / "mlflow.db").exists():
        sys.exit(f"error: no {old / 'mlflow.db'}")
    if database(data).exists():
        sys.exit(f"error: {database(data)} exists; move it away first")
    move = Move(old)
    move.write(data)
    print(f"{database(data)}: {len(move.experiments)} experiments, {len(move.runs)} runs "
          f"({len(move.companions)} of them a record's run too), {len(move.records)} records; "
          f"{len(move.files)} files copied to {data / 'files'}, {len(move.left_out)} with other links left out\n")
    if not check(move, data):
        sys.exit("the copy differs")
    print("the copy agrees")


if __name__ == "__main__":
    main()
