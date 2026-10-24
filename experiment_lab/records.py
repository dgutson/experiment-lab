"""Records: decisions, findings, protocol rules and questions, the rows of the
table records in lab.db. rec and any script add and change them here, so the
same rules hold: a record needs a text, a new question is open and any other
record closed unless a status is given, and a record that answers an open
question closes it. Evidence items are run:<run id>, file:<path>, sql:<query
over the views> or record:<id>. Only the standard library, as lab.py.
"""
import re

from experiment_lab.lab import BELOW, now

EVIDENCE = ("run", "file", "sql", "record")


def split(evidence):
    return [item for item in (evidence or "").split("; ") if item]


def check(r, fields=("text", "evidence")):
    """Checks those of fields that are text (not empty) and evidence (items of EVIDENCE)."""
    if "text" in fields and not r["text"].strip():
        raise ValueError(f"a record needs a text: {r['title']}")
    for item in r["evidence"] if "evidence" in fields else []:
        kind, _, value = item.partition(":")
        if kind not in EVIDENCE or not value:
            raise ValueError(f"evidence {item!r} is not one of {', '.join(k + ':...' for k in EVIDENCE)}")


def get(lab, rid):
    """The record as a dict, its evidence a list; None if there is none."""
    cursor = lab.db.execute("SELECT * FROM records WHERE id = ?", (rid,))
    row = cursor.fetchone()
    if row is None:
        return None
    r = dict(zip([d[0] for d in cursor.description], row))
    r["evidence"] = split(r["evidence"])
    return r


def open_records(lab, project=None):
    """The open records, only those of the project when one is given."""
    return lab.db.execute(f"{BELOW} SELECT id, label, kind, title FROM records WHERE status = 'open'"
                          " AND (? IS NULL OR experiment IN below) ORDER BY time", (project, project)).fetchall()


def new_id(lab, title):
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-")
    rid, n = base, 2
    while get(lab, rid):
        rid, n = f"{base}-{n}", n + 1
    return rid


def save(db, r):
    db.execute("INSERT OR REPLACE INTO records (id, experiment, label, kind, title, text, author, status, answers,"
               " evidence, time) VALUES (:id, :experiment, :label, :kind, :title, :text, :author, :status, :answers,"
               " :evidence, :time)", {**r, "evidence": "; ".join(r["evidence"])})


def add(lab, experiment, kind, title, *, text, label=None, author="agent", status=None, answers="",
        evidence=(), rid=None, time=None):
    """Adds a record to an existing experiment; returns it, and the question
    it closed if any. text (Markdown) may not be empty."""
    if rid and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rid):
        raise ValueError(f"an id is lower-case letters, digits and dashes: {rid}")
    if rid and get(lab, rid):
        raise ValueError(f"{rid} exists; change it instead")
    question = get(lab, answers) if answers else None
    if answers and not question:
        raise ValueError(f"no record {answers}")
    lab.check_experiment(experiment)
    r = {"id": rid or new_id(lab, title), "experiment": experiment, "label": label or experiment, "kind": kind,
         "title": title, "text": text, "author": author,
         "status": status or ("open" if kind == "question" else "closed"), "answers": answers,
         "evidence": list(evidence), "time": time or now()}
    check(r)
    changed = [r]
    with lab.write() as db:
        save(db, r)
        if question and question["status"] == "open":
            question["status"] = "closed"
            save(db, question)
            changed.append(question)
    return changed


def change(lab, rid, **fields):
    """Sets fields of the record (columns of records; evidence a list) and
    checks them, not the fields it keeps; returns it."""
    r = get(lab, rid)
    if r is None:
        raise ValueError(f"no record {rid}")
    r.update(fields)
    check(r, fields)
    with lab.write() as db:
        save(db, r)
    return r
