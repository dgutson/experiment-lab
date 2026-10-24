"""The /lab pages, each of one project, picked at the top of every page: the
project's records with their evidence, its experiments as a tree with a search
of their texts, the views as tables, a query box, and the project's files.
lab-server serves them with uvicorn at http://127.0.0.1:$EXPERIMENT_LAB_PORT
(default 5000).

The pages only read. They keep one DuckDB database with lab.db attached
read-only and the views, made again when lab.sql, a view file or a project's
list of view files changes; each request reads through a cursor of its own,
with file access off. A statement of a view file that fails is skipped, and
its error shown on the records page and the view list. SQL from the query box
and from sql: evidence runs in a transaction that is rolled back, so nothing
it makes is kept.
"""
import contextlib
import mimetypes
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import duckdb
import markdown
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from experiment_lab import query, records
from experiment_lab.settings import data_folder, database

HERE = Path(__file__).parent
ROWS = 1000  # rows shown per table
EVIDENCE_ROWS = 200
CUT = 300  # characters shown per cell, except in the query box
KIND_ORDER = {"question": 0, "decision": 1, "protocol": 2, "finding": 3}
TEXT_TYPES = ("application/json", "application/javascript", "application/xml", "application/sql",
              "application/x-sh", "application/toml")
# statements that would act outside the transaction that is rolled back
REFUSED = (duckdb.StatementType.TRANSACTION, duckdb.StatementType.ATTACH, duckdb.StatementType.DETACH)
TIME = "strftime(to_timestamp({} / 1000), '%Y-%m-%d %H:%M')"

templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["cell"] = query.cell


class Views:
    """The DuckDB databases the pages read, one per project (None: lab.sql's
    views only), each with the sources of its views (query.make_views)."""

    def __init__(self, data):
        self.data = data
        self.lock = threading.Lock()
        self.made = {}

    def cursor(self, project):
        with self.lock:
            con, sources = self.made.get(project, (None, []))
            if con is None or query.version(con, project) != [(s["path"], s["mtime"]) for s in sources]:
                con, sources = query.connect(self.data, project)
                con.execute("SET enable_external_access = false")
                con.execute("SET lock_configuration = true")
                self.made[project] = con, sources
            return con.cursor()

    def errors(self, project):
        return query.errors(self.made[project][1])


def cursor(request: Request):
    """A cursor of the picked project's database. It also sets
    request.state.projects and request.state.project: the one picked, kept in
    the cookie project, else the first."""
    views = request.app.state.views
    with contextlib.closing(views.cursor(None)) as con:
        projects = [name for name, _ in query.projects(con)]
    picked = urllib.parse.unquote(request.cookies.get("project", ""))
    request.state.projects = projects
    request.state.project = picked if picked in projects else next(iter(projects), None)
    con = views.cursor(request.state.project)
    try:
        yield con
    finally:
        con.close()


def page(request, template, status_code=200, **context):
    context = {"projects": getattr(request.state, "projects", []), "project": getattr(request.state, "project", None),
               **context}
    return templates.TemplateResponse(request, f"lab/{template}.html", context, status_code=status_code)


def run_sql(con, sql, limit=ROWS, cut: int | None = CUT):
    """The last statement's rows as a table, or the error text."""
    try:
        if any(s.type in REFUSED for s in con.extract_statements(sql)):
            return {"error": "BEGIN, COMMIT, ROLLBACK, ATTACH and DETACH are not run here"}
        con.begin()
        try:
            result = query.run(con, sql)
            return {"error": "the statement returns no rows"} if result is None else query.table(result, limit, cut)
        finally:
            con.rollback()
    except (duckdb.Error, ValueError) as e:
        return {"error": str(e)}


def md(text):
    return Markup(markdown.markdown(text or "", extensions=["tables", "fenced_code", "sane_lists"]))


def all_records(con, project=None):
    """Every record, or the project's, with its evidence as a list; experiment
    is its label and filed_in the experiment it is in."""
    where = " WHERE r.project = ?" if project else ""
    rows = con.execute("SELECT r.*, l.experiment AS filed_in FROM records r JOIN lab.records l USING (id)"
                       f"{where} ORDER BY r.time, r.id", [project] if project else [])
    columns = [d[0] for d in rows.description]
    out = []
    for values in rows.fetchall():
        r = dict(zip(columns, values))
        r["evidence"] = records.split(r["evidence"])
        out.append(r)
    return out


def duration(ms):
    """A time span in milliseconds as text: < 1 min, 12 min, 26 h 53 min."""
    m = int(ms // 60000)
    return "< 1 min" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def covered(spans):
    """The time during which at least one of the (start, end) spans runs."""
    total = end = 0
    for s, e in sorted(spans):
        total += max(0, e - max(s, end))
        end = max(end, e)
    return total


def tree(con, project):
    """The project's experiments, each followed by those below it, siblings in
    the order they were made. Each is a dict: name, parent, dead_end, records
    (how many), started (when its first run started, or with no runs when it
    was made), running (whether a run has not ended), processing (the sum of
    its ended runs' durations) and clock (the time during which at least one of
    them ran) as text, empty if 0, above (the names from the project down to
    its parent) and branch (the lines that draw the tree before its name). The
    project's records, started, running, processing and clock are those of the
    whole project."""
    result = con.execute(f"""WITH r AS (SELECT experiment, min(start_time) AS t0,
                 bool_or(status = 'RUNNING') AS running FROM lab.runs GROUP BY ALL),
             c AS (SELECT experiment, count(*) AS n FROM lab.records GROUP BY ALL),
             own AS (SELECT e.name, e.parent, e.dead_end, e.created, coalesce(c.n, 0) AS records,
                 coalesce(r.t0, e.created) AS t0, coalesce(r.running, false) AS running
             FROM lab.experiments e JOIN experiments x USING (name)
             LEFT JOIN r ON r.experiment = e.name LEFT JOIN c ON c.experiment = e.name WHERE x.project = ?),
             shown AS (SELECT name, parent, dead_end, created,
                 if(parent IS NULL, sum(records) OVER (), records) AS records,
                 if(parent IS NULL, min(t0) OVER (), t0) AS t0,
                 if(parent IS NULL, bool_or(running) OVER (), running) AS running FROM own)
        SELECT name, parent, dead_end, records, {TIME.format("t0")} AS started, running
        FROM shown ORDER BY created, name""", [project])
    columns, rows = [d[0] for d in result.description], result.fetchall()
    spans = {}
    for name, start, end in con.execute("SELECT r.experiment, r.start_time, r.end_time FROM lab.runs r JOIN experiments x"
                                        " ON x.name = r.experiment WHERE x.project = ? AND r.end_time IS NOT NULL",
                                        [project]).fetchall():
        spans.setdefault(name, []).append((start, end))
    children = {}
    for values in rows:
        e = dict(zip(columns, values))
        own = spans.get(e["name"], []) if e["parent"] else [s for ss in spans.values() for s in ss]
        processing, clock = sum(end - start for start, end in own), covered(own)
        e["processing"], e["clock"] = duration(processing) if processing else "", duration(clock) if clock else ""
        children.setdefault(e["parent"], []).append(e)
    out = []

    def walk(e, above, prefix, last):
        e["above"], e["branch"] = above, prefix + ("└─ " if last else "├─ ") if above else ""
        out.append(e)
        below = children.get(e["name"], [])
        inner = prefix + ("   " if last else "│  ") if above else ""
        for i, child in enumerate(below):
            walk(child, above + [e["name"]], inner, i == len(below) - 1)

    for root in children.get(None, []):
        walk(root, [], "", True)
    return out


def snippet(text, words, width=240):
    """About width characters of text, from a little before the first of words in it."""
    at = min([i for i in (text.lower().find(w) for w in words) if i >= 0], default=0)
    start = max(0, at - width // 4)
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")


def send(path):
    """The file, as text unless it is a page or binary; a page is shown with scripts off."""
    kind = mimetypes.guess_type(path.name)[0]
    if kind is None:
        with open(path, "rb") as f:
            kind = "application/octet-stream" if b"\0" in f.read(4096) else "text/plain"
    elif kind in TEXT_TYPES or kind.startswith("text/") and kind != "text/html":
        kind = "text/plain"
    headers = {"Content-Security-Policy": "sandbox"} if kind == "text/html" else None
    return FileResponse(path, media_type=kind, headers=headers)


def shown_path(path):
    """path as ~/... under the home folder, else whole."""
    return "~/" + str(path.relative_to(Path.home())) if path.is_relative_to(Path.home()) else str(path)


def create_app(data):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.views = Views(data)
    app.mount("/lab/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(HTTPException)
    def http_error(request: Request, e: HTTPException):
        return page(request, "error", e.status_code, message=e.detail)

    @app.exception_handler(duckdb.Error)
    @app.exception_handler(FileNotFoundError)
    def database_error(request: Request, e: Exception):
        return page(request, "error", 500, message=str(e))

    @app.get("/")
    def root():
        return RedirectResponse("/lab/")

    @app.get("/lab/health")
    def health():
        return PlainTextResponse("OK")

    @app.get("/lab/")
    def index(request: Request, con=Depends(cursor)):
        project = request.state.project
        rs = all_records(con, project)
        by = {}
        for r in rs:
            by.setdefault(r["filed_in"], {}).setdefault(r["experiment"], []).append(r)
        for labels in by.values():
            for rows in labels.values():
                rows.sort(key=lambda r: (KIND_ORDER.get(r["kind"], 9), r["time"]))
        return page(request, "index", open=[r for r in rs if r["status"] == "open"],
                    groups=[(e, by[e["name"]]) for e in tree(con, project) if e["name"] in by],
                    errors=request.app.state.views.errors(project))

    @app.get("/lab/experiments")
    def experiments(request: Request, node: str = "", find: str = "", below: str = "", con=Depends(cursor)):
        """The project's experiments as a tree, and those of its experiments,
        runs and records whose title and text (the view texts) hold every word
        of find, ignoring case: below node when below is given, else in the
        whole project."""
        nodes = tree(con, request.state.project)
        node = node if node in [e["name"] for e in nodes] else request.state.project
        words = find.lower().split()
        hits, more = None, False
        if words:
            under = [e["name"] for e in nodes if not below or e["name"] == node or node in e["above"]]
            where = " AND ".join(["contains(lower(title || chr(10) || text), ?)"] * len(words))
            found = con.execute("SELECT kind, id, experiment, title, text FROM texts"
                                f" WHERE experiment IN (SELECT unnest(?::VARCHAR[])) AND {where}"
                                f" ORDER BY kind, experiment, title LIMIT {ROWS + 1}", [under, *words]).fetchall()
            hits = [{"kind": kind, "id": i, "experiment": experiment, "title": title, "snippet": snippet(text, words)}
                    for kind, i, experiment, title, text in found[:ROWS]]
            more = len(found) > ROWS
        return page(request, "experiments", nodes=nodes, node=node, find=find, below=bool(below) or not words,
                    hits=hits, more=more)

    @app.get("/lab/experiment/{name:path}")
    def experiment(request: Request, name: str, con=Depends(cursor)):
        found = con.execute("SELECT description, parent, dead_end, folder, files, views FROM lab.experiments"
                            " WHERE name = ?", [name]).fetchone()
        if found is None:
            raise HTTPException(404, f"no experiment {name}")
        description, parent, dead_end, folder, files, views = found
        above = []
        while parent is not None:
            above.insert(0, parent)
            parent = (con.execute("SELECT parent FROM lab.experiments WHERE name = ?", [parent]).fetchone() or [None])[0]
        branches = con.execute("SELECT name, dead_end FROM lab.experiments WHERE parent = ? ORDER BY created, name",
                               [name]).fetchall()
        result = con.execute(f"""SELECT run, name, status, {TIME.format("start_time")} AS started,
                round((end_time - start_time) / 1000, 1) AS seconds, params
            FROM runs WHERE experiment = ? ORDER BY start_time DESC""", [name])
        t = query.table(result, ROWS, CUT)
        rs = [r for r in all_records(con) if r["filed_in"] == name]
        project = folder and {"folder": shown_path(Path(folder)), "files": shown_path(Path(files)),
                              "views": [shown_path(Path(v)) for v in views.split("; ") if v]}
        return page(request, "experiment", name=name, description=md(description), dead_end=dead_end, above=above,
                    branches=branches, project_settings=project, t=t, records=rs)

    @app.get("/lab/run/{rid}")
    def run_page(request: Request, rid: str, con=Depends(cursor)):
        result = con.execute(f"SELECT *, {TIME.format('start_time')} AS started, {TIME.format('end_time')} AS ended"
                             " FROM runs WHERE run = ?", [rid])
        row = result.fetchone()
        if row is None:
            raise HTTPException(404, f"no run {rid}")
        r = dict(zip([d[0] for d in result.description], row))
        folder = data / "files" / rid
        files = sorted((str(p.relative_to(folder)), p.stat().st_size) for p in folder.rglob("*") if p.is_file())
        cited_by = [x for x in all_records(con) if f"run:{rid}" in x["evidence"]]
        return page(request, "run", r=r, note=md(r["note"]), files=files, cited_by=cited_by,
                    maps=[(title, r[key] or {}) for title, key in
                          (("Params", "params"), ("Latest metrics", "metrics"), ("Tags", "tags"))])

    @app.get("/lab/run/{rid}/file/{path:path}")
    def run_file(rid: str, path: str):
        folder = (data / "files" / rid).resolve()
        target = (folder / path).resolve()
        if not target.is_relative_to(folder) or not target.is_file():
            raise HTTPException(404, f"no file {path} of run {rid}")
        return send(target)

    @app.get("/lab/record/{rid}")
    def record_page(request: Request, rid: str, con=Depends(cursor)):
        rs = {r["id"]: r for r in all_records(con)}
        if rid not in rs:
            raise HTTPException(404, f"no record {rid}")
        r = rs[rid]
        evidence = []
        # sql: evidence reads the views of the record's project
        with contextlib.closing(request.app.state.views.cursor(r["project"])) as own:
            for item in r["evidence"]:
                kind, _, value = item.partition(":")
                e = {"kind": kind, "value": value}
                if kind == "sql":
                    e["table"] = run_sql(own, value, EVIDENCE_ROWS)
                elif kind == "run":
                    found = con.execute("SELECT name, experiment FROM lab.runs WHERE id = ?", [value]).fetchone()
                    e["label"] = f"{found[0]} ({found[1]})" if found else None
                elif kind == "record":
                    e["label"] = rs[value]["title"] if value in rs else None
                evidence.append(e)
        answered_by = [x for x in rs.values() if x["answers"] == rid]
        cited_by = [x for x in rs.values() if f"record:{rid}" in x["evidence"]]
        return page(request, "record", r=r, text=md(r["text"]), evidence=evidence, answers=rs.get(r["answers"]),
                    answered_by=answered_by, cited_by=cited_by)

    @app.get("/lab/view/{name}")
    def view_page(request: Request, name: str, con=Depends(cursor)):
        if name not in [v for v, _ in query.view_columns(con)]:
            raise HTTPException(404, f"no view {name}")
        return page(request, "view", name=name, t=query.table(con.execute(f'FROM "{name}"'), ROWS, CUT))

    @app.get("/lab/query")
    def query_page(request: Request, sql: str = "", con=Depends(cursor)):
        result = run_sql(con, sql, cut=None) if sql.strip() else None
        return page(request, "query", sql=sql, result=result, views=query.view_columns(con),
                    errors=request.app.state.views.errors(request.state.project))

    @app.get("/lab/file")
    def file_page(request: Request, path: str = "", raw: str = "", con=Depends(cursor)):
        """A file or folder under a project's files folder; without path, the picked project's."""
        folders = dict(con.execute("SELECT name, files FROM lab.experiments WHERE parent IS NULL").fetchall())
        if not folders:
            raise HTTPException(404, "no project yet, so no files folder: rec project makes one")
        target = Path(os.path.expanduser(path or folders[request.state.project])).resolve()
        tops = [Path(f) for f in folders.values() if target.is_relative_to(f)]
        if not tops:
            raise HTTPException(403, "only files under the projects' files folders are shown")
        top = min(tops, key=lambda f: len(f.parts))
        if not target.exists():
            raise HTTPException(404, f"no file {target}")
        shown = shown_path(target)
        if target.is_dir():
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            items = [{"name": p.name + ("/" if p.is_dir() else ""), "path": f"{shown}/{p.name}",
                      "size": p.stat().st_size if p.is_file() else ""} for p in entries]
            parent = shown_path(target.parent) if target != top else None
            return page(request, "folder", shown=shown, items=items, parent=parent)
        if target.suffix == ".md" and not raw:
            return page(request, "markdown", shown=shown, text=md(target.read_text(errors="replace")))
        return send(target)

    return app


def main():
    data = data_folder()
    if not database(data).exists():  # made in another process: this one reads lab.db with DuckDB only
        subprocess.run([sys.executable, "-m", "experiment_lab.lab"], check=True)
    uvicorn.run(create_app(data), host="127.0.0.1", port=int(os.environ.get("EXPERIMENT_LAB_PORT", "5000")),
                log_level="warning")
