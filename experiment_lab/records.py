"""Records: decisions, findings, protocol rules and questions, the rows of the
table records in lab.db. rec and any script add and change them here, so the
same rules hold: a record needs a text, a new question is open and any other
record closed unless a status is given, and a record that answers an open
question closes it. Evidence items are run:<run id>, file:<path>, sql:<query
over the views> or record:<id>, the run, record or file existing; a file: item
cites a copy of its file or folder (with_copies). Only the standard library, as
lab.py.
"""
import hashlib
import os
import re
import shutil
import sqlite3
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from experiment_lab.lab import BELOW, Lab, now, shown

KINDS = ("question", "decision", "protocol", "finding")  # in the order the pages show them
STATUSES = ("open", "closed", "superseded")
EVIDENCE = ("run", "file", "sql", "record")
ID_TABLES = {"run": "runs", "record": "records"}  # the table whose id an item of that kind names
COPIES = "evidence"  # the data folder's folder of the copies that file: items cite
LARGEST_COPY = 100 * 2**20  # bytes; a larger file or folder is cited where it is

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


class Rule(NamedTuple):
    id: str
    label: str
    title: str
    text: str


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
    """Refuses an item of no kind of EVIDENCE, a run: or record: item whose
    id no run or record has, and a file: item whose path does not exist."""
    for item in items:
        kind, _, value = item.partition(":")
        if kind not in EVIDENCE or not value:
            raise ValueError(f"evidence {item!r} is not one of {', '.join(k + ':...' for k in EVIDENCE)}")
        if kind in ID_TABLES and not lab.db.execute(f"SELECT 1 FROM {ID_TABLES[kind]} WHERE id = ?",
                                                     (value,)).fetchone():
            raise ValueError(f"evidence {item!r}: there is no {kind} {value}")
        if kind == "file" and not Path(value).expanduser().exists():
            raise ValueError(f"evidence {item!r}: there is no file {value}")


def with_copies(lab: Lab, items: Iterable[str]) -> Evidence:
    """items, each file: item of a file or folder outside the data folder,
    of at most LARGEST_COPY bytes, replaced by one citing its copy (copied),
    so that the record shows later what it cited."""
    return [f"file:{shown(copied(lab.data, path))}" if (path := outside(lab, item)) and size(path) <= LARGEST_COPY
            else item for item in items]


def uncopied(lab: Lab, items: Iterable[str]) -> list[str]:
    """The file: items that with_copies leaves as they are for their size:
    of a file or folder outside the data folder."""
    return [item for item in items if outside(lab, item)]


def outside(lab: Lab, item: str) -> Path | None:
    """The file or folder of a file: item, resolved, when it exists outside
    the data folder; else None."""
    if not item.startswith("file:"):
        return None
    path = Path(item.removeprefix("file:")).expanduser().resolve()
    return path if path.exists() and not path.is_relative_to(lab.data.resolve()) else None


def size(path: Path) -> int:
    """The bytes of the file, or of the files below the folder."""
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else path.stat().st_size


def copied(data: Path, source: Path) -> Path:
    """The copy of source, evidence/<digest>/<its name> in the data folder,
    made unless it exists: a file cited again unchanged keeps one copy, and a
    changed one gets its own. It is written under another name and renamed,
    so that a copy cut short is never taken for a whole one."""
    target = data / COPIES / digest(source) / source.name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f".{target.name}.{os.getpid()}")
        if source.is_dir():
            shutil.copytree(source, part)
        else:
            shutil.copy2(source, part)
        try:
            part.replace(target)
        except OSError:
            if not target.is_dir():
                raise
            shutil.rmtree(part)  # another process copied the same folder first
    return target


def digest(source: Path) -> str:
    """The first 16 hex digits of the SHA-256 of the file, or of the paths
    and contents of the files below the folder."""
    files = sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else [source]
    total = hashlib.sha256()
    for f in files:
        with f.open("rb") as stream:
            total.update(f.relative_to(source).as_posix().encode() + b"\0"
                         + hashlib.file_digest(stream, "sha256").digest())
    return total.hexdigest()[:16]


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


def protocol_rules(lab: Lab, project: str) -> list[Rule]:
    """The project's protocol records that are not superseded, oldest first."""
    return [Rule(*row) for row in lab.db.execute(
        f"{BELOW} SELECT id, label, title, text FROM records WHERE kind = 'protocol'"
        " AND status <> 'superseded' AND experiment IN below ORDER BY time", (project,))]


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
    it closed if any. text (Markdown) may not be empty. The id is chosen in
    the write, so that another writer cannot take it before the record is
    saved, which would replace that writer's record. A file: item of
    evidence is replaced by one citing a copy of its file (with_copies)."""
    lab.check_experiment(experiment)
    evidence = with_copies(lab, evidence)
    with lab.write() as db:
        question = answered(lab, answers)
        r = Record(id=free_id(lab, rid, title), experiment=experiment, label=label or experiment, kind=kind,
                   title=title, text=text, author=author,
                   status=status or ("open" if kind == "question" else "closed"), answers=answers,
                   evidence=list(evidence), time=time or now())
        check(lab, r)
        changed = [r]
        save(db, r)
        if question and question["status"] == "open":
            question["status"] = "closed"
            save(db, question)
            changed.append(question)
    return changed


def change(lab: Lab, rid: str, **fields: str | Evidence) -> Record:
    """Sets fields of the record, which must be in the project the lab works
    in (columns of records; evidence a list), and checks them, not the fields
    it keeps; returns it. The record is read in the write, so that a change
    by another writer is kept. A file: item of evidence is replaced by one
    citing a copy of its file (with_copies)."""
    if isinstance(evidence := fields.get("evidence"), list):
        fields = {**fields, "evidence": with_copies(lab, evidence)}
    with lab.write() as db:
        r = get(lab, rid)
        if r is None:
            raise ValueError(f"no record {rid}")
        lab.check_experiment(r["experiment"])
        r = as_record({**r, **fields})
        check(lab, r, fields)
        save(db, r)
    return r
