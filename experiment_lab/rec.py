"""Adds or changes a project, an experiment or a record (a decision, finding,
protocol rule or question) in the lab's database, starts or ends a run, or
prints the experiment tree. rec COMMAND --help prints COMMAND's paragraph
below, and the one that applies to every command.

usage: rec project NAME --text DESCRIPTION [--folder F] [--files F] [--views FILE ...] [--replace]
       rec experiment NAME --text DESCRIPTION [--in PARENT] [--dead-end | --no-dead-end] [--replace]
       rec tree [EXPERIMENT]
       rec add KIND "TITLE" --in EXPERIMENT --text T [--experiment LABEL] [--by WHO]
                            [--status S] [--answers ID] [--evidence ITEM ...] [--id ID] [--time T]
       rec set ID FIELD=VALUE ...
       rec open
       rec run EXPERIMENT NAME [--note T] [--start T]
       rec end RUN [--status FINISHED|FAILED|KILLED] [--at T] [--metric K=V ...] [--tag K=V ...]
                   [--file PATH[=NAME] ...]
       (each but project takes --project P)

The experiments form a tree whose roots are the projects. rec works in the
project --project names, else in the one whose folder holds the current folder
(or the folder at the same place in its git repository's main worktree), and
uses only that project's experiments, runs and records; with no project, it
refuses them, and open prints every project's open records. A --text of -
reads the text from stdin, without its last line breaks: a quoted heredoc
(--text - <<'EOF') needs no escaping. add's --time, run's --start and end's
--at are local times as "2026-09-10 12:00" (or 2026-09-10T12:00). The database
is lab.db in $EXPERIMENT_LAB_DATA, else ~/lab-data.

project NAME makes a project, once, from its folder, --folder (by default the
current git repository's main worktree, else the current folder), with --text,
a one-line description; given again, it changes what is given. --files is the
folder its files page shows (its folder unless given), --views its view files,
run after lab.sql in that order (--views alone clears them). A description
other than the project's own is refused unless --replace.

experiment NAME makes an experiment directly below the project, or below --in
PARENT, with --text, its description (Markdown: what it measures and why);
given again, it moves it below --in, or marks it as a dead end with --dead-end
(an idea that failed; a finding says why) or not with --no-dead-end. A
description other than an existing experiment's own is refused unless
--replace, since a new experiment needs a name that no experiment of the lab
has.

tree prints the project's experiments, or those below EXPERIMENT, one per line,
each indented below the one it came from, with [dead end] after those marked
so.

add KIND TITLE files a record in the experiment that --in names, which must
exist, or with --in PROJECT in the project itself. KIND is decision, finding,
protocol or question; TITLE is its title, not its id. --text, its text
(Markdown), may not be empty. --experiment gives the label it is shown under
(the --in experiment unless given), --by its author (agent unless given). A new
question is open and any other record closed, unless --status says otherwise
(open, closed or superseded); --answers ID closes the question ID. --evidence
items are run:<run id>, file:<path>, sql:<query over the views> or
record:<id>, the run or record existing; a file: item in a linked git worktree,
where a later checkout can change the file, is warned about. --id gives the
record's id (lower-case letters, digits and dashes), else rec makes one from
the title's first words; --time its time, now unless given. add prints the
records it adds or closes, then how many of the project's records are open.

set ID FIELD=VALUE ... changes the record ID's FIELD: kind, title, text, by,
status, experiment, answers or evidence (items separated by ";"). FIELD=-
reads VALUE from stdin, as --text - does, for one FIELD per call. It warns
about a file: item as add does, and prints the record, then how many of the
project's records are open.

open lists the project's open records.

run EXPERIMENT NAME starts a run, RUNNING, in an existing experiment, for work
that runs outside the Python that logs it, and prints its id. --note is the
run's note, and --start its start, now unless given.

end RUN ends a RUNNING run, FINISHED unless --status says FAILED or KILLED, at
--at, now unless given, which may not be before the run's start. First it
logs its results, as lab.py's log_metrics, set_tags and log_file do: --metric
KEY=VALUE items, each a number, at step 0 and the end's time; --tag KEY=VALUE
items; --file items, each PATH, the file copied under its own name, or
PATH=NAME (split at the last =), copied as NAME. A wrong item is refused
before anything is written.
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
type Commands = argparse._SubParsersAction[argparse.ArgumentParser]  # what add_subparsers returns


def ms(t: datetime.datetime | None) -> int | None:
    """t in milliseconds since 1970; None if None."""
    return None if t is None else int(t.timestamp() * 1000)


def given_text(value: str) -> str:
    """value; for -, stdin's text without its last line breaks."""
    return sys.stdin.read().rstrip("\n") if value == "-" else value


def pair(item: str) -> tuple[str, str]:
    """KEY=VALUE as (KEY, VALUE)."""
    key, eq, value = item.partition("=")
    if not (key and eq):
        raise argparse.ArgumentTypeError(f"not KEY=VALUE: {item}")
    return key, value


def number(item: str) -> tuple[str, str]:
    """KEY=VALUE whose VALUE is a number, as (KEY, VALUE)."""
    key, value = pair(item)
    try:
        float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {item}") from None
    return key, value


def file_item(item: str) -> tuple[str, str]:
    """PATH or PATH=NAME, split at the last = unless the whole is a file, as
    (PATH, NAME), NAME '' when not given; PATH must be a file."""
    path, _, name = (item, "", "") if Path(item).is_file() else item.rpartition("=")
    if not Path(path).is_file():
        raise argparse.ArgumentTypeError(f"no file {path or item}")
    return path, name


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


def show_open_count(lab: Lab) -> None:
    """How many of the project's records are open, and the command that lists them."""
    named = f" --project {lab.named}" if lab.named else ""
    print(f"{len(records.open_records(lab, lab.current))} open in {lab.current}; rec open{named} lists them")


def run_project(lab: Lab, a: argparse.Namespace) -> None:
    lab.project(a.name, a.text, folder=a.folder, files=a.files, views=a.views, replace=a.replace)
    folder, files, views = lab.db.execute("SELECT folder, files, views FROM experiments WHERE name = ?",
                                          (a.name,)).fetchone()
    print(f"project {a.name}, folder {shown(folder)}, files {shown(files)}, views {views or 'none'}")


def run_experiment(lab: Lab, a: argparse.Namespace) -> None:
    print("experiment " + " › ".join(lab.experiment(a.name, a.text, parent=a.parent, dead_end=a.dead_end,
                                                    replace=a.replace)))


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
    show_open_count(lab)


def run_set(lab: Lab, a: argparse.Namespace) -> None:
    if [x.partition("=")[2] for x in a.assignments].count("-") > 1:
        raise ValueError("stdin holds one value: one FIELD=- per call")
    fields = {}
    for assignment in a.assignments:
        field, _, value = assignment.partition("=")
        if field not in SETTABLE:
            raise ValueError(f"cannot set {field}; fields: {', '.join(SETTABLE)}")
        value = given_text(value)
        fields[COLUMNS.get(field, field)] = ([v.strip() for v in value.split(";") if v.strip()]
                                             if field == "evidence" else value)
    show(records.change(lab, a.id, **fields))
    warn_overwritable(fields.get("evidence", []))
    show_open_count(lab)


def run_run(lab: Lab, a: argparse.Namespace) -> None:
    print(lab.run(a.experiment, a.name, note=a.note, start=ms(a.start)).id)


def run_end(lab: Lab, a: argparse.Namespace) -> None:
    """Logs the results given and ends a RUNNING run, at --at or now, which may
    not be before its start."""
    run = lab.run_by_id(a.id)
    experiment, name, status, start = lab.db.execute("SELECT experiment, name, status, start_time FROM runs"
                                                     " WHERE id = ?", (a.id,)).fetchone()
    end = ms(a.at) or now()
    if status != "RUNNING":
        raise ValueError(f"run {name} in {experiment} has ended, {status}")
    if end < start:
        raise ValueError(f"run {name} in {experiment} started at"
                         f" {datetime.datetime.fromtimestamp(start / 1000):%Y-%m-%d %H:%M}, after --at")
    run.log_metrics({k: float(v) for k, v in a.metric}, at=end)
    run.set_tags(dict(a.tag))
    for path, logged_as in a.file:
        run.log_file(path, logged_as or None)
    run.end(a.status, at=end)
    print(f"run {name} in {experiment}: {a.status}, {(end - start) / 60000:.1f} min")


def command(sub: Commands, name: str, *parents: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The parser of the command name, whose --help prints its paragraph of the
    docstring, then the one after usage, which applies to every command."""
    paragraphs = (__doc__ or "").split("\n\n")
    own = next(p for p in paragraphs if p.startswith(name + " "))
    return sub.add_parser(name, parents=list(parents), description=f"{own}\n\n{paragraphs[2]}",
                          formatter_class=argparse.RawDescriptionHelpFormatter)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.set_defaults(project=None)
    named = argparse.ArgumentParser(add_help=False)  # the options of every command but project
    named.add_argument("--project", metavar="P")
    replace = argparse.ArgumentParser(add_help=False)  # the options of project and experiment
    replace.add_argument("--replace", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    project = command(sub, "project", replace)
    project.add_argument("name")
    project.add_argument("--text", type=given_text)
    project.add_argument("--folder")
    project.add_argument("--files")
    project.add_argument("--views", nargs="*")
    project.set_defaults(run=run_project)
    exp = command(sub, "experiment", named, replace)
    exp.add_argument("name")
    exp.add_argument("--text", type=given_text)
    exp.add_argument("--in", dest="parent", metavar="PARENT")
    exp.add_argument("--dead-end", action=argparse.BooleanOptionalAction)
    exp.set_defaults(run=run_experiment)
    tree = command(sub, "tree", named)
    tree.add_argument("top", nargs="?", metavar="EXPERIMENT")
    tree.set_defaults(run=run_tree)
    add = command(sub, "add", named)
    add.add_argument("kind")
    add.add_argument("title")
    add.add_argument("--in", dest="container", required=True, metavar="EXPERIMENT")
    add.add_argument("--experiment")
    add.add_argument("--text", required=True, type=given_text)
    add.add_argument("--by", default="agent")
    add.add_argument("--status")
    add.add_argument("--answers", default="")
    add.add_argument("--evidence", nargs="*", default=[])
    add.add_argument("--id")
    add.add_argument("--time", type=datetime.datetime.fromisoformat)
    add.set_defaults(run=run_add)
    setp = command(sub, "set", named)
    setp.add_argument("id")
    setp.add_argument("assignments", nargs="+", metavar="FIELD=VALUE")
    setp.set_defaults(run=run_set)
    command(sub, "open", named).set_defaults(run=lambda lab, _: show_open(lab))
    run = command(sub, "run", named)
    run.add_argument("experiment")
    run.add_argument("name")
    run.add_argument("--note", default="")
    run.add_argument("--start", type=datetime.datetime.fromisoformat)
    run.set_defaults(run=run_run)
    end = command(sub, "end", named)
    end.add_argument("id", metavar="RUN")
    end.add_argument("--status", choices=("FINISHED", "FAILED", "KILLED"), default="FINISHED")
    end.add_argument("--at", type=datetime.datetime.fromisoformat)
    end.add_argument("--metric", nargs="+", action="extend", default=[], type=number, metavar="KEY=VALUE")
    end.add_argument("--tag", nargs="+", action="extend", default=[], type=pair, metavar="KEY=VALUE")
    end.add_argument("--file", nargs="+", action="extend", default=[], type=file_item, metavar="PATH[=NAME]")
    end.set_defaults(run=run_end)
    return p


def main() -> None:
    a = parser().parse_args()
    try:
        a.run(Lab(project=a.project), a)
    except (ValueError, OSError, sqlite3.Error) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
