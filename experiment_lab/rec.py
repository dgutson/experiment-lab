"""Adds or changes an experiment or a record (a decision, finding, protocol
rule or question) in the lab's database, then prints the open records.

usage: rec experiment NAME --text DESCRIPTION
       rec add KIND "TITLE" --in EXPERIMENT --text T [--experiment LABEL] [--by WHO]
                            [--status S] [--answers ID] [--evidence ITEM ...] [--id ID] [--time T]
       rec set ID FIELD=VALUE ...
       rec open

experiment makes an experiment, or sets its description (Markdown: what it
measures and why), which a new experiment needs. add files a record, whose text
(Markdown) may not be empty, in the experiment named by --in, which must exist;
--experiment gives the label it is shown under (the --in experiment unless
given). KIND is decision, finding, protocol or question. A new question is open
and any other record closed, unless --status says otherwise (open, closed or
superseded); a record added with --answers closes that question. Evidence items
are run:<run id>, file:<path>, sql:<query over the views> or record:<id>. set
changes kind, title, text, by, status, experiment, answers or evidence (items
separated by ";"). The database is lab.db in $EXPERIMENT_LAB_DATA, else
~/lab-data.
"""
import argparse
import datetime
import sqlite3
import sys

from experiment_lab import records
from experiment_lab.lab import Lab

# rec's field names for the columns of records that differ
COLUMNS = {"by": "author", "experiment": "label"}
SETTABLE = ("kind", "title", "text", "by", "status", "experiment", "answers", "evidence")


def show(r):
    print(f"{r['id']}: {r['kind']}, {r['status']}: {r['title']}")


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("experiment")
    exp.add_argument("name")
    exp.add_argument("--text")
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
        if a.command == "experiment":
            lab.experiment(a.name, a.text)
            print(f"experiment {a.name}")
        elif a.command == "add":
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
    rows = records.open_records(lab)
    print(f"open ({len(rows)}):")
    for rid, label, kind, title in rows:
        print(f"  {rid} [{label}, {kind}] {title}")


if __name__ == "__main__":
    main()
