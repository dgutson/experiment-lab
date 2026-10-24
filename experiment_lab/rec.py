"""Adds or changes a project, an experiment or a record (a decision, finding,
protocol rule or question) in the lab's database, or prints the experiment
tree.

usage: rec project NAME --text DESCRIPTION [--folder F] [--files F] [--views FILE ...]
       rec experiment NAME --text DESCRIPTION [--in PARENT] [--dead-end | --no-dead-end]
       rec tree [EXPERIMENT]
       rec add KIND "TITLE" --in EXPERIMENT --text T [--experiment LABEL] [--by WHO]
                            [--status S] [--answers ID] [--evidence ITEM ...] [--id ID] [--time T]
       rec set ID FIELD=VALUE ...
       rec open

The experiments form a tree whose roots are the projects. rec works in the
project whose folder holds the current folder (or its git repository's main
worktree), and uses only that project's experiments.

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
named by --in, which must exist; --experiment gives the label it is shown under
(the --in experiment unless given). KIND is decision, finding, protocol or
question. A new question is open and any other record closed, unless --status
says otherwise (open, closed or superseded); a record added with --answers
closes that question. Evidence items are run:<run id>, file:<path>, sql:<query
over the views> or record:<id>. set changes kind, title, text, by, status,
experiment, answers or evidence (items separated by ";"). add, set and open
then print the project's open records. The database is lab.db in
$EXPERIMENT_LAB_DATA, else ~/lab-data.
"""
import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

from experiment_lab import records
from experiment_lab.lab import Lab

# rec's field names for the columns of records that differ
COLUMNS = {"by": "author", "experiment": "label"}
SETTABLE = ("kind", "title", "text", "by", "status", "experiment", "answers", "evidence")


def show(r):
    print(f"{r['id']}: {r['kind']}, {r['status']}: {r['title']}")


def shown(path):
    """path as ~/... under the home folder."""
    return "~/" + str(Path(path).relative_to(Path.home())) if Path(path).is_relative_to(Path.home()) else path


def tree(lab, top):
    path = lab.path(top)
    folder = lab.db.execute("SELECT folder FROM experiments WHERE name = ?", (top,)).fetchone()[0]
    print(f"{top}  ({shown(folder)})" if folder else " › ".join(path))
    children = {}
    for name, parent, dead_end in lab.db.execute("SELECT name, parent, dead_end FROM experiments ORDER BY created, name"):
        children.setdefault(parent, []).append((name, dead_end))

    def walk(name, depth):
        for child, dead_end in children.get(name, []):
            print("  " * depth + child + ("  [dead end]" if dead_end else ""))
            walk(child, depth + 1)

    walk(top, 1)


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    project = sub.add_parser("project")
    project.add_argument("name")
    project.add_argument("--text")
    project.add_argument("--folder")
    project.add_argument("--files")
    project.add_argument("--views", nargs="*")
    exp = sub.add_parser("experiment")
    exp.add_argument("name")
    exp.add_argument("--text")
    exp.add_argument("--in", dest="parent", metavar="PARENT")
    exp.add_argument("--dead-end", action=argparse.BooleanOptionalAction)
    tree_p = sub.add_parser("tree")
    tree_p.add_argument("top", nargs="?", metavar="EXPERIMENT")
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
    setp = sub.add_parser("set")
    setp.add_argument("id")
    setp.add_argument("assignments", nargs="+", metavar="FIELD=VALUE")
    sub.add_parser("open")
    a = p.parse_args()
    lab = Lab()
    try:
        if a.command == "project":
            lab.project(a.name, a.text, folder=a.folder, files=a.files, views=a.views)
            folder, files, views = lab.db.execute("SELECT folder, files, views FROM experiments WHERE name = ?",
                                                  (a.name,)).fetchone()
            print(f"project {a.name}, folder {shown(folder)}, files {shown(files)}, views {views or 'none'}")
            return
        if a.command == "experiment":
            print("experiment " + " › ".join(lab.experiment(a.name, a.text, parent=a.parent, dead_end=a.dead_end)))
            return
        if a.command == "tree":
            tree(lab, a.top or lab.here())
            return
        if a.command == "add":
            for r in records.add(lab, a.container, a.kind, a.title, label=a.experiment, text=a.text, author=a.by,
                                 status=a.status, answers=a.answers, evidence=a.evidence, rid=a.id,
                                 time=a.time and int(a.time.timestamp() * 1000)):
                show(r)
        elif a.command == "set":
            fields = {}
            for assignment in a.assignments:
                field, _, value = assignment.partition("=")
                if field not in SETTABLE:
                    sys.exit(f"error: cannot set {field}; fields: {', '.join(SETTABLE)}")
                fields[COLUMNS.get(field, field)] = ([v.strip() for v in value.split(";") if v.strip()]
                                                     if field == "evidence" else value)
            show(records.change(lab, a.id, **fields))
    except (ValueError, sqlite3.IntegrityError) as e:
        sys.exit(f"error: {e}")
    rows = records.open_records(lab, lab.current)
    print(f"open{' in ' + lab.current if lab.current else ''} ({len(rows)}):")
    for rid, label, kind, title in rows:
        print(f"  {rid} [{label}, {kind}] {title}")


if __name__ == "__main__":
    main()
