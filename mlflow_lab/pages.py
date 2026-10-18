"""The /lab routes. Records and views come from the view files through Store;
files come only from the folder lab.toml names."""
import json
import mimetypes
import os
from pathlib import Path

import duckdb
import markdown
from flask import Blueprint, abort, render_template, request, send_file
from markupsafe import Markup

blueprint = Blueprint("lab", __name__, url_prefix="/lab", template_folder="templates",
                      static_folder="static")
store = None  # set by create_app
FILES = None  # the folder /lab/file shows, set by create_app from lab.toml
ROWS = 1000  # rows shown per table
EVIDENCE_ROWS = 200
CUT = 300  # characters shown per cell, except in the query box
KIND_ORDER = {"question": 0, "decision": 1, "protocol": 2, "finding": 3}
TEXT_TYPES = ("application/json", "application/javascript", "application/xml", "application/sql",
              "application/x-sh", "application/toml")


def cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) >= 0.001 else f"{v:.3g}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, default=str, ensure_ascii=False)
    return str(v)


def table(result, limit=ROWS, cut=CUT):
    """Column names, the first rows as text with cells cut at cut characters
    (None: whole), whether each column is numeric, and the number of rows
    when all of them are shown."""
    columns = [d[0] for d in result.description]
    rows = result.fetchmany(limit + 1)
    total = len(rows) if len(rows) <= limit else None
    rows = rows[:limit]
    numeric = [bool(rows) and all(isinstance(r[i], (int, float)) and not isinstance(r[i], bool)
                                  for r in rows if r[i] is not None) for i in range(len(columns))]
    text = [[cell(v) for v in r] for r in rows]
    was_cut = cut is not None and any(len(v) > cut for r in text for v in r)
    if was_cut:
        text = [[v if len(v) <= cut else v[:cut - 1] + "…" for v in r] for r in text]
    return {"columns": columns, "rows": text, "numeric": numeric, "total": total, "limit": limit,
            "cut": cut if was_cut else None}


def run_sql(con, sql, limit=ROWS, cut=CUT):
    """The last statement's rows as a table, or the error text."""
    try:
        statements = con.extract_statements(sql)
        if not statements:
            return {"error": "no statement"}
        for statement in statements[:-1]:
            con.execute(statement)
        result = con.execute(statements[-1])
        if result.description is None:
            return {"error": "the statement returns no rows"}
        return table(result, limit, cut)
    except duckdb.Error as e:
        return {"error": str(e)}


def md(text):
    html = markdown.markdown(text or "", extensions=["tables", "fenced_code", "sane_lists"])
    return Markup(html.replace('href="#/', 'href="/#/'))


def records(con):
    rows = con.execute("""SELECT r.*, runs.experiment AS container, e.experiment_id
        FROM records r JOIN runs USING (run) JOIN mlflow.experiments e ON e.name = runs.experiment
        ORDER BY r.time, r.id""")
    columns = [d[0] for d in rows.description]
    out = []
    for values in rows.fetchall():
        r = dict(zip(columns, values))
        r["evidence"] = [item for item in (r["evidence"] or "").split("; ") if item]
        out.append(r)
    return out


def view_names(con):
    return [name for (name,) in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog = current_database() "
        "AND table_schema = 'main' ORDER BY table_name").fetchall()]


def views(con):
    rows = con.execute("""SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position)
        FROM information_schema.columns WHERE table_catalog = current_database() AND table_schema = 'main'
        GROUP BY table_name ORDER BY table_name""").fetchall()
    return [{"name": name, "columns": columns} for name, columns in rows]


@blueprint.errorhandler(duckdb.Error)
def database_error(e):
    return render_template("lab/error.html", message=str(e)), 500


@blueprint.get("/")
def index():
    with store.connect() as con:
        rs = records(con)
    groups = {}
    for r in rs:
        container = groups.setdefault((r["container"], r["experiment_id"]), {})
        container.setdefault(r["experiment"] or "", []).append(r)
    for labels in groups.values():
        for rows in labels.values():
            rows.sort(key=lambda r: (KIND_ORDER.get(r["kind"], 9), r["time"]))
    return render_template("lab/index.html", open=[r for r in rs if r["status"] == "open"],
                           groups=sorted(groups.items()))


@blueprint.get("/record/<rid>")
def record_page(rid):
    with store.connect() as con:
        rs = {r["id"]: r for r in records(con)}
        if rid not in rs:
            abort(404, f"no record {rid}")
        r = rs[rid]
        evidence = []
        for item in r["evidence"]:
            kind, _, value = item.partition(":")
            e = {"kind": kind, "value": value}
            if kind == "sql":
                e["table"] = run_sql(con, value, EVIDENCE_ROWS)
            elif kind == "mlflow":
                row = con.execute("""SELECT r.name, r.experiment, e.experiment_id FROM runs r
                    JOIN mlflow.experiments e ON e.name = r.experiment WHERE r.run = ?""", [value]).fetchone()
                e["label"], e["experiment_id"] = (f"{row[0]} ({row[1]})", row[2]) if row else (value, None)
            elif kind == "record":
                e["label"] = rs[value]["title"] if value in rs else f"{value} (no such record)"
            evidence.append(e)
    answered_by = [x for x in rs.values() if x["answers"] == rid]
    cited_by = [x for x in rs.values() if f"record:{rid}" in x["evidence"]]
    return render_template("lab/record.html", r=r, text=md(r["text"]), evidence=evidence,
                           answers=rs.get(r["answers"]), answered_by=answered_by, cited_by=cited_by)


@blueprint.get("/view/<name>")
def view_page(name):
    with store.connect() as con:
        if name not in view_names(con):
            abort(404, f"no view {name}")
        count = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        t = table(con.execute(f'SELECT * FROM "{name}"'))
    return render_template("lab/view.html", name=name, count=count, t=t)


@blueprint.get("/query")
def query_page():
    sql = request.args.get("sql", "")
    with store.connect() as con:
        result = run_sql(con, sql, cut=None) if sql.strip() else None
        vs = views(con)
    return render_template("lab/query.html", sql=sql, result=result, views=vs)


def shown_path(path):
    """path as ~/... under the home folder, else whole."""
    return "~/" + str(path.relative_to(Path.home())) if path.is_relative_to(Path.home()) else str(path)


@blueprint.get("/file")
def file_page():
    if FILES is None:
        abort(404, "no files are shown: lab.toml, next to mlflow.db, names no files folder")
    path = Path(os.path.expanduser(request.args.get("path", str(FILES)))).resolve()
    if not path.is_relative_to(FILES):
        abort(403, f"only files under {shown_path(FILES)} are shown")
    if not path.exists():
        abort(404, f"no file {path}")
    shown = shown_path(path)
    if path.is_dir():
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        items = [{"name": p.name + ("/" if p.is_dir() else ""), "path": f"{shown}/{p.name}",
                  "size": p.stat().st_size if p.is_file() else ""} for p in entries]
        parent = shown_path(path.parent) if path != FILES else None
        return render_template("lab/folder.html", shown=shown, items=items, parent=parent)
    if path.suffix == ".md" and not request.args.get("raw"):
        return render_template("lab/markdown.html", shown=shown, text=md(path.read_text(errors="replace")))
    kind = mimetypes.guess_type(path.name)[0]
    if kind is None:
        with open(path, "rb") as f:
            kind = "application/octet-stream" if b"\0" in f.read(4096) else "text/plain"
    elif kind in TEXT_TYPES or kind.startswith("text/") and kind != "text/html":
        kind = "text/plain"
    return send_file(path, mimetype=kind)
