"""Compares what Lab.runs and Lab.file read with what q's views give, on a new
lab of make_scratch.py with one more run, whose metrics were logged at two
steps, the lower one later, and twice at the same step and time, once as NaN,
and whose JSON file, logged by log_json, Lab.json reads back. For each
experiment: every run with the view runs; the latest runs for each param, for
all of them and for none with the macro latest_runs; the filters of Lab.runs
with a WHERE; the runs' files with the view files.

usage: uv run python checks/check_reader.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from duckdb import DuckDBPyConnection

from experiment_lab import query
from experiment_lab.lab import BELOW, Lab, RunRow, now

SWEEP = "sample rate sweep"
RESULT = {"rate": 22050, "passed": [1, 3]}
# the WHERE of the view runs that each filter of filtered stands for, in order
WHERE = ["status = 'FINISHED' AND tags['outcome'] = 'pass'",
         "status = 'FINISHED' AND params['rate'] IN ('16000', '48000')",
         "status = 'FINISHED' AND map_contains(metrics, 'loss')",
         "params['rate'] = '32000'"]


class Latest(NamedTuple):
    experiment: str
    keys: list[str]


class Read(NamedTuple):
    runs: list[str]  # each run of each experiment
    latest: list[str]  # the ids of the latest runs of each Latest
    filtered: list[str]  # the ids of the runs of each filter of filtered
    files: list[str]  # each file's run, name and path


def add_run(lab: Lab) -> str:
    at = now()
    with lab.run(SWEEP, "rate 22050", params={"rate": 22050}) as run:
        run.log_metrics({"accuracy": 0.95, "loss": float("nan")}, step=2, at=at)
        run.log_metrics({"loss": 0.2}, step=2, at=at)
        run.log_metrics({"accuracy": 0.5, "loss": 0.1}, step=1, at=at + 1000)
        run.log_json("result.json", RESULT)
    return run.id


def filtered(lab: Lab) -> list[str]:
    return [" ".join(r.id for r in rows) for rows in (
        lab.runs(SWEEP, tags={"outcome": "pass"}),
        lab.runs(SWEEP, params={"rate": (16000, 48000)}),
        lab.runs(SWEEP, has_metric="loss"),
        lab.runs(SWEEP, status=None, params={"rate": 32000}))]


def latest_of(experiment: str, runs: list[RunRow]) -> list[Latest]:
    """Each param of the runs, all of them and none."""
    names = sorted({k for r in runs for k in r.params})
    return [Latest(experiment, keys) for keys in [*([k] for k in names), names, []]]


def read_lab(lab: Lab, names: list[str], plans: list[Latest]) -> Read:
    runs = [(n, r) for n in names for r in lab.runs(n, status=None)]
    latest = [f"{p}: {' '.join(r.id for r in lab.runs(p.experiment, latest_by=p.keys))}" for p in plans]
    ids = {r.id for _, r in runs}
    files = sorted(f"{rid} {name} {lab.file(rid, name)}" for rid, name in lab.db.execute("SELECT run, name FROM files")
                   if rid in ids)
    return Read([f"{n}: {r}" for n, r in runs], latest, filtered(lab) if SWEEP in names else [], files)


def ids_of(con: DuckDBPyConnection, sql: str, args: list[object]) -> str:
    return " ".join(rid for (rid,) in con.execute(sql, args).fetchall())


def as_row(row: tuple[object, ...]) -> RunRow:
    rid, name, status, start, end, params, tags, metrics, note = row
    return RunRow(rid, name, status, start, end, params or {}, tags or {}, metrics or {}, note)  # type: ignore[arg-type]


def read_views(data: Path, project: str, names: list[str], plans: list[Latest]) -> Read:
    con, _ = query.connect(data, project)
    runs = [f"{n}: {as_row(row)}" for n in names for row in con.execute(
        "SELECT run, name, status, start_time, end_time, params, tags, metrics, note FROM runs"
        " WHERE experiment = ? ORDER BY start_time, run", [n]).fetchall()]
    latest = [f"{p}: {ids_of(con, 'SELECT run FROM latest_runs(?, ?::VARCHAR[]) ORDER BY start_time, run',
                              [p.experiment, p.keys])}" for p in plans]
    filtered = [ids_of(con, f"SELECT run FROM runs WHERE experiment = ? AND {w} ORDER BY start_time, run", [SWEEP])
                for w in WHERE] if SWEEP in names else []
    files = sorted(f"{rid} {name} {path}" for rid, name, path in con.execute("SELECT run, name, path FROM files").fetchall())
    con.close()
    return Read(runs, latest, filtered, files)


def compare(project: str, mine: Read, views: Read) -> bool:
    for part, a, b in zip(Read._fields, mine, views, strict=True):
        print(f"{project:12} {part:9} {len(a):3}  {'same' if a == b else 'DIFFERENT'}")
    return mine == views


def main() -> None:
    folder = Path(tempfile.mkdtemp()) / "lab"
    # in a process of its own, whose sqlite3 connections are closed before DuckDB opens lab.db (query.py)
    subprocess.run([sys.executable, Path(__file__).with_name("make_scratch.py"), folder], check=True)
    data = folder / "lab-data"
    same = True
    for project in ("audio-codec", "vision"):
        lab = Lab(data, project=project)
        if project == "audio-codec":
            same &= lab.json(add_run(lab), "result.json") == RESULT
        names = [n for (n,) in lab.db.execute(f"{BELOW} SELECT name FROM below", (project,))]
        plans = [p for n in names for p in latest_of(n, lab.runs(n, status=None))]
        mine = read_lab(lab, names, plans)
        lab.close()
        same &= compare(project, mine, read_views(data, project, names, plans))
    if not same:
        sys.exit("Lab.runs, Lab.file or Lab.json and the views differ")
    print("Lab.runs, Lab.file and Lab.json agree with the views")


main()
