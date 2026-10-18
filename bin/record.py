# /// script
# requires-python = ">=3.12"
# ///
"""Adds or changes a decision, finding, protocol rule or question, kept as a
run of the local MLflow server, then prints the open records.

usage: rec add KIND "TITLE" --in EXPERIMENT [--experiment LABEL] [--text T] [--by WHO]
                            [--status S] [--answers ID] [--evidence ITEM ...] [--id ID] [--time T]
       rec set ID FIELD=VALUE ...
       rec open

A record is a run in the MLflow experiment named by --in, named
"<kind>: <title>", with its text as the run's note and the tags record.id,
record.kind, record.status, record.by, record.experiment (a label; the --in
experiment unless --experiment gives one), record.answers and record.evidence
(items joined by "; "). KIND is decision, finding, protocol or question. A new
question is open and any other record closed, unless --status says otherwise
(open, closed or superseded); a record added with --answers closes that
question. Evidence items are mlflow:<run id>, file:<path>, sql:<query over
lab.sql's views> or record:<id>. set changes kind, title, text, by, status,
experiment, answers or evidence (items separated by ";"). The server is
$MLFLOW_TRACKING_URI, else http://127.0.0.1:5000.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
KINDS = ("decision", "finding", "protocol", "question")
STATUSES = ("open", "closed", "superseded")
TAGS = {"id": "record.id", "kind": "record.kind", "status": "record.status", "by": "record.by",
        "experiment": "record.experiment", "answers": "record.answers", "evidence": "record.evidence",
        "text": "mlflow.note.content"}
SETTABLE = ("kind", "title", "text", "by", "status", "experiment", "answers", "evidence")


class MLflow:
    """Records as runs, through the server's REST API."""

    def __init__(self, url=URL):
        self.url = url.rstrip("/")

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.url}/api/2.0/mlflow/{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            sys.exit(f"error: MLflow {path}: {e.code} {e.read().decode(errors='replace')[:500]}")
        except urllib.error.URLError as e:
            sys.exit(f"error: no MLflow server at {self.url} ({e.reason}); start it with start.sh")

    def experiment_id(self, name):
        query = urllib.parse.urlencode({"experiment_name": name})
        return self.call("GET", f"experiments/get-by-name?{query}")["experiment"]["experiment_id"]

    def search(self, filter_string):
        ids = [e["experiment_id"] for e in
               self.call("POST", "experiments/search", {"max_results": 1000}).get("experiments", [])]
        records, token = [], None
        while True:
            body = {"experiment_ids": ids, "filter": filter_string, "max_results": 1000}
            if token:
                body["page_token"] = token
            reply = self.call("POST", "runs/search", body)
            records += [record(run) for run in reply.get("runs", [])]
            token = reply.get("next_page_token")
            if not token:
                return records

    def find(self, rid):
        found = self.search(f"tags.`record.id` = '{rid}'")
        return found[0] if found else None

    def create(self, r, experiment_id):
        run = self.call("POST", "runs/create", {"experiment_id": experiment_id, "run_name": name(r),
                                                "start_time": r["start"], "tags": tags(r)})
        r["run"] = run["run"]["info"]["run_id"]
        self.call("POST", "runs/update", {"run_id": r["run"], "status": "FINISHED", "end_time": r["start"]})

    def update(self, r):
        self.call("POST", "runs/log-batch", {"run_id": r["run"], "tags": tags(r)})
        self.call("POST", "runs/update", {"run_id": r["run"], "run_name": name(r)})


def record(run):
    t = {tag["key"]: tag["value"] for tag in run["data"].get("tags", [])}
    r = {field: t.get(key, "") for field, key in TAGS.items()}
    r["evidence"] = [item for item in r["evidence"].split("; ") if item]
    r["title"] = run["info"].get("run_name", "").removeprefix(f"{r['kind']}: ")
    r["run"], r["start"] = run["info"]["run_id"], int(run["info"]["start_time"])
    return r


def name(r):
    return f"{r['kind']}: {r['title']}"


def tags(r):
    return [{"key": key, "value": "; ".join(r[field]) if field == "evidence" else r[field] or ""}
            for field, key in TAGS.items()]


def new_id(title, server):
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].rstrip("-")
    rid, n = base, 2
    while server.find(rid):
        rid, n = f"{base}-{n}", n + 1
    return rid


def show(r):
    print(f"{r['id']}: {r['kind']}, {r['status']}: {r['title']}")


def show_open(server):
    rows = sorted(server.search("tags.`record.status` = 'open'"), key=lambda r: r["start"])
    print(f"open ({len(rows)}):")
    for r in rows:
        print(f"  {r['id']} [{r['experiment']}, {r['kind']}] {r['title']}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("kind", choices=KINDS)
    add.add_argument("title")
    add.add_argument("--in", dest="container", required=True, metavar="EXPERIMENT")
    add.add_argument("--experiment")
    add.add_argument("--text", default="")
    add.add_argument("--by", default="agent")
    add.add_argument("--status", choices=STATUSES)
    add.add_argument("--answers")
    add.add_argument("--evidence", nargs="*", default=[])
    add.add_argument("--id")
    add.add_argument("--time", default=datetime.datetime.now().isoformat(timespec="minutes"))
    setp = sub.add_parser("set")
    setp.add_argument("id")
    setp.add_argument("assignments", nargs="+", metavar="FIELD=VALUE")
    sub.add_parser("open")
    a = p.parse_args()
    server = MLflow()
    if a.command == "add":
        if a.id and not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.id):
            sys.exit(f"error: an id is lower-case letters, digits and dashes: {a.id}")
        if a.id and server.find(a.id):
            sys.exit(f"error: {a.id} exists; change it with set")
        question = server.find(a.answers) if a.answers else None
        if a.answers and not question:
            sys.exit(f"error: no record {a.answers}")
        r = {"id": a.id or new_id(a.title, server), "kind": a.kind, "title": a.title, "text": a.text, "by": a.by,
             "status": a.status or ("open" if a.kind == "question" else "closed"),
             "experiment": a.experiment or a.container, "answers": a.answers or "", "evidence": a.evidence,
             "start": int(datetime.datetime.fromisoformat(a.time).timestamp() * 1000)}
        server.create(r, server.experiment_id(a.container))
        show(r)
        if question and question["status"] == "open":
            question["status"] = "closed"
            server.update(question)
            show(question)
    elif a.command == "set":
        r = server.find(a.id)
        if not r:
            sys.exit(f"error: no record {a.id}")
        for assignment in a.assignments:
            field, _, value = assignment.partition("=")
            if field not in SETTABLE:
                sys.exit(f"error: cannot set {field}; fields: {', '.join(SETTABLE)}")
            if field == "kind" and value not in KINDS or field == "status" and value not in STATUSES:
                sys.exit(f"error: {field} is one of {', '.join(KINDS if field == 'kind' else STATUSES)}")
            r[field] = [v.strip() for v in value.split(";") if v.strip()] if field == "evidence" else value
        server.update(r)
        show(r)
    show_open(server)


if __name__ == "__main__":
    main()
