"""Adds or changes a project, an experiment or a record (a decision, finding,
protocol rule or question) in the lab's database, starts or ends a run, or
prints the experiment tree.

usage: rec project NAME --text DESCRIPTION [--folder F] [--files F] [--views FILE ...]
       rec experiment NAME --text DESCRIPTION [--in PARENT] [--dead-end | --no-dead-end]
       rec tree [EXPERIMENT]
       rec add KIND "TITLE" --in EXPERIMENT --text T [--experiment LABEL] [--by WHO]
                            [--status S] [--answers ID] [--evidence ITEM ...] [--id ID] [--time T]
       rec set ID FIELD=VALUE ...
       rec open
       rec run EXPERIMENT NAME [--note T] [--start T]
       rec end RUN [--status FINISHED|FAILED|KILLED] [--at T]

The experiments form a tree whose roots are the projects. rec works in the
project whose folder holds the current folder (or its git repository's main
worktree), and uses only that project's experiments; from a folder no project
holds, it reaches every project's experiments by name, which are unique across
the lab, and a new experiment needs --in.

project makes a project, once, from its folder (by default the current git
repository's main worktree, else the current folder), with a one-line
description; given again, it changes what is given. --files is the folder its
files page shows (its folder unless given), --views its view files, run after
lab.sql. experiment makes an experiment directly below the project, or below
--in PARENT, with a description (Markdown: what it measures and why); given
again, it changes the description, moves it below --in, or marks it as a dead
end (an idea that failed; a finding says why) or not. tree prints the
project's experiments, or those below EXPERIMENT, one per line, indented, with
[dead end] after those marked so.

add files a record, whose text (Markdown) may not be empty, in the experiment
named by --in, which must exist, or in the project itself; --experiment gives
the label it is shown under (the --in experiment unless given). KIND is
decision, finding, protocol or question. A new question is open and any other record closed, unless --status
says otherwise (open, closed or superseded); a record added with --answers
closes that question. Evidence items are run:<run id>, file:<path>, sql:<query
over the views> or record:<id>; add and set warn about a file: item in a linked
git worktree, where a later checkout can change the file. set changes kind, title, text, by, status,
experiment, answers or evidence (items separated by ";"). add, set and open
then print the project's open records.

run starts a run, RUNNING, in an existing experiment, for work that runs
outside the Python that logs it, and prints its id; end ends a RUNNING run,
FINISHED unless --status says otherwise. --start, --at and add's --time are
local times as 2026-09-10T12:00. The database is lab.db in
$EXPERIMENT_LAB_DATA, else ~/lab-data.
"""
import argparse
import datetime
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

from experiment_lab import records
from experiment_lab.lab import Lab, now

# rec's field names for the columns of records that differ
COLUMNS = {"by": "author", "experiment": "label"}
SETTABLE = ("kind", "title", "text", "by", "status", "experiment", "answers", "evidence")


def ms(t: datetime.datetime | None) -> int | None:
    """t in milliseconds since 1970; None if None."""
    return None if t is None else int(t.timestamp() * 1000)


def show(r: records.Record) -> None:
    print(f"{r['id']}: {r['kind']}, {r['status']}: {r['title']}")


def shown(path: str) -> str:
    """path as ~/... under the home folder."""
    return "~/" + str(Path(path).relative_to(Path.home())) if Path(path).is_relative_to(Path.home()) else path


def show_open(lab: Lab) -> None:
    rows = records.open_records(lab, lab.current)
    print(f"open{' in ' + lab.current if lab.current else ''} ({len(rows)}):")
    for rid, label, kind, title in rows:
        print(f"  {rid} [{label}, {kind}] {title}")


def run_project(lab: Lab, a: argparse.Namespace) -> None:
    lab.project(a.name, a.text, folder=a.folder, files=a.files, views=a.views)
    folder, files, views = lab.db.execute("SELECT folder, files, views FROM experiments WHERE name = ?",
                                          (a.name,)).fetchone()
    print(f"project {a.name}, folder {shown(folder)}, files {shown(files)}, views {views or 'none'}")


def run_experiment(lab: Lab, a: argparse.Namespace) -> None:
    print("experiment " + " › ".join(lab.experiment(a.name, a.text, parent=a.parent, dead_end=a.dead_end)))


def run_tree(lab: Lab, a: argparse.Namespace) -> None:
    top = a.top or lab.here()
    path = lab.path(top)
    folder = lab.db.execute("SELECT folder FROM experiments WHERE name = ?", (top,)).fetchone()[0]
    print(f"{top}  ({shown(folder)})" if folder else " › ".join(path))

    def walk(name: str, depth: int) -> None:
        for child, dead_end in lab.db.execute("SELECT name, dead_end FROM experiments WHERE parent = ?"
                                              " ORDER BY created, name", (name,)).fetchall():
            print("  " * depth + child + ("  [dead end]" if dead_end else ""))
            walk(child, depth + 1)

    walk(top, 1)


def warn_overwritable(evidence: Iterable[str]) -> None:
    for item in records.in_linked_worktree(evidence):
        print(f"warning: {item} is in a linked git worktree, where a later checkout can change it;"
              " log the file with run.log_file and cite run:<run id>", file=sys.stderr)


def run_add(lab: Lab, a: argparse.Namespace) -> None:
    for r in records.add(lab, a.container, a.kind, a.title, label=a.experiment, text=a.text, author=a.by,
                         status=a.status, answers=a.answers, evidence=a.evidence, rid=a.id,
                         time=ms(a.time)):
        show(r)
    warn_overwritable(a.evidence)
    show_open(lab)


def run_set(lab: Lab, a: argparse.Namespace) -> None:
    fields = {}
    for assignment in a.assignments:
        field, _, value = assignment.partition("=")
        if field not in SETTABLE:
            raise ValueError(f"cannot set {field}; fields: {', '.join(SETTABLE)}")
        fields[COLUMNS.get(field, field)] = ([v.strip() for v in value.split(";") if v.strip()]
                                             if field == "evidence" else value)
    show(records.change(lab, a.id, **fields))
    warn_overwritable(fields.get("evidence", []))
    show_open(lab)


def run_run(lab: Lab, a: argparse.Namespace) -> None:
    print(lab.run(a.experiment, a.name, note=a.note, start=ms(a.start)).id)


def run_end(lab: Lab, a: argparse.Namespace) -> None:
    """Ends a RUNNING run, at --at or now, which may not be before its start."""
    run = lab.run_by_id(a.id)
    experiment, name, status, start = lab.db.execute("SELECT experiment, name, status, start_time FROM runs"
                                                     " WHERE id = ?", (a.id,)).fetchone()
    end = ms(a.at) or now()
    if status != "RUNNING":
        raise ValueError(f"run {name} in {experiment} has ended, {status}")
    if end < start:
        raise ValueError(f"run {name} in {experiment} started at {datetime.datetime.fromtimestamp(start / 1000)},"
                         " after --at")
    run.end(a.status, at=end)
    print(f"run {name} in {experiment}: {a.status}, {(end - start) / 60000:.1f} min")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    project = sub.add_parser("project")
    project.add_argument("name")
    project.add_argument("--text")
    project.add_argument("--folder")
    project.add_argument("--files")
    project.add_argument("--views", nargs="*")
    project.set_defaults(run=run_project)
    exp = sub.add_parser("experiment")
    exp.add_argument("name")
    exp.add_argument("--text")
    exp.add_argument("--in", dest="parent", metavar="PARENT")
    exp.add_argument("--dead-end", action=argparse.BooleanOptionalAction)
    exp.set_defaults(run=run_experiment)
    tree = sub.add_parser("tree")
    tree.add_argument("top", nargs="?", metavar="EXPERIMENT")
    tree.set_defaults(run=run_tree)
    add = sub.add_parser("add")
    add.add_argument("kind")
    add.add_argument("title")
    add.add_argument("--in", dest="container", required=True, metavar="EXPERIMENT")
    add.add_argument("--experiment")
    add.add_argument("--text", required=True)
    add.add_argument("--by", default="agent")
    add.add_argument("--status")
    add.add_argument("--answers", default="")
    add.add_argument("--evidence", nargs="*", default=[])
    add.add_argument("--id")
    add.add_argument("--time", type=datetime.datetime.fromisoformat)
    add.set_defaults(run=run_add)
    setp = sub.add_parser("set")
    setp.add_argument("id")
    setp.add_argument("assignments", nargs="+", metavar="FIELD=VALUE")
    setp.set_defaults(run=run_set)
    sub.add_parser("open").set_defaults(run=lambda lab, _: show_open(lab))
    run = sub.add_parser("run")
    run.add_argument("experiment")
    run.add_argument("name")
    run.add_argument("--note", default="")
    run.add_argument("--start", type=datetime.datetime.fromisoformat)
    run.set_defaults(run=run_run)
    end = sub.add_parser("end")
    end.add_argument("id", metavar="RUN")
    end.add_argument("--status", choices=("FINISHED", "FAILED", "KILLED"), default="FINISHED")
    end.add_argument("--at", type=datetime.datetime.fromisoformat)
    end.set_defaults(run=run_end)
    return p


def main() -> None:
    a = parser().parse_args()
    try:
        a.run(Lab(), a)
    except (ValueError, OSError, sqlite3.Error) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
