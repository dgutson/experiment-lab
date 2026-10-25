"""Moves a data folder of mlflow-lab, whose runs and records an MLflow server
kept in mlflow.db, into a new lab.db, then checks the copy.

usage: lab-from-mlflow OLD_DATA_FOLDER PROJECT --text DESCRIPTION [--folder FOLDER]

The new data folder is $EXPERIMENT_LAB_DATA, else ~/lab-data; it must have no
lab.db yet. mlflow.db and artifacts/ are only read, so the MLflow server may keep
running; what it writes after the read starts is not copied. What is copied:

- the experiments, with their descriptions (the experiment tag
  mlflow.note.content), except MLflow's Default experiment when it has no runs,
  directly below a new project, PROJECT, with the one-line DESCRIPTION. Its
  folder is FOLDER, by default the current git repository's main worktree,
  else the current folder; its files folder and view files are those of the
  old folder's lab.toml (files, by default its folder, and views);
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
  own folder holds it.

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
import tomllib
import urllib.parse
from collections.abc import Container, Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import duckdb
from duckdb import DuckDBPyConnection

from experiment_lab import query, records
from experiment_lab.lab import Lab
from experiment_lab.settings import data_folder, database

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


type Row = tuple[Any, ...]  # a row as sqlite3 or DuckDB returns it
type Rows = list[Row]


class Experiment(NamedTuple):
    name: str
    description: str
    created: int


class RunRow(NamedTuple):
    id: str
    experiment: str
    name: str
    status: str
    start_time: int
    end_time: int | None
    note: str


class Pair(NamedTuple):
    """A row of params or tags."""
    run: str
    key: str
    value: str


class Metric(NamedTuple):
    run: str
    key: str
    value: float | None
    step: int
    time: int


class RunFile(NamedTuple):
    source: Path
    path: Path  # under files/


def per_run(db: sqlite3.Connection, sql: str, live: Container[str]) -> collections.defaultdict[str, Rows]:
    """The rows of sql, whose first column is a run id, per run of live, without that column."""
    found = collections.defaultdict(list)
    for run, *values in db.execute(sql):
        if run in live:
            found[run].append(tuple(values))
    return found


def run_tags(tags: Mapping[str, str]) -> dict[str, str]:
    """The tags that stay a run's: not its note, its name or a record's fields."""
    return {k: v for k, v in tags.items() if k not in (NOTE, NAME) and not k.startswith("record.")}


class Move:
    """What the old data folder holds, in the new tables' form."""

    def __init__(self, old: Path) -> None:
        self.old = old
        toml = old / "lab.toml"
        settings = tomllib.loads(toml.read_text()) if toml.exists() else {}
        self.files = settings.get("files") and str(old / Path(settings["files"]).expanduser())
        self.views = [old / Path(path).expanduser() for path in settings.get("views", [])]
        self.runs: list[RunRow] = []
        self.params: list[Pair] = []
        self.tags: list[Pair] = []
        self.metrics: list[Metric] = []
        self.records: list[records.Record] = []
        self.run_files: list[RunFile] = []
        self.companions: set[str] = set()
        self.left_out: list[Path] = []
        db = sqlite3.connect(f"file:{old / 'mlflow.db'}?mode=ro", uri=True, isolation_level=None)
        db.execute("BEGIN")  # one snapshot for every read
        rows = db.execute("""SELECT e.experiment_id, e.name, coalesce(t.value, ''), coalesce(e.creation_time, 0),
                (SELECT count(*) FROM runs r WHERE r.experiment_id = e.experiment_id AND r.lifecycle_stage = 'active')
            FROM experiments e LEFT JOIN experiment_tags t ON t.experiment_id = e.experiment_id AND t.key = ?
            WHERE e.lifecycle_stage = 'active'""", (NOTE,)).fetchall()
        self.names = {str(eid): name for eid, name, *_ in rows}
        self.experiments = [Experiment(name, self.relink(text), created) for _, name, text, created, runs in rows
                            if runs or name != "Default"]
        live = {r[0]: r for r in db.execute("""SELECT r.run_uuid, e.name, r.name, r.status, r.start_time,
                r.end_time, r.artifact_uri FROM runs r JOIN experiments e USING (experiment_id)
            WHERE r.lifecycle_stage = 'active' AND e.lifecycle_stage = 'active'""")}
        tags = per_run(db, "SELECT run_uuid, key, value FROM tags", live)
        params = per_run(db, "SELECT run_uuid, key, value FROM params", live)
        metrics = per_run(db, "SELECT run_uuid, key, CASE WHEN is_nan THEN NULL ELSE value END, step, timestamp"
                              " FROM metrics", live)
        db.close()
        for rid, (_, experiment, name, status, start, end, uri) in live.items():
            self.add_run(RunRow(rid, experiment, name, status, start, end, ""), uri, dict(tags[rid]), params[rid],
                         metrics[rid])

    def add_run(self, run: RunRow, uri: str | None, tags: Mapping[str, str], params: Rows, metrics: Rows) -> None:
        """Adds the run, or the record it holds. A record's run that holds more
        than the record (params, metrics, other tags, files) is also copied as a
        run, with the same id, and the record's evidence gains run:<id>."""
        files = self.files_of(run.id, uri)
        other = run_tags(tags)
        if "record.id" in tags:
            self.records.append(self.record(run.experiment, run.name, run.start_time, tags))
            if not any((params, metrics, other, files)):
                return
            self.companions.add(run.id)
            self.records[-1]["evidence"].append(f"run:{run.id}")
        self.runs.append(run._replace(note="" if run.id in self.companions else self.relink(tags.get(NOTE, ""))))
        self.params += [Pair(run.id, k, v) for k, v in params]
        self.tags += [Pair(run.id, k, v) for k, v in other.items()]
        self.metrics += [Metric(run.id, *m) for m in metrics]
        self.run_files += files

    def relink(self, text: str) -> str:
        def lab_page(m: re.Match[str]) -> str:
            if m[2]:
                return f"/lab/run/{m[2]}"
            return f"/lab/experiment/{urllib.parse.quote(self.names[m[1]])}" if m[1] in self.names else m[0]

        return LINK.sub(lab_page, text)

    def record(self, experiment: str, name: str, start: int, t: Mapping[str, str]) -> records.Record:
        kind = t.get("record.kind", "")
        return records.Record(id=t["record.id"], experiment=experiment, label=t.get("record.experiment") or experiment,
                              kind=kind, title=name.removeprefix(f"{kind}: "), text=self.relink(t.get(NOTE, "")),
                              author=t.get("record.by") or "", status=t.get("record.status", ""),
                              answers=t.get("record.answers") or "", evidence=evidence(t.get("record.evidence")),
                              time=start)

    def files_of(self, rid: str, uri: str | None) -> list[RunFile]:
        """The run's files to copy."""
        if not uri or not uri.startswith(ARTIFACTS):
            return []
        folder = self.old / "artifacts" / uri.removeprefix(ARTIFACTS)
        found = []
        for parent, _, names in os.walk(folder):
            for name in names:
                path = Path(parent, name)
                s = path.lstat()
                if stat.S_ISREG(s.st_mode) and s.st_nlink == 1:
                    found.append(RunFile(path, Path(rid) / path.relative_to(folder)))
                else:
                    self.left_out.append(path)
        return found

    def write(self, data: Path, project: str, description: str, folder: str | None) -> None:
        lab = Lab(data)
        lab.project(project, description, folder=folder, files=self.files, views=self.views)
        with lab.write() as db:
            db.executemany("INSERT INTO experiments (name, parent, description, created) VALUES (?, ?, ?, ?)",
                           [(name, project, text, created) for name, text, created in self.experiments])
            db.executemany("INSERT INTO runs (id, experiment, name, status, start_time, end_time, note)"
                           " VALUES (?, ?, ?, ?, ?, ?, ?)", self.runs)
            db.executemany("INSERT INTO params (run, key, value) VALUES (?, ?, ?)", self.params)
            db.executemany("INSERT INTO tags (run, key, value) VALUES (?, ?, ?)", self.tags)
            db.executemany("INSERT INTO metrics (run, key, value, step, time) VALUES (?, ?, ?, ?, ?)", self.metrics)
            for r in self.records:
                records.save(db, r)
        lab.close()
        for source, path in self.run_files:
            (data / "files" / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, data / "files" / path)


def evidence(text: str | None) -> list[str]:
    """The items of an old record's evidence, mlflow:<run id> as run:<run id>."""
    return ["run:" + item.removeprefix("mlflow:") if item.startswith("mlflow:") else item for item in records.split(text)]


def same(v: object) -> object:
    """v in a form that compares by value: maps as sorted pairs, an empty map as none."""
    if isinstance(v, dict):
        return tuple(sorted((k, same(x)) for k, x in v.items())) or None
    if isinstance(v, (list, tuple)):
        return tuple(same(x) for x in v)
    return v


def compare(name: str, old_rows: Iterable[Row], new_rows: Iterable[Row]) -> bool:
    old, new = collections.Counter(map(same, old_rows)), collections.Counter(map(same, new_rows))
    print(f"{name:20} {old.total():8} {new.total():8}  {'same' if old == new else 'DIFFERENT'}")
    for row in list((old - new).keys())[:3]:
        print(f"    only old: {str(row)[:300]}")
    for row in list((new - old).keys())[:3]:
        print(f"    only new: {str(row)[:300]}")
    return old == new


def old_database(move: Move) -> DuckDBPyConnection:
    """mlflow.db attached to a new DuckDB database, with mlflow-lab's views
    runs and records of it."""
    old = duckdb.connect()
    old.execute(f"ATTACH '{move.old / 'mlflow.db'}' AS ml (TYPE sqlite, READ_ONLY)")
    old.execute("CREATE SCHEMA mlflow")
    for table in ("experiments", "experiment_tags", "runs", "tags", "params", "metrics", "latest_metrics"):
        old.execute(f"CREATE VIEW mlflow.{table} AS FROM ml.{table}")
    old.execute(OLD_VIEWS)
    return old


def compare_counts(old: DuckDBPyConnection, new: DuckDBPyConnection, move: Move) -> bool:
    """Compares, per table, the rows the old database should give with the rows copied."""
    old.execute("CREATE SCHEMA moved")  # out of main, so out of the views compared later
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
        below = " WHERE parent IS NOT NULL" if table == "experiments" else ""  # not the project
        copied = (new.execute(f"SELECT count(*) FROM lab.{table}{below}").fetchone() or (0,))[0]
        print(f"{table:20} {n:8} {copied:8}  {'same' if n == copied else 'DIFFERENT'}")
        ok &= n == copied
    return ok


RECORD_COLUMNS = "id, time, experiment, kind, title, text, author, status, answers, evidence"
RUN_COLUMNS = "run, experiment, name, status, start_time"


def old_records(old: DuckDBPyConnection, move: Move) -> Rows:
    """The rows of the old view records as the new one should give them."""
    return [(*r[:5], move.relink(r[5] or ""), r[6] or "", r[7], r[8] or "",
             "; ".join([*evidence(r[9]), *([f"run:{r[10]}"] if r[10] in move.companions else [])]))
            for r in old.execute(f"SELECT {RECORD_COLUMNS}, run FROM records").fetchall()]


def old_runs(old: DuckDBPyConnection, move: Move) -> Rows:
    """The rows of the old view runs, those that are not records, as the new one should give them."""
    return [(*r[:5], {k: v for k, v in (r[5] or {}).items() if k not in (NOTE, NAME)}, r[6], r[7],
             move.relink((r[5] or {}).get(NOTE, "")))
            for r in old.execute(f"SELECT {RUN_COLUMNS}, tags, params, metrics FROM runs"
                                 " WHERE tags['record.id'] IS NULL").fetchall()]


def compare_views(old: DuckDBPyConnection, new: DuckDBPyConnection, move: Move) -> bool:
    """Compares the rows of the old views with the new ones: runs, records,
    experiments, and every view of the view files."""
    print(f"\n{'view':20} {'old rows':>8} {'new rows':>8}")
    ok = compare("records", old_records(old, move), new.execute(f"SELECT {RECORD_COLUMNS} FROM records").fetchall())
    ok &= compare("runs", old_runs(old, move),
                  new.execute(f"SELECT {RUN_COLUMNS}, tags, params, metrics, note FROM runs"
                              " WHERE run NOT IN (SELECT unnest(?::VARCHAR[]))", [sorted(move.companions)]).fetchall())
    ok &= compare("experiments", [(n, move.relink(d or "")) for n, d in old.execute(
        """SELECT e.name, t.value FROM mlflow.experiments e LEFT JOIN mlflow.experiment_tags t
           ON t.experiment_id = e.experiment_id AND t.key = 'mlflow.note.content' WHERE e.lifecycle_stage = 'active'
           AND (e.name <> 'Default' OR EXISTS (FROM runs WHERE experiment = e.name))""").fetchall()],
        new.execute("SELECT name, description FROM lab.experiments WHERE parent IS NOT NULL").fetchall())
    for view, _ in query.view_columns(old):
        if view not in ("runs", "records"):
            sql = "FROM " + query.identifier(view)
            ok &= compare(view, old.execute(sql).fetchall(), new.execute(sql).fetchall())
    return ok


def check(move: Move, data: Path, project: str) -> bool:
    """Compares the copy with the old database (see the module's docstring)."""
    old = old_database(move)
    failed = [f"old {path.name}: {e}" for path in move.views for e in query.run_file(old, path)]
    new, sources = query.connect(data, project)
    failed += [f"new {e}" for e in query.errors(sources)]
    for error in failed:
        print(error)
    if failed:
        return False
    counted = compare_counts(old, new, move)
    return compare_views(old, new, move) and counted


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("old", type=Path, metavar="OLD_DATA_FOLDER")
    p.add_argument("project")
    p.add_argument("--text", required=True)
    p.add_argument("--folder")
    a = p.parse_args()
    old = a.old.expanduser()
    data = data_folder()
    if not (old / "mlflow.db").exists():
        sys.exit(f"error: no {old / 'mlflow.db'}")
    if database(data).exists():
        sys.exit(f"error: {database(data)} exists; move it away first")
    move = Move(old)
    if a.project in [name for name, *_ in move.experiments]:
        sys.exit(f"error: {a.project} is the name of an old experiment; give the project another")
    move.write(data, a.project, a.text, a.folder)
    print(f"{database(data)}: {len(move.experiments)} experiments, {len(move.runs)} runs "
          f"({len(move.companions)} of them a record's run too), {len(move.records)} records; "
          f"{len(move.run_files)} files copied to {data / 'files'}, {len(move.left_out)} with other links left out\n")
    if not check(move, data, a.project):
        sys.exit("the copy differs")
    print("the copy agrees")


if __name__ == "__main__":
    main()
