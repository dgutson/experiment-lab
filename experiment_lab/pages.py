"""The /lab pages. Four show the project picked at the top of them: its records
with their evidence and a search of them, its experiments as a tree with a
search of their texts, its
views with a query box, and its files folder. The pages they link to, of an
experiment, a run, a record, a view or a file under the projects' folders and
files folders, show the same whatever is picked, and show their project's name
in the picker's place, with the picker's width so that the links after it stay
where they are. The Projects page, which lists every project with a button
that deletes one, and the About page leave that place empty.
lab-server serves them with uvicorn at http://127.0.0.1:$EXPERIMENT_LAB_PORT
(default 5000).

The pages read lab.db only through DuckDB; lab-server makes lab.db, and
deletes a project, in a child process. They keep one DuckDB database per
project with lab.db attached read-only and the project's views, and one whose
views hold every project's rows, for the pages that show the same whatever is
picked, each made again when lab.sql, a view file or a project's list of view
files changes; each request reads through a cursor of its own,
with file access off. A statement of a view file that fails is skipped, and
its error shown on the records page and the view list. SQL from the query box
and from sql: evidence runs in a transaction that is rolled back, so nothing
it makes is kept.
"""
import contextlib
import importlib.metadata
import mimetypes
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Required, TypedDict
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
from experiment_lab.lab import Project, holding
from experiment_lab.settings import data_folder

HERE = Path(__file__).parent
ROWS = 1000  # rows shown per table
EVIDENCE_ROWS = 200
CUT = 300  # characters shown per cell, except in the query box
LONGEST_FIND, LONGEST_SQL = 200, 5000  # characters of a search and of a query from the query box
TEXT_TYPES = ("application/json", "application/javascript", "application/xml", "application/sql",
              "application/x-sh", "application/toml")
# statements that would act outside the transaction that is rolled back
REFUSED = (duckdb.StatementType.TRANSACTION, duckdb.StatementType.ATTACH, duckdb.StatementType.DETACH)
# a ~/ or absolute path in a text, after the start, a space, a parenthesis or a quote; a . , ; : ! or ?
# at its end is not part of it
PATH = re.compile(r"""(?<![^\s("'])~?/[^\s()<>"'`\[\]]*[^\s()<>"'`\[\].,;:!?]""")

templates = Jinja2Templates(directory=HERE / "templates")

type Span = tuple[int, int]  # a run's start and end, in milliseconds
type Spans = list[Span]


class Failure(TypedDict):
    error: str


class Cell(NamedTuple):
    """A cell of a table as a page shows it, and the page it links to."""
    text: str
    numeric: bool
    href: str | None = None


type Cells = list[Cell]
type RunValues = dict[str, Cell]  # a run's params, latest metrics or tags, by key


class Shown(TypedDict):
    """A table as a page shows it: its column names, its rows, the sentence
    under it and query.LastResult's hidden_note above it."""
    columns: list[str]
    rows: list[Cells]
    note: str
    hidden_note: str


class Answer(NamedTuple):
    """A record that answers a question."""
    id: str
    title: str
    kind: str


type Answers = list[Answer]


class Listed(TypedDict):
    """A row of the view records, its evidence a list, and filed_in, the
    experiment it is in (experiment is the label it is shown under);
    answered_by, the records that answer it, and unanswered, whether it is a
    question closed with none."""
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
    answered_by: Answers
    unanswered: bool


type Listing = list[Listed]


class FoundRecord(NamedTuple):
    """A record that the Records page's search found, and its text around the first word found."""
    record: Listed
    snippet: str
type Labeled = dict[str, Listing]  # records by the label they are shown under


class Node(TypedDict):
    """An experiment in the tree of tree()."""
    name: str
    parent: str | None
    dead_end: bool
    records: str
    started: str
    timed: bool
    processing: str
    clock: str
    above: list[str]
    branch: str


type Nodes = list[Node]
type LabelRecords = tuple[str | None, Listing]  # a label and the records under it; None: the experiment's name


class ExperimentRecords(TypedDict):
    """An experiment's records as the records page shows them: indent is how
    far its box is from the left (px), path the experiments above it below
    the project, labels its records by label."""
    name: str
    dead_end: bool
    indent: int
    path: str
    labels: list[LabelRecords]


class Item(TypedDict, total=False):
    """An evidence item as the record page shows it: its kind and value; for
    sql, its table; else the words that show it (prefix, then label), the
    page it links to (None if there is none) and, for a file, its media."""
    kind: Required[str]
    value: Required[str]
    table: Shown | Failure
    prefix: str
    label: str
    href: str | None
    media: str | None


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


def cursor(request: Request, project: str | None = None) -> Iterator[DuckDBPyConnection]:
    """A cursor of the picked project's database. It also sets
    request.state.projects and request.state.project: the one picked, project
    if given, else the one kept in the cookie project, else the first."""
    views = request.app.state.views
    projects = views.projects()
    if project is not None and project not in projects:
        raise HTTPException(404, f"no project {project}")
    picked = project or urllib.parse.unquote(request.cookies.get("project", ""))
    request.state.projects = projects
    request.state.project = picked if picked in projects else next(iter(projects), None)
    with contextlib.closing(views.cursor(request.state.project)) as con:
        yield con


def every_project(request: Request) -> Iterator[DuckDBPyConnection]:
    """A cursor of the database whose views hold every project's rows."""
    with contextlib.closing(request.app.state.views.cursor(None)) as con:
        yield con


def page(request: Request, template: str, status_code: int = 200, **context: object) -> Response:
    """The template filled with context; picker=True shows the project picker,
    else label, a project's name, shows in its place, which keeps the picker's
    width so that the links after it stay where they are."""
    projects = getattr(request.state, "projects", None)
    if projects is None:
        with contextlib.suppress(duckdb.Error, OSError):  # an error page of a missing lab.db
            projects = request.app.state.views.projects()
    context = {"projects": projects or [], "project": getattr(request.state, "project", None), **context}
    return templates.TemplateResponse(request, f"lab/{template}.html", context, status_code=status_code)


def url(kind: str, name: str) -> str:
    """The page of an experiment, a run or a record."""
    return f"/lab/{kind}/{urllib.parse.quote(name)}"


def file_url(path: str, raw: bool = False) -> str:
    """The page of a file or folder that /lab/file serves; raw: of its plain text."""
    return "/lab/file?path=" + urllib.parse.quote(path) + ("&raw=1" if raw else "")


def view_url(name: str, project: str | None) -> str:
    """The page of a view of the project's database."""
    return "/lab/view/" + urllib.parse.quote(name) + (f"?project={urllib.parse.quote(project)}" if project else "")


def query_url(sql: str, project: str | None) -> str:
    """The query page that runs sql in the project's database."""
    return "/lab/query?sql=" + urllib.parse.quote(sql) + (f"&project={urllib.parse.quote(project)}" if project else "")


templates.env.globals.update(url=url, file_url=file_url, view_url=view_url, query_url=query_url)


def shown(result: DuckDBPyConnection, limit: int, cut: int | None, ordered: bool = False,
          link: str | None = None, hidden_note: str = "") -> Shown:
    """query.table's result as a page shows it, and the sentence under it,
    which says how many rows it has, which ones when some are left out
    (ordered: the query's ORDER BY gives every row its place) and whether cells
    are cut. With link, a kind of page that url() takes, the first column (an
    id) is not shown and the second links to the page of that id."""
    t = query.table(result, limit, cut)
    rows = (f"{len(t['rows'])} rows." if not t["more"] else f"The first {limit} rows." if ordered else
            f"The first {limit} rows the query returned; without ORDER BY, other rows may come first on another load.")
    cells = f" Cells longer than {t['cut']} characters are cut; the query box's result shows them whole." if t["cut"] else ""
    return Shown(columns=t["columns"][1:] if link else t["columns"],
                 rows=[row_cells(r, t["numeric"], link) for r in t["rows"]], note=rows + cells,
                 hidden_note=hidden_note)


def row_cells(row: query.Row, numeric: Sequence[bool], link: str | None) -> Cells:
    """A row of query.table as a page shows it; with link, the second value,
    or the first when it is empty, links to the page of the first."""
    if link is None:
        return [Cell(v, n) for v, n in zip(row, numeric)]
    return [Cell(row[1] or row[0], numeric[1], url(link, row[0])), *map(Cell, row[2:], numeric[2:])]


def run_sql(con: DuckDBPyConnection, sql: str, limit: int = ROWS, cut: int | None = CUT) -> Shown | Failure:
    """The last statement's rows as a table, or the error text."""
    try:
        if any(s.type in REFUSED for s in con.extract_statements(sql)):
            return Failure(error="BEGIN, COMMIT, ROLLBACK, ATTACH and DETACH are not run here")
        con.begin()
        try:
            last = query.run(con, sql)
            if last.result is None:
                return Failure(error="the statement returns no rows")
            return shown(last.result, limit, cut, hidden_note=last.hidden_note)
        finally:
            con.rollback()
    except (duckdb.Error, ValueError) as e:
        return Failure(error=str(e))


def roots(con: DuckDBPyConnection) -> list[Path]:
    """The folders whose files /lab/file serves: each project's folder and files folder."""
    return [Path(f).resolve() for row in con.execute("SELECT folder, files FROM lab.experiments"
                                                     " WHERE parent IS NULL").fetchall() for f in row]


def holder(con: DuckDBPyConnection, target: Path) -> str | None:
    """The project whose folder or files folder holds target, the innermost if several."""
    rows = con.execute("SELECT name, folder, files FROM lab.experiments WHERE parent IS NULL").fetchall()
    return holding([Project(name, f) for name, *folders in rows for f in folders], [target])


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
    a = ElementTree.Element("a", href=file_url(path))
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


def all_records(con: DuckDBPyConnection) -> list[Listed]:
    """The records of the view records, with their evidence as a list;
    experiment is its label and filed_in the experiment it is in."""
    rows = con.execute("SELECT r.*, l.experiment AS filed_in FROM records r JOIN lab.records l USING (id)"
                       " ORDER BY r.time, r.id")
    columns = [d[0] for d in rows.description]
    rs = [dict(zip(columns, values)) for values in rows.fetchall()]
    answers: dict[str, Answers] = {}
    for r in rs:
        answers.setdefault(r["answers"], []).append(Answer(r["id"], r["title"], r["kind"]))
    return [Listed(id=r["id"], time=r["time"], project=r["project"], experiment=r["experiment"], kind=r["kind"],
                   title=r["title"], text=r["text"], author=r["author"], status=r["status"], answers=r["answers"],
                   evidence=records.split(r["evidence"]), filed_in=r["filed_in"],
                   answered_by=answers.get(r["id"], []),
                   unanswered=r["kind"] == "question" and r["status"] == "closed" and r["id"] not in answers)
            for r in rs]


def duration(ms: float) -> str:
    """A time span in milliseconds as text: < 1 min, 12 min, 26 h 53 min."""
    m = int(ms // 60000)
    return "< 1 min" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def spent(ms: float, running: bool) -> str:
    """An experiment's time as the experiments page shows it: running while
    one of its runs has not ended, else its duration, not timed if 0."""
    return "running" if running else duration(ms) if ms else "not timed"


def covered(spans: Iterable[Span]) -> int:
    """The time during which at least one of the (start, end) spans runs."""
    total = end = 0
    for s, e in sorted(spans):
        total += max(0, e - max(s, end))
        end = max(end, e)
    return total


def ended_runs(con: DuckDBPyConnection) -> dict[str, Spans]:
    """The spans of the ended runs of the experiments of the view experiments, per experiment."""
    spans: dict[str, Spans] = {}
    for name, start, end in con.execute("SELECT r.experiment, r.start_time, r.end_time FROM lab.runs r JOIN experiments x"
                                        " ON x.name = r.experiment WHERE r.end_time IS NOT NULL").fetchall():
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


def tree(con: DuckDBPyConnection) -> Nodes:
    """The project's experiments, each followed by those below it, siblings in
    the order they were made. Each is a dict: name, parent, dead_end, records
    (how many, empty if none), started (when its first run started, or with no runs when it
    was made), processing (the sum of its ended runs' durations) and clock (the
    time during which at least one of them ran) as spent() shows them, timed
    (whether both are durations), above (the names from the project down to
    its parent) and branch (the lines that draw the tree before its name). The
    project's records, started, processing and clock are those of the whole
    project."""
    rows = con.execute("""WITH r AS (SELECT experiment, min(start_time) AS t0,
                 bool_or(status = 'RUNNING') AS running FROM lab.runs GROUP BY ALL),
             c AS (SELECT experiment, count(*) AS n FROM lab.records GROUP BY ALL),
             own AS (SELECT e.name, e.parent, e.dead_end, e.created, coalesce(c.n, 0) AS records,
                 coalesce(r.t0, e.created) AS t0, coalesce(r.running, false) AS running
             FROM lab.experiments e JOIN experiments x USING (name)
             LEFT JOIN r ON r.experiment = e.name LEFT JOIN c ON c.experiment = e.name),
             shown AS (SELECT name, parent, dead_end, created,
                 if(parent IS NULL, sum(records) OVER (), records) AS records,
                 if(parent IS NULL, min(t0) OVER (), t0) AS t0,
                 if(parent IS NULL, bool_or(running) OVER (), running) AS running FROM own)
        SELECT name, parent, dead_end, records, local_time(t0) AS started, running
        FROM shown ORDER BY created, name""").fetchall()
    spans = ended_runs(con)
    children: dict[str | None, Nodes] = {}
    for name, parent, dead_end, count, started, running in rows:
        own = spans.get(name, []) if parent else [s for ss in spans.values() for s in ss]
        processing, clock = sum(end - start for start, end in own), covered(own)
        children.setdefault(parent, []).append(Node(
            name=name, parent=parent, dead_end=dead_end, records=str(count) if count else "", started=started,
            timed=not running and processing > 0, processing=spent(processing, running), clock=spent(clock, running),
            above=[], branch=""))
    return drawn(children)


def records_by_experiment(rs: Listing) -> dict[str, Labeled]:
    """The records by the experiment they are filed in, then by the label they
    are shown under, each label's in the order of KINDS, then by time."""
    by: dict[str, Labeled] = {}
    for r in rs:
        by.setdefault(r["filed_in"], {}).setdefault(r["experiment"], []).append(r)
    for labels in by.values():
        for rows in labels.values():
            rows.sort(key=lambda r: (records.KINDS.index(r["kind"]), r["time"]))
    return by


def experiment_records(e: Node, labels: Labeled) -> ExperimentRecords:
    """The experiment's records, by label in the order of their names
    ignoring case, as the records page shows them."""
    above = e["above"][1:]
    return ExperimentRecords(name=e["name"], dead_end=e["dead_end"], indent=16 * len(above),
                 path=" › ".join(above) + " ›" if above else "",
                 labels=[(None if label == e["name"] else label, rows)
                         for label, rows in sorted(labels.items(), key=lambda lr: lr[0].lower())])


def under(nodes: Iterable[Node], node: str | None, below: bool) -> list[str]:
    """The names of node and those below it when below, else of every node."""
    return [e["name"] for e in nodes if not below or e["name"] == node or node in e["above"]]


def search(con: DuckDBPyConnection, words: Sequence[str], experiments: Sequence[str]) -> list[Hit]:
    """Up to ROWS + 1 of the experiments, runs and records of experiments whose
    title and text (the view texts) hold every word, ignoring case."""
    found = con.execute("SELECT kind, id, experiment, title, text FROM texts"
                        " WHERE experiment IN (SELECT unnest(?::VARCHAR[])) AND holds_words(title || chr(10) || text, ?)"
                        f" ORDER BY kind, experiment, title LIMIT {ROWS + 1}", [experiments, " ".join(words)]).fetchall()
    return [Hit(kind=kind, id=i, experiment=experiment, title=title, snippet=snippet(text, words))
            for kind, i, experiment, title, text in found]


def found_records(con: DuckDBPyConnection, rs: Listing, words: Sequence[str]) -> list[FoundRecord]:
    """Up to ROWS + 1 of rs, the project's records, whose title and text hold every word, ignoring case."""
    ids = {i for (i,) in con.execute("SELECT id FROM records WHERE holds_words(title || chr(10) || text, ?)",
                                     [" ".join(words)]).fetchall()}
    return [FoundRecord(r, snippet(r["text"], words)) for r in rs if r["id"] in ids][:ROWS + 1]


def searched(find: str) -> list[str]:
    """The words of a search box's text, in lower case."""
    if len(find) > LONGEST_FIND:
        raise HTTPException(400, f"the search is longer than {LONGEST_FIND} characters")
    return find.lower().split()


def kind_of(item: str) -> tuple[str, str]:
    """The kind and value of an evidence item. One that is not of
    records.EVIDENCE, which records.check refuses but lab.db may hold, is a
    file, a url or a text."""
    kind, _, value = item.partition(":")
    if kind in records.EVIDENCE:
        return kind, value
    return "file" if PATH.fullmatch(item) else "url" if item.startswith(("http://", "https://")) else "text", item


def evidence_item(item: str, con: DuckDBPyConnection, own: DuckDBPyConnection, tops: Iterable[Path],
                  rs: Mapping[str, Listed]) -> Item:
    """The evidence item as the record page shows it; sql: reads own, the
    views of the record's project, and rs are the records by id."""
    kind, value = kind_of(item)
    if kind == "sql":
        return Item(kind=kind, value=value, table=run_sql(own, value, EVIDENCE_ROWS))
    if kind == "file":
        target = served(value, tops)
        return cited_item(kind, value, value if target else None, file_url(value),
                       "file under a project's folder or files folder", media(target) if target else None)
    if kind == "run":
        found = con.execute("SELECT name, experiment FROM lab.runs WHERE id = ?", [value]).fetchone()
        return cited_item(kind, value, f"{found[0]} ({found[1]})" if found else None, url("run", value), "run")
    if kind == "record":
        return cited_item(kind, value, rs[value]["title"] if value in rs else None, url("record", value), "record")
    return Item(kind=kind, value=value, prefix="", label=value, href=value if kind == "url" else None)


def cited_item(kind: str, value: str, label: str | None, href: str, missing: str, media_kind: str | None = None) -> Item:
    """An evidence item that cites a run, a file or a record: its label, page
    and media_kind (pages.media); with label None, that there is no such missing."""
    if label is None:
        return Item(kind=kind, value=value, prefix=f"{kind.capitalize()}: ", label=f"{value} (no such {missing})",
                    href=None)
    return Item(kind=kind, value=value, prefix=f"{kind.capitalize()}: ", label=label, href=href, media=media_kind)


def folder(request: Request, target: Path, tops: Iterable[Path], picker: bool, label: str | None) -> Response:
    """The page of a folder that /lab/file serves: its folders, then its
    files, and a link to its parent unless it is the outermost of tops that
    holds it."""
    top = min((r for r in tops if target.is_relative_to(r)), key=lambda r: len(r.parts))
    shown = shown_path(target)
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    items = [{"name": p.name + ("/" if p.is_dir() else ""), "src": file_url(f"{shown}/{p.name}"),
              "size": p.stat().st_size if p.is_file() else "", "media": p.is_file() and media(p)}
             for p in entries]
    return page(request, "folder", shown=shown, items=items,
                parent=shown_path(target.parent) if target != top else None, picker=picker, label=label)


def run_values(r: Mapping[str, Any]) -> dict[str, RunValues]:
    """The run's params, latest metrics and tags, those it has, by the
    heading its page shows them under: keys in order ignoring case, values as
    query.cell writes them."""
    return {title: {k: Cell(query.cell(v), isinstance(v, (int, float)))
                    for k, v in sorted(values.items(), key=lambda kv: kv[0].lower())}
            for title, values in (("Params", r["params"]), ("Latest metrics", r["metrics"]), ("Tags", r["tags"]))
            if values}


def about() -> dict[str, Cell]:
    """The version, repository and author of the installed experiment-lab, as
    its pyproject.toml gives them; only the version, unknown, if it is not
    installed."""
    try:
        meta = importlib.metadata.metadata("experiment-lab")
    except importlib.metadata.PackageNotFoundError:
        return {"Version": Cell("unknown", False)}
    urls = dict(u.split(", ", 1) for u in meta.get_all("Project-URL") or [])
    return {"Version": Cell(meta["Version"], False), "Repository": Cell(urls["Repository"], False, urls["Repository"]),
            "Author": Cell(meta["Author"], False, urls["Author"])}


class ListedProject(NamedTuple):
    """A row of the Projects page: a project, the number of experiments below
    it and of their runs and records, and its delete dialog's sentences."""
    name: str
    created: str
    description: str
    folder: str
    experiments: int
    runs: int
    records: int
    question: str
    deletes: str
    kept: str
    delete_url: str


PROJECTS = """SELECT p.name, p.created, p.description, l.folder,
    (SELECT count(*) FROM experiments x WHERE x.project = p.name AND x.depth > 0),
    (SELECT count(*) FROM lab.runs r JOIN experiments x ON x.name = r.experiment WHERE x.project = p.name),
    (SELECT count(*) FROM lab.records r JOIN experiments x ON x.name = r.experiment WHERE x.project = p.name)
FROM experiments p JOIN lab.experiments l USING (name) WHERE p.depth = 0 ORDER BY p.name"""


def listed_projects(con: DuckDBPyConnection) -> list[ListedProject]:
    listed = []
    for name, created, description, folder, experiments, runs, records_ in con.execute(PROJECTS).fetchall():
        listed.append(ListedProject(
            name, created, description, shown_path(Path(folder)), experiments, runs, records_,
            question=f"Delete the project {name}?",
            deletes=(f"This deletes from lab.db its {experiments} experiments, {runs} runs and {records_} records,"
                     " and the runs' files in the data folder. It cannot be undone."),
            kept=f"Its folder {shown_path(Path(folder))}, its files folder and its view files are not touched.",
            delete_url="/lab/projects/delete?name=" + urllib.parse.quote(name)))
    return listed


def same_origin(request: Request) -> bool:
    """Whether a request comes from a page of this server, which any website's
    page can post a form to: its Host is 127.0.0.1 or localhost, not a website's
    domain made to resolve to it, and its Origin that same address."""
    host = request.headers.get("host", "")
    return (urllib.parse.urlsplit(f"//{host}").hostname in ("127.0.0.1", "localhost")
            and request.headers.get("origin") == f"http://{host}")


def found(hits: Sequence[object], where: str) -> str:
    """The sentence above a search's results, of which the page shows ROWS."""
    return f"{'The first ' if len(hits) > ROWS else ''}{min(len(hits), ROWS)} found in {where}."


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
    def index(request: Request, find: str = "", con: DuckDBPyConnection = Depends(cursor)) -> Response:
        """The project's records, and those whose title and text hold every word of find, ignoring case."""
        project = request.state.project
        words = searched(find)
        rs = all_records(con)
        hits = found_records(con, rs, words) if words else None
        by = records_by_experiment(rs)
        return page(request, "index", find=find, hits=hits and hits[:ROWS],
                    found=None if hits is None else found(hits, project), open=[r for r in rs if r["status"] == "open"],
                    experiments=[experiment_records(e, by[e["name"]]) for e in tree(con) if e["name"] in by],
                    errors=request.app.state.views.errors(project), picker=True)

    @app.get("/lab/experiments")
    def experiments(request: Request, node: str = "", find: str = "", below: str = "",
                    con: DuckDBPyConnection = Depends(cursor)) -> Response:
        """The project's experiments as a tree, and those of its experiments,
        runs and records whose title and text (the view texts) hold every word
        of find, ignoring case: below node when below is given, else in the
        whole project."""
        words = searched(find)
        nodes = tree(con)
        node = node if node in [e["name"] for e in nodes] else request.state.project
        hits = search(con, words, under(nodes, node, bool(below))) if words else None
        where = f"{node} and below it" if below else request.state.project
        return page(request, "experiments", nodes=nodes, node=node, find=find, below=bool(below) or not words,
                    hits=hits and hits[:ROWS], found=None if hits is None else found(hits, where), picker=True)

    @app.get("/lab/experiment/{name:path}")
    def experiment(request: Request, name: str, con: DuckDBPyConnection = Depends(every_project)) -> Response:
        found = con.execute("SELECT description, parent, dead_end, folder, files, views FROM lab.experiments"
                            " WHERE name = ?", [name]).fetchone()
        if found is None:
            raise HTTPException(404, f"no experiment {name}")
        description, parent, dead_end, folder, files, views = found
        path = [name]  # from the project down
        while parent is not None:
            path.insert(0, parent)
            parent = (con.execute("SELECT parent FROM lab.experiments WHERE name = ?", [parent]).fetchone() or [None])[0]
        branches = con.execute("SELECT name, dead_end FROM lab.experiments WHERE parent = ? ORDER BY created, name",
                               [name]).fetchall()
        runs = f"FROM runs WHERE experiment = {query.literal(name)} ORDER BY start_time DESC, name, run"
        result = con.execute(f"SELECT run, name, status, started, round((end_time - start_time) / 1000, 1) AS seconds,"
                             f" params {runs}")
        t = shown(result, ROWS, CUT, ordered=True, link="run")
        rs = [r for r in all_records(con) if r["filed_in"] == name]
        project = folder and {"folder": shown_path(Path(folder)), "files": shown_path(Path(files)),
                              "views": ", ".join(shown_path(Path(v)) for v in views.split("; ") if v) or "none"}
        return page(request, "experiment", name=name, description=md(description, roots(con)), dead_end=dead_end,
                    above=path[:-1], branches=branches, project_settings=project, t=t,
                    query=query_url("SELECT * " + runs, path[0]), records=rs, label=path[0])

    @app.get("/lab/run/{rid}")
    def run_page(request: Request, rid: str, con: DuckDBPyConnection = Depends(every_project)) -> Response:
        result = con.execute("SELECT *, concat_ws(' to ', started, ended) AS span FROM runs WHERE run = ?", [rid])
        row = result.fetchone()
        if row is None:
            raise HTTPException(404, f"no run {rid}")
        r = dict(zip([d[0] for d in result.description], row))
        folder = data / "files" / rid
        logged_files = con.execute("SELECT name, bytes FROM files WHERE run = ? ORDER BY name", [rid]).fetchall()
        files = [(n, size, media(folder / n), f"{url('run', rid)}/file/{urllib.parse.quote(n)}")
                 for n, size in logged_files]
        cited_by = [x for x in all_records(con) if f"run:{rid}" in x["evidence"]]
        logged = query_url("SELECT key, step, time, value FROM lab.metrics"
                           f" WHERE run = {query.literal(rid)} ORDER BY key, step, time", r["project"])
        return page(request, "run", r=r, note=md(r["note"], roots(con)), files=files, cited_by=cited_by, logged=logged,
                    run_values=run_values(r), label=r["project"])

    @app.get("/lab/run/{rid}/file/{path:path}")
    def run_file(rid: str, path: str) -> FileResponse:
        folder = (data / "files" / rid).resolve()
        target = (folder / path).resolve()
        if not target.is_relative_to(folder) or not target.is_file():
            raise HTTPException(404, f"no file {path} of run {rid}")
        return send(target)

    @app.get("/lab/record/{rid}")
    def record_page(request: Request, rid: str, con: DuckDBPyConnection = Depends(every_project)) -> Response:
        rs = {r["id"]: r for r in all_records(con)}
        if rid not in rs:
            raise HTTPException(404, f"no record {rid}")
        r = rs[rid]
        tops = roots(con)
        with contextlib.closing(request.app.state.views.cursor(r["project"])) as own:
            evidence = [evidence_item(item, con, own, tops, rs) for item in r["evidence"]]
        answered_by = [(x, md(x["text"], tops)) for x in rs.values() if x["answers"] == rid]
        cited_by = [x for x in rs.values() if f"record:{rid}" in x["evidence"]]
        return page(request, "record", r=r, text=md(r["text"], tops), evidence=evidence, answers=rs.get(r["answers"]),
                    answered_by=answered_by, cited_by=cited_by, by=r["author"] or "?", label=r["project"])

    @app.get("/lab/view/{name}")
    def view_page(request: Request, name: str, con: DuckDBPyConnection = Depends(cursor)) -> Response:
        """A view of the project's database, sorted by every column so that
        each load shows the same rows."""
        if name not in [v for v, _ in query.view_columns(con)]:
            raise HTTPException(404, f"no view {name}")
        sql = f"SELECT * FROM {query.identifier(name)} ORDER BY ALL"
        return page(request, "view", name=name, query=query_url(sql, request.state.project),
                    t=shown(con.execute(sql), ROWS, CUT, ordered=True), label=request.state.project)

    @app.get("/lab/query")
    def query_page(request: Request, sql: str = "", con: DuckDBPyConnection = Depends(cursor)) -> Response:
        if len(sql) > LONGEST_SQL:
            raise HTTPException(400, f"the query is longer than {LONGEST_SQL} characters")
        result = run_sql(con, sql, cut=None) if sql.strip() else None
        project = request.state.project
        listed = "lab.sql's views" + (f" and those of {project}'s view files" if project else "") + "."
        return page(request, "query", sql=sql, result=result, views=query.view_columns(con), listed=listed,
                    errors=request.app.state.views.errors(project), picker=True)

    @app.get("/lab/projects")
    def projects_page(request: Request, con: DuckDBPyConnection = Depends(every_project)) -> Response:
        return page(request, "projects", listed=listed_projects(con))

    @app.post("/lab/projects/delete")
    def delete_project(request: Request, name: str) -> Response:
        if not same_origin(request):
            raise HTTPException(403, "a project is deleted only from this server's Projects page")
        if name not in request.app.state.views.projects():
            raise HTTPException(404, f"no project {name}")
        # lab.db is written in another process: this one reads it with DuckDB only
        done = subprocess.run([sys.executable, "-m", "experiment_lab.lab", "--delete", name], capture_output=True,
                              text=True, env=dict(os.environ, EXPERIMENT_LAB_DATA=str(data)), check=False)
        if done.returncode:
            raise HTTPException(500, done.stderr.strip())
        return RedirectResponse("/lab/projects", status_code=303)

    @app.get("/lab/about")
    def about_page(request: Request) -> Response:
        return page(request, "about", lines=about())

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
            return folder(request, target, tops, picker, holder(con, target))
        if target.suffix == ".md" and not raw:
            return page(request, "markdown", shown=shown_path(target),
                        text=md(target.read_text(errors="replace"), tops), label=holder(con, target))
        return send(target)

    return app


def main() -> None:
    port = os.environ.get("EXPERIMENT_LAB_PORT", "5000")
    if not (port.isdigit() and 0 < int(port) < 65536):
        sys.exit(f"error: EXPERIMENT_LAB_PORT is {port!r}, not a port number")
    data = data_folder()
    # lab.db, or the tables it lacks, made in another process, which prints why it failed: this one reads
    # lab.db with DuckDB only
    if subprocess.run([sys.executable, "-m", "experiment_lab.lab"]).returncode:
        sys.exit(1)
    uvicorn.run(create_app(data), host="127.0.0.1", port=int(port), log_level="warning")
