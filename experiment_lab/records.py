"""Records: decisions, findings, protocol rules and questions, the rows of the
table records in lab.db. rec and any script add and change them here, so the
same rules hold: a record needs a text, a new question is open and any other
record closed unless a status is given, and a record that answers an open
question closes it. Evidence items are run:<run id>, file:<path>, sql:<query
over the views> or record:<id>, the run or record existing. Only the standard
library, as lab.py.
"""
import re
import sqlite3
import subprocess
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from experiment_lab.lab import BELOW, Lab, now

KINDS = ("question", "decision", "protocol", "finding")  # in the order the pages show them
STATUSES = ("open", "closed", "superseded")
EVIDENCE = ("run", "file", "sql", "record")
ID_TABLES = {"run": "runs", "record": "records"}  # the table whose id an item of that kind names

type Evidence = list[str]


class Record(TypedDict):
    """A row of records, its evidence a list."""
    id: str
    experiment: str
    label: str
    kind: str
    title: str
    text: str
    author: str
    status: str
    answers: str
    evidence: Evidence
    time: int


class OpenRecord(NamedTuple):
    id: str
    label: str
    kind: str
    title: str


def split(evidence: str | None) -> Evidence:
    return [item for item in (evidence or "").split("; ") if item]


def as_record(r: Mapping[str, Any]) -> Record:
    """A row of records, its evidence a list, or a record with fields changed,
    as a Record; a KeyError if a key is missing."""
    return Record(id=r["id"], experiment=r["experiment"], label=r["label"], kind=r["kind"], title=r["title"],
                  text=r["text"], author=r["author"], status=r["status"], answers=r["answers"],
                  evidence=r["evidence"], time=r["time"])


def check(lab: Lab, r: Record, fields: Collection[str] = ("kind", "status", "text", "evidence")) -> None:
    """Checks those of fields that are kind (one of KINDS), status (one of
    STATUSES), text (not empty) and evidence (items of EVIDENCE, a run or
    record that exists)."""
    for field, value, allowed in (("kind", r["kind"], KINDS), ("status", r["status"], STATUSES)):
        if field in fields and value not in allowed:
            raise ValueError(f"{field} {value!r} is not one of {', '.join(allowed)}")
    if "text" in fields and not r["text"].strip():
        raise ValueError(f"a record needs a text: {r['title']}")
    if "evidence" in fields:
        check_evidence(lab, r["evidence"])


def check_evidence(lab: Lab, items: Iterable[str]) -> None:
    """Refuses an item of no kind of EVIDENCE, and a run: or record: item
    whose id no run or record has."""
    for item in items:
        kind, _, value = item.partition(":")
        if kind not in EVIDENCE or not value:
            raise ValueError(f"evidence {item!r} is not one of {', '.join(k + ':...' for k in EVIDENCE)}")
        if kind in ID_TABLES and not lab.db.execute(f"SELECT 1 FROM {ID_TABLES[kind]} WHERE id = ?",
                                                     (value,)).fetchone():
            raise ValueError(f"evidence {item!r}: there is no {kind} {value}")


def in_linked_worktree(items: Iterable[str]) -> list[str]:
    """The file: items under a linked git worktree (one that git worktree add
    made, not the repository's main one), where a later checkout can change the
    file."""
    return [item for item in items if item.startswith("file:") and linked(Path(item[5:]).expanduser())]


def linked(path: Path) -> bool:
    """Whether path, or the nearest folder above it that exists, is in a
    linked git worktree: one whose git folder is not the repository's."""
    folder = next(p for p in (path, *path.parents) if p.is_dir())
    try:
        git = subprocess.run(["git", "-C", str(folder), "rev-parse", "--path-format=absolute", "--git-dir",
                              "--git-common-dir"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    own, common = git.stdout.splitlines()
    return own != common


def get(lab: Lab, rid: str) -> Record | None:
    """The record; None if there is none."""
    cursor = lab.db.execute("SELECT * FROM records WHERE id = ?", (rid,))
    row = cursor.fetchone()
    if row is None:
        return None
    r = dict(zip([d[0] for d in cursor.description], row))
    return as_record({**r, "evidence": split(r["evidence"])})


def open_records(lab: Lab, project: str | None = None) -> list[OpenRecord]:
    """The open records, only those of the project when one is given."""
    return [OpenRecord(*row) for row in lab.db.execute(
        f"{BELOW} SELECT id, label, kind, title FROM records WHERE status = 'open'"
        " AND (? IS NULL OR experiment IN below) ORDER BY time", (project, project))]


def free_id(lab: Lab, rid: str | None, title: str) -> str:
    """rid, which must be lower-case letters, digits and dashes and not taken;
    without it, an id made from the title, cut at the last word that ends
    within 40 characters, that is not taken."""
    if rid:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rid):
            raise ValueError(f"an id is lower-case letters, digits and dashes: {rid}")
        if get(lab, rid):
            raise ValueError(f"{rid} exists; change it instead")
        return rid
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    words = re.match(r".{1,40}(?=-|$)", slug)  # a first word of over 40 characters is cut inside
    base = words[0] if words else slug[:40]
    rid, n = base, 2
    while get(lab, rid):
        rid, n = f"{base}-{n}", n + 1
    return rid


def answered(lab: Lab, answers: str) -> Record | None:
    """The question that answers names, which must exist; None if it names none."""
    question = get(lab, answers) if answers else None
    if answers and not question:
        raise ValueError(f"no record {answers}")
    return question


def save(db: sqlite3.Connection, r: Record) -> None:
    db.execute("INSERT OR REPLACE INTO records (id, experiment, label, kind, title, text, author, status, answers,"
               " evidence, time) VALUES (:id, :experiment, :label, :kind, :title, :text, :author, :status, :answers,"
               " :evidence, :time)", {**r, "evidence": "; ".join(r["evidence"])})


def add(lab: Lab, experiment: str, kind: str, title: str, *, text: str, label: str | None = None,
        author: str = "agent", status: str | None = None, answers: str = "", evidence: Iterable[str] = (),
        rid: str | None = None, time: int | None = None) -> list[Record]:
    """Adds a record to an existing experiment; returns it, and the question
    it closed if any. text (Markdown) may not be empty."""
    lab.check_experiment(experiment)
    rid = free_id(lab, rid, title)
    question = answered(lab, answers)
    r = Record(id=rid, experiment=experiment, label=label or experiment, kind=kind, title=title, text=text,
               author=author, status=status or ("open" if kind == "question" else "closed"), answers=answers,
               evidence=list(evidence), time=time or now())
    check(lab, r)
    changed = [r]
    with lab.write() as db:
        save(db, r)
        if question and question["status"] == "open":
            question["status"] = "closed"
            save(db, question)
            changed.append(question)
    return changed


def change(lab: Lab, rid: str, **fields: str | Evidence) -> Record:
    """Sets fields of the record, which must be in the project the lab works
    in (columns of records; evidence a list), and checks them, not the fields
    it keeps; returns it."""
    r = get(lab, rid)
    if r is None:
        raise ValueError(f"no record {rid}")
    lab.check_experiment(r["experiment"])
    r = as_record({**r, **fields})
    check(lab, r, fields)
    with lab.write() as db:
        save(db, r)
    return r
