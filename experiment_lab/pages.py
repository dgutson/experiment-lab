"""The /lab pages. Four show the project picked at the top of them: its records
with their evidence, its experiments as a tree with a search of their texts, its
views with a query box, and its files folder. The pages they link to, of an
experiment, a run, a record, a view or a file under the projects' folders and
files folders, show the same whatever is picked, and have no picker.
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
import re
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, Required, TypedDict
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

import duckdb
import markdown
import uvicorn
from duckdb import DuckDBPyConnection
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown.treeprocessors import Treeprocessor
from markupsafe import Markup

from experiment_lab import query, records
from experiment_lab.settings import data_folder, database

HERE = Path(__file__).parent
ROWS = 1000  # rows shown per table
EVIDENCE_ROWS = 200
CUT = 300  # characters shown per cell, except in the query box
LONGEST_FIND, LONGEST_SQL = 200, 5000  # characters of a search and of a query from the query box
TEXT_TYPES = ("application/json", "application/javascript", "application/xml", "application/sql",
              "application/x-sh", "application/toml")
# statements that would act outside the transaction that is rolled back
REFUSED = (duckdb.StatementType.TRANSACTION, duckdb.StatementType.ATTACH, duckdb.StatementType.DETACH)
TIME = "strftime(to_timestamp({} / 1000), '%Y-%m-%d %H:%M')"
# a ~/ or absolute path in a text, after the start, a space, a parenthesis or a quote; a . , ; : ! or ?
# at its end is not part of it
PATH = re.compile(r"""(?<![^\s("'])~?/[^\s()<>"'`\[\]]*[^\s()<>"'`\[\].,;:!?]""")

templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["cell"] = query.cell

type Span = tuple[int, int]  # a run's start and end, in milliseconds
type Spans = list[Span]


class Failure(TypedDict):
    error: str


class Shown(query.Table):
    """A table as a page shows it, with the sentence under it."""
    note: str


class Listed(TypedDict):
    """A row of the view records, its evidence a list, and filed_in, the
    experiment it is in (experiment is the label it is shown under)."""
    id: str
    time: str
    project: str
    experiment: str
    kind: str
    title: str
    text: str
    author: str
    status: str
    answers: str
    evidence: list[str]
    filed_in: str


type Listing = list[Listed]
type Labeled = dict[str, Listing]  # records by the label they are shown under


class Node(TypedDict):
    """An experiment in the tree of tree()."""
    name: str
    parent: str | None
    dead_end: bool
    records: int
    started: str
    running: bool
    processing: str
    clock: str
    above: list[str]
    branch: str


type Nodes = list[Node]


class Item(TypedDict, total=False):
    """An evidence item as the record page shows it: its kind and value; for
    a file, whether /lab/file serves it and its media; for sql, its table; for
    run and record, a label (None if missing)."""
    kind: Required[str]
    value: Required[str]
    served: bool
    media: str | None
    table: Shown | Failure
    label: str | None


class Hit(TypedDict):
    """An experiment, run or record that a search found."""
    kind: str
    id: str
    experiment: str
    title: str
    snippet: str


class Pieces(NamedTuple):
    text: str  # up to the first link
    links: list[Element]  # each with the text after it up to the next as its tail


class Views:
    """The DuckDB databases the pages read, one per project (None: lab.sql's
    views only), each with the sources of its views (query.make_views)."""

    def __init__(self, data: Path) -> None:
        self.data = data
        self.lock = threading.Lock()
        self.made: dict[str | None, query.Database] = {}

    def cursor(self, project: str | None) -> DuckDBPyConnection:
        with self.lock:
            con, sources = self.made.get(project, (None, []))
            if con is None or query.version(con, project) != [(s["path"], s["mtime"]) for s in sources]:
                con, sources = query.connect(self.data, project)
                con.execute("SET enable_external_access = false")
                con.execute("SET lock_configuration = true")
                self.made[project] = query.Database(con, sources)
            return con.cursor()

    def errors(self, project: str | None) -> list[str]:
        return query.errors(self.made[project][1])

    def projects(self) -> list[str]:
        with contextlib.closing(self.cursor(None)) as con:
            return [name for name, _ in query.projects(con)]


def cursor(request: Request) -> Iterator[DuckDBPyConnection]:
    """A cursor of the picked project's database. It also sets
    request.state.projects and request.state.project: the one picked, kept in
    the cookie project, else the first."""
    views = request.app.state.views
    projects = views.projects()
    picked = urllib.parse.unquote(request.cookies.get("project", ""))
    request.state.projects = projects
    request.state.project = picked if picked in projects else next(iter(projects), None)
    con = views.cursor(request.state.project)
    try:
        yield con
    finally:
        con.close()


def page(request: Request, template: str, status_code: int = 200, **context: object) -> Response:
    """The template filled with context; picker=True shows the project picker."""
    context = {"projects": getattr(request.state, "projects", []), "project": getattr(request.state, "project", None),
               **context}
    return templates.TemplateResponse(request, f"lab/{template}.html", context, status_code=status_code)


def query_url(sql: str) -> str:
    """The query page that runs sql."""
    return "/lab/query?sql=" + urllib.parse.quote(sql)


def shown(result: DuckDBPyConnection, limit: int, cut: int | None, ordered: bool = False) -> Shown:
    """query.table's result and the sentence under it, which says how many
    rows it has, which ones when some are left out (ordered: the query's ORDER
    BY gives every row its place) and whether cells are cut."""
    t = query.table(result, limit, cut)
    rows = (f"{len(t['rows'])} rows." if not t["more"] else f"The first {limit} rows." if ordered else
            f"The first {limit} rows the query returned; without ORDER BY, other rows may come first on another load.")
    cells = f" Cells longer than {t['cut']} characters are cut; the query box's result shows them whole." if t["cut"] else ""
    return Shown(**t, note=rows + cells)


def run_sql(con: DuckDBPyConnection, sql: str, limit: int = ROWS, cut: int | None = CUT) -> Shown | Failure:
    """The last statement's rows as a table, or the error text."""
    try:
        if any(s.type in REFUSED for s in con.extract_statements(sql)):
            return Failure(error="BEGIN, COMMIT, ROLLBACK, ATTACH and DETACH are not run here")
        con.begin()
        try:
            result = query.run(con, sql)
            return Failure(error="the statement returns no rows") if result is None else shown(result, limit, cut)
        finally:
            con.rollback()
    except (duckdb.Error, ValueError) as e:
        return Failure(error=str(e))


def roots(con: DuckDBPyConnection) -> list[Path]:
    """The folders whose files /lab/file serves: each project's folder and files folder."""
    return [Path(f).resolve() for row in con.execute("SELECT folder, files FROM lab.experiments"
                                                     " WHERE parent IS NULL").fetchall() for f in row]


def served(path: str, roots: Iterable[Path]) -> Path | None:
    """The file or folder at path (~/ or absolute), resolved, if /lab/file serves it, else None."""
    target = Path(os.path.expanduser(path))
    if not target.is_absolute():
        return None
    target = target.resolve()
    return target if any(target.is_relative_to(r) for r in roots) and target.exists() else None


def media(path: Path) -> str | None:
    """"audio" or "image" for a file the pages play or show in place, else None."""
    kind = mimetypes.guess_type(path.name)[0] or ""
    return next((m for m in ("audio", "image") if kind.startswith(m + "/")), None)


def link(path: str) -> Element:
    a = ElementTree.Element("a", href="/lab/file?path=" + urllib.parse.quote(path))
    a.text = path
    return a


class PathLinks(Treeprocessor):
    """Makes each path of the text (PATH) that /lab/file serves a link to it,
    also when it is all of a code span, but not in code blocks and links."""

    def __init__(self, md: markdown.Markdown, roots: Iterable[Path]) -> None:
        super().__init__(md)
        self.roots = roots

    def pieces(self, text: str) -> Pieces:
        """text up to its first served path, and a link for each, with the
        text after it up to the next as its tail."""
        found = [m for m in PATH.finditer(text) if served(m[0], self.roots)]
        links = [link(m[0]) for m in found]
        for a, m, end in zip(links, found, [m.start() for m in found[1:]] + [len(text)]):
            a.tail = text[m.end():end]
        return Pieces(text[:found[0].start()] if found else text, links)

    def run(self, root: Element) -> None:
        if root.tag in ("pre", "a"):
            return
        if root.tag == "code":
            if root.text and PATH.fullmatch(root.text) and served(root.text, self.roots):
                root.append(link(root.text))
                root.text = None
            return
        for child in list(root):
            self.run(child)
            if child.tail:
                child.tail, links = self.pieces(child.tail)
                at = list(root).index(child) + 1
                root[at:at] = links
        if root.text:
            root.text, links = self.pieces(root.text)
            root[0:0] = links


def md(text: str | None, roots: Iterable[Path]) -> Markup:
    """The Markdown text as HTML, with paths that /lab/file serves (roots) linked."""
    m = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    m.treeprocessors.register(PathLinks(m, roots), "paths", 5)
    return Markup(m.convert(text or ""))


def all_records(con: DuckDBPyConnection, project: str | None = None) -> list[Listed]:
    """Every record, or the project's, with its evidence as a list; experiment
    is its label and filed_in the experiment it is in."""
    where = " WHERE r.project = ?" if project else ""
    rows = con.execute("SELECT r.*, l.experiment AS filed_in FROM records r JOIN lab.records l USING (id)"
                       f"{where} ORDER BY r.time, r.id", [project] if project else [])
    columns = [d[0] for d in rows.description]
    return [Listed(id=r["id"], time=r["time"], project=r["project"], experiment=r["experiment"], kind=r["kind"],
                   title=r["title"], text=r["text"], author=r["author"], status=r["status"], answers=r["answers"],
                   evidence=records.split(r["evidence"]), filed_in=r["filed_in"])
            for r in (dict(zip(columns, values)) for values in rows.fetchall())]


def duration(ms: float) -> str:
    """A time span in milliseconds as text: < 1 min, 12 min, 26 h 53 min."""
    m = int(ms // 60000)
    return "< 1 min" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def covered(spans: Iterable[Span]) -> int:
    """The time during which at least one of the (start, end) spans runs."""
    total = end = 0
    for s, e in sorted(spans):
        total += max(0, e - max(s, end))
        end = max(end, e)
    return total


def ended_runs(con: DuckDBPyConnection, project: str | None) -> dict[str, Spans]:
    """The spans of the project's ended runs, per experiment."""
    spans: dict[str, Spans] = {}
    for name, start, end in con.execute("SELECT r.experiment, r.start_time, r.end_time FROM lab.runs r JOIN experiments x"
                                        " ON x.name = r.experiment WHERE x.project = ? AND r.end_time IS NOT NULL",
                                        [project]).fetchall():
        spans.setdefault(name, []).append((start, end))
    return spans


def drawn(children: Mapping[str | None, Nodes]) -> Nodes:
    """The experiments from the project down, each followed by children[its
    name], with above and branch set."""
    out = []

    def walk(e: Node, above: list[str], prefix: str, last: bool) -> None:
        e["above"], e["branch"] = above, prefix + ("└─ " if last else "├─ ") if above else ""
        out.append(e)
        below = children.get(e["name"], [])
        inner = prefix + ("   " if last else "│  ") if above else ""
        for i, child in enumerate(below):
            walk(child, above + [e["name"]], inner, i == len(below) - 1)

    for root in children.get(None, []):
        walk(root, [], "", True)
    return out


def tree(con: DuckDBPyConnection, project: str | None) -> Nodes:
    """The project's experiments, each followed by those below it, siblings in
    the order they were made. Each is a dict: name, parent, dead_end, records
    (how many), started (when its first run started, or with no runs when it
    was made), running (whether a run has not ended), processing (the sum of
    its ended runs' durations) and clock (the time during which at least one of
    them ran) as text, empty if 0, above (the names from the project down to
    its parent) and branch (the lines that draw the tree before its name). The
    project's records, started, running, processing and clock are those of the
    whole project."""
    rows = con.execute(f"""WITH r AS (SELECT experiment, min(start_time) AS t0,
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
        FROM shown ORDER BY created, name""", [project]).fetchall()
    spans = ended_runs(con, project)
    children: dict[str | None, Nodes] = {}
    for name, parent, dead_end, count, started, running in rows:
        own = spans.get(name, []) if parent else [s for ss in spans.values() for s in ss]
        processing, clock = sum(end - start for start, end in own), covered(own)
        children.setdefault(parent, []).append(Node(
            name=name, parent=parent, dead_end=dead_end, records=count, started=started, running=running,
            processing=duration(processing) if processing else "", clock=duration(clock) if clock else "",
            above=[], branch=""))
    return drawn(children)


def under(nodes: Iterable[Node], node: str | None, below: bool) -> list[str]:
    """The names of node and those below it when below, else of every node."""
    return [e["name"] for e in nodes if not below or e["name"] == node or node in e["above"]]


def search(con: DuckDBPyConnection, words: Sequence[str], experiments: Sequence[str]) -> list[Hit]:
    """Up to ROWS + 1 of the experiments, runs and records of experiments whose
    title and text (the view texts) hold every word, ignoring case."""
    where = " AND ".join(["contains(lower(title || chr(10) || text), ?)"] * len(words))
    found = con.execute("SELECT kind, id, experiment, title, text FROM texts"
                        f" WHERE experiment IN (SELECT unnest(?::VARCHAR[])) AND {where}"
                        f" ORDER BY kind, experiment, title LIMIT {ROWS + 1}", [experiments, *words]).fetchall()
    return [Hit(kind=kind, id=i, experiment=experiment, title=title, snippet=snippet(text, words))
            for kind, i, experiment, title, text in found]


def kind_of(item: str) -> tuple[str, str]:
    """The kind and value of an evidence item. One that is not of
    records.EVIDENCE, which records.check refuses and from_mlflow copies from
    MLflow as it is, is a file, a url or a text."""
    kind, _, value = item.partition(":")
    if kind in records.EVIDENCE:
        return kind, value
    return "file" if PATH.fullmatch(item) else "url" if item.startswith(("http://", "https://")) else "text", item


def evidence_item(item: str, con: DuckDBPyConnection, own: DuckDBPyConnection, tops: Iterable[Path],
                  rs: Mapping[str, Listed]) -> Item:
    """The evidence item as the record page shows it; sql: reads own, the
    views of the record's project, and rs are the records by id."""
    kind, value = kind_of(item)
    e = Item(kind=kind, value=value)
    if kind == "file":
        target = served(value, tops)
        e["served"], e["media"] = target is not None, media(target) if target else None
    elif kind == "sql":
        e["table"] = run_sql(own, value, EVIDENCE_ROWS)
    elif kind == "run":
        found = con.execute("SELECT name, experiment FROM lab.runs WHERE id = ?", [value]).fetchone()
        e["label"] = f"{found[0]} ({found[1]})" if found else None
    elif kind == "record":
        e["label"] = rs[value]["title"] if value in rs else None
    return e


def folder(request: Request, target: Path, tops: Iterable[Path], picker: bool) -> Response:
    """The page of a folder that /lab/file serves: its folders, then its
    files, and a link to its parent unless it is the outermost of tops that
    holds it."""
    top = min((r for r in tops if target.is_relative_to(r)), key=lambda r: len(r.parts))
    shown = shown_path(target)
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    items = [{"name": p.name + ("/" if p.is_dir() else ""), "path": f"{shown}/{p.name}",
              "size": p.stat().st_size if p.is_file() else "", "media": p.is_file() and media(p)}
             for p in entries]
    return page(request, "folder", shown=shown, items=items,
                parent=shown_path(target.parent) if target != top else None, picker=picker)


def snippet(text: str, words: Sequence[str], width: int = 240) -> str:
    """About width characters of text, from a little before the first of words in it."""
    at = min([i for i in (text.lower().find(w) for w in words) if i >= 0], default=0)
    start = max(0, at - width // 4)
    return ("…" if start else "") + text[start:start + width] + ("…" if start + width < len(text) else "")


def send(path: Path) -> FileResponse:
    """The file, as text unless it is a page or binary; a page is shown with scripts off."""
    kind = mimetypes.guess_type(path.name)[0]
    if kind is None:
        with open(path, "rb") as f:
            kind = "application/octet-stream" if b"\0" in f.read(4096) else "text/plain"
    elif kind in TEXT_TYPES or kind.startswith("text/") and kind != "text/html":
        kind = "text/plain"
    headers = {"Content-Security-Policy": "sandbox"} if kind == "text/html" else None
    return FileResponse(path, media_type=kind, headers=headers)


def shown_path(path: Path) -> str:
    """path as ~/... under the home folder, else whole."""
    return "~/" + str(path.relative_to(Path.home())) if path.is_relative_to(Path.home()) else str(path)


def create_app(data: Path) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.views = Views(data)
    app.mount("/lab/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.exception_handler(HTTPException)
    def http_error(request: Request, e: HTTPException) -> Response:
        return page(request, "error", e.status_code, message=e.detail)

    @app.exception_handler(duckdb.Error)
    @app.exception_handler(FileNotFoundError)
    def database_error(request: Request, e: Exception) -> Response:
        return page(request, "error", 500, message=str(e))

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/lab/")

    @app.get("/lab/health")
    def health() -> PlainTextResponse:
        return PlainTextResponse("OK")

    @app.get("/lab/")
    def index(request: Request, con: DuckDBPyConnection = Depends(cursor)) -> Response:
        project = request.state.project
        rs = all_records(con, project)
        by: dict[str, Labeled] = {}
        for r in rs:
            by.setdefault(r["filed_in"], {}).setdefault(r["experiment"], []).append(r)
        for labels in by.values():
            for rows in labels.values():
                rows.sort(key=lambda r: (records.KINDS.index(r["kind"]), r["time"]))
        return page(request, "index", open=[r for r in rs if r["status"] == "open"],
                    groups=[(e, by[e["name"]]) for e in tree(con, project) if e["name"] in by],
                    errors=request.app.state.views.errors(project), picker=True)

    @app.get("/lab/experiments")
    def experiments(request: Request, node: str = "", find: str = "", below: str = "",
                    con: DuckDBPyConnection = Depends(cursor)) -> Response:
        """The project's experiments as a tree, and those of its experiments,
        runs and records whose title and text (the view texts) hold every word
        of find, ignoring case: below node when below is given, else in the
        whole project."""
        if len(find) > LONGEST_FIND:
            raise HTTPException(400, f"the search is longer than {LONGEST_FIND} characters")
        nodes = tree(con, request.state.project)
        node = node if node in [e["name"] for e in nodes] else request.state.project
        words = find.lower().split()
        hits = search(con, words, under(nodes, node, bool(below))) if words else None
        return page(request, "experiments", nodes=nodes, node=node, find=find, below=bool(below) or not words,
                    hits=hits and hits[:ROWS], more=hits is not None and len(hits) > ROWS, picker=True)

    @app.get("/lab/experiment/{name:path}")
    def experiment(request: Request, name: str, con: DuckDBPyConnection = Depends(cursor)) -> Response:
        found = con.execute("SELECT description, parent, dead_end, folder, files, views FROM lab.experiments"
                            " WHERE name = ?", [name]).fetchone()
        if found is None:
            raise HTTPException(404, f"no experiment {name}")
        description, parent, dead_end, folder, files, views = found
        above: list[str] = []
        while parent is not None:
            above.insert(0, parent)
            parent = (con.execute("SELECT parent FROM lab.experiments WHERE name = ?", [parent]).fetchone() or [None])[0]
        branches = con.execute("SELECT name, dead_end FROM lab.experiments WHERE parent = ? ORDER BY created, name",
                               [name]).fetchall()
        runs = f"FROM runs WHERE experiment = {query.literal(name)} ORDER BY start_time DESC, name, run"
        result = con.execute(f"""SELECT run, name, status, {TIME.format("start_time")} AS started,
                round((end_time - start_time) / 1000, 1) AS seconds, params {runs}""")
        t = shown(result, ROWS, CUT, ordered=True)
        rs = [r for r in all_records(con) if r["filed_in"] == name]
        project = folder and {"folder": shown_path(Path(folder)), "files": shown_path(Path(files)),
                              "views": [shown_path(Path(v)) for v in views.split("; ") if v]}
        return page(request, "experiment", name=name, description=md(description, roots(con)), dead_end=dead_end,
                    above=above, branches=branches, project_settings=project, t=t,
                    query=query_url("SELECT * " + runs), records=rs)

    @app.get("/lab/run/{rid}")
    def run_page(request: Request, rid: str, con: DuckDBPyConnection = Depends(cursor)) -> Response:
        result = con.execute(f"SELECT *, {TIME.format('start_time')} AS started, {TIME.format('end_time')} AS ended"
                             " FROM runs WHERE run = ?", [rid])
        row = result.fetchone()
        if row is None:
            raise HTTPException(404, f"no run {rid}")
        r = dict(zip([d[0] for d in result.description], row))
        folder = data / "files" / rid
        files = sorted((str(p.relative_to(folder)), p.stat().st_size, media(p))
                       for p in folder.rglob("*") if p.is_file())
        cited_by = [x for x in all_records(con) if f"run:{rid}" in x["evidence"]]
        return page(request, "run", r=r, note=md(r["note"], roots(con)), files=files, cited_by=cited_by,
                    maps=[(title, r[key] or {}) for title, key in
                          (("Params", "params"), ("Latest metrics", "metrics"), ("Tags", "tags"))])

    @app.get("/lab/run/{rid}/file/{path:path}")
    def run_file(rid: str, path: str) -> FileResponse:
        folder = (data / "files" / rid).resolve()
        target = (folder / path).resolve()
        if not target.is_relative_to(folder) or not target.is_file():
            raise HTTPException(404, f"no file {path} of run {rid}")
        return send(target)

    @app.get("/lab/record/{rid}")
    def record_page(request: Request, rid: str, con: DuckDBPyConnection = Depends(cursor)) -> Response:
        rs = {r["id"]: r for r in all_records(con)}
        if rid not in rs:
            raise HTTPException(404, f"no record {rid}")
        r = rs[rid]
        tops = roots(con)
        with contextlib.closing(request.app.state.views.cursor(r["project"])) as own:
            evidence = [evidence_item(item, con, own, tops, rs) for item in r["evidence"]]
        answered_by = [x for x in rs.values() if x["answers"] == rid]
        cited_by = [x for x in rs.values() if f"record:{rid}" in x["evidence"]]
        return page(request, "record", r=r, text=md(r["text"], tops), evidence=evidence, answers=rs.get(r["answers"]),
                    answered_by=answered_by, cited_by=cited_by)

    @app.get("/lab/view/{name}")
    def view_page(request: Request, name: str, project: str | None = None) -> Response:
        """A view of the project's database, without project of lab.sql's, sorted
        by every column so that each load shows the same rows."""
        views = request.app.state.views
        if project is not None and project not in views.projects():
            raise HTTPException(404, f"no project {project}")
        with contextlib.closing(views.cursor(project)) as con:
            if name not in [v for v, _ in query.view_columns(con)]:
                raise HTTPException(404, f"no view {name}")
            sql = f"SELECT * FROM {query.identifier(name)} ORDER BY ALL"
            return page(request, "view", name=name, project=project, query=query_url(sql),
                        t=shown(con.execute(sql), ROWS, CUT, ordered=True))

    @app.get("/lab/query")
    def query_page(request: Request, sql: str = "", con: DuckDBPyConnection = Depends(cursor)) -> Response:
        if len(sql) > LONGEST_SQL:
            raise HTTPException(400, f"the query is longer than {LONGEST_SQL} characters")
        result = run_sql(con, sql, cut=None) if sql.strip() else None
        return page(request, "query", sql=sql, result=result, views=query.view_columns(con),
                    errors=request.app.state.views.errors(request.state.project), picker=True)

    @app.get("/lab/file")
    def file_page(request: Request, path: str = "", raw: str = "",
                  con: DuckDBPyConnection = Depends(cursor)) -> Response:
        """A file or folder under a project's folder or files folder; without
        path, the picked project's files folder."""
        files = dict(con.execute("SELECT name, files FROM lab.experiments WHERE parent IS NULL").fetchall())
        if not files:
            raise HTTPException(404, "no project yet, so no files folder: rec project makes one")
        picker = not path
        path, tops = path or files[request.state.project], roots(con)
        target = served(path, tops)
        if target is None:
            raise HTTPException(404, f"no file {path} under a project's folder or files folder")
        if target.is_dir():
            return folder(request, target, tops, picker)
        if target.suffix == ".md" and not raw:
            return page(request, "markdown", shown=shown_path(target),
                        text=md(target.read_text(errors="replace"), tops))
        return send(target)

    return app


def main() -> None:
    port = os.environ.get("EXPERIMENT_LAB_PORT", "5000")
    if not (port.isdigit() and 0 < int(port) < 65536):
        sys.exit(f"error: EXPERIMENT_LAB_PORT is {port!r}, not a port number")
    data = data_folder()
    # made in another process, which prints why it failed: this one reads lab.db with DuckDB only
    if not database(data).exists() and subprocess.run([sys.executable, "-m", "experiment_lab.lab"]).returncode:
        sys.exit(1)
    uvicorn.run(create_app(data), host="127.0.0.1", port=int(port), log_level="warning")
