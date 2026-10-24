"""The /lab pages: records with their evidence, experiments and their runs,
a search of their texts, the views as tables, a query box, and the files of
the folder lab.toml names.
lab-server serves them with uvicorn at http://127.0.0.1:$EXPERIMENT_LAB_PORT
(default 5000).

The pages only read. They keep one DuckDB database with lab.db attached
read-only and the views, made again when lab.sql, lab.toml or a view file
changes; each request reads through a cursor of its own, with file access off.
SQL from the query box and from sql: evidence runs in a transaction that is
rolled back, so nothing it makes is kept.
"""
import mimetypes
import os
import subprocess
import sys
import threading
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
from experiment_lab.settings import data_folder, database, settings

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
    """The DuckDB database the pages read."""

    def __init__(self, data):
        self.data = data
        self.lock = threading.Lock()
        self.con = None
        self.version = None

    def cursor(self):
        with self.lock:
            files = [self.data / "lab.toml", *query.view_files(self.data)]
            version = tuple(p.stat().st_mtime_ns if p.exists() else 0 for p in files)
            if self.con is None or version != self.version:
                con = query.connect(self.data, keep_going=False)
                con.execute("SET enable_external_access = false")
                con.execute("SET lock_configuration = true")
                self.con, self.version = con, version
            return self.con.cursor()


def cursor(request: Request):
    con = request.app.state.views.cursor()
    try:
        yield con
    finally:
        con.close()


def page(request, template, status_code=200, **context):
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


def all_records(con):
    """Every record, with its evidence as a list; experiment is its label and
    filed_in the experiment it is in."""
    rows = con.execute("SELECT r.*, l.experiment AS filed_in FROM records r JOIN lab.records l USING (id)"
                       " ORDER BY r.time, r.id")
    columns = [d[0] for d in rows.description]
    out = []
    for values in rows.fetchall():
        r = dict(zip(columns, values))
        r["evidence"] = records.split(r["evidence"])
        out.append(r)
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
    @app.exception_handler(ValueError)  # lab.toml
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
        rs = all_records(con)
        groups = {}
        for r in rs:
            groups.setdefault(r["filed_in"], {}).setdefault(r["experiment"], []).append(r)
        for labels in groups.values():
            for rows in labels.values():
                rows.sort(key=lambda r: (KIND_ORDER.get(r["kind"], 9), r["time"]))
        return page(request, "index", open=[r for r in rs if r["status"] == "open"], groups=sorted(groups.items()))

    @app.get("/lab/experiments")
    def experiments(request: Request, con=Depends(cursor)):
        rows = con.execute(f"""SELECT e.name, count(r.id) AS runs,
                (SELECT count(*) FROM lab.records c WHERE c.experiment = e.name) AS records,
                {TIME.format("max(r.start_time)")} AS last_run
            FROM lab.experiments e LEFT JOIN lab.runs r ON r.experiment = e.name
            GROUP BY e.name ORDER BY e.name""").fetchall()
        return page(request, "experiments", rows=rows)

    @app.get("/lab/experiment/{name:path}")
    def experiment(request: Request, name: str, con=Depends(cursor)):
        found = con.execute("SELECT description FROM lab.experiments WHERE name = ?", [name]).fetchone()
        if found is None:
            raise HTTPException(404, f"no experiment {name}")
        result = con.execute(f"""SELECT run, name, status, {TIME.format("start_time")} AS started,
                round((end_time - start_time) / 1000, 1) AS seconds, params
            FROM runs WHERE experiment = ? ORDER BY start_time DESC""", [name])
        t = query.table(result, ROWS, CUT)
        rs = [r for r in all_records(con) if r["filed_in"] == name]
        return page(request, "experiment", name=name, description=md(found[0]), t=t, records=rs)

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
        for item in r["evidence"]:
            kind, _, value = item.partition(":")
            e = {"kind": kind, "value": value}
            if kind == "sql":
                e["table"] = run_sql(con, value, EVIDENCE_ROWS)
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

    @app.get("/lab/search")
    def search(request: Request, find: str = "", con=Depends(cursor)):
        """The experiments, runs and records whose title and text (the view
        texts) hold every word of find, ignoring case."""
        words = find.lower().split()
        where = " AND ".join(["contains(lower(title || chr(10) || text), ?)"] * len(words)) or "false"
        found = con.execute(f"SELECT kind, id, experiment, title, text FROM texts WHERE {where}"
                            f" ORDER BY kind, experiment, title LIMIT {ROWS + 1}", words).fetchall()
        hits = [{"kind": kind, "id": i, "experiment": experiment, "title": title, "snippet": snippet(text, words)}
                for kind, i, experiment, title, text in found[:ROWS]]
        return page(request, "search", find=find, hits=hits, more=len(found) > ROWS)

    @app.get("/lab/query")
    def query_page(request: Request, sql: str = "", con=Depends(cursor)):
        result = run_sql(con, sql, cut=None) if sql.strip() else None
        return page(request, "query", sql=sql, result=result, views=query.view_columns(con))

    @app.get("/lab/file")
    def file_page(request: Request, path: str = "", raw: str = ""):
        top = settings(data)[0]
        if top is None:
            raise HTTPException(404, "no files are shown: lab.toml, next to lab.db, names no files folder")
        target = Path(os.path.expanduser(path or str(top))).resolve()
        if not target.is_relative_to(top):
            raise HTTPException(403, f"only files under {shown_path(top)} are shown")
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
