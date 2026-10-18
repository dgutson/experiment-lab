"""Pages at /lab of the MLflow server: the records (decisions, findings,
protocol rules, questions) with their evidence, lab.sql's views as tables, a
read-only query box, and the experiment folders' files. Markdown (record texts,
.md files) is shown as HTML, its ```mermaid blocks drawn as diagrams in the
browser by the copy of mermaid.js in static/.

`mlflow server --app-name lab` runs create_app, which adds the pages to
MLflow's Flask app the way MLflow's login add-on (basic-auth) adds its own.
The data folder is the database's folder; its lab.toml, read once at start,
names the folder /lab/file shows and the view files run after lab.sql.
"""
import os
import tomllib
from pathlib import Path

from mlflow.environment_variables import _MLFLOW_SGI_NAME
from mlflow.server import BACKEND_STORE_URI_ENV_VAR
from mlflow.server import app as mlflow_app

from mlflow_lab import pages
from mlflow_lab.store import Store


def settings(data):
    """The data folder's lab.toml: the folder /lab/file shows (None without
    one) and the view files run after lab.sql. bin/q.py reads it the same way."""
    path = data / "lab.toml"
    found = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(found) - {"files", "views"}
    if unknown:
        raise ValueError(f"{path}: unknown settings {', '.join(sorted(unknown))}")

    def resolve(p):
        return (data / Path(p).expanduser()).resolve()

    files = resolve(found["files"]) if "files" in found else None
    return files, [resolve(p) for p in found.get("views", [])]


def create_app(app=mlflow_app):
    uri = os.environ[BACKEND_STORE_URI_ENV_VAR]
    if not uri.startswith("sqlite:///"):
        raise ValueError(f"the lab pages read a SQLite store, not {uri}")
    database = Path(uri.removeprefix("sqlite:///"))
    pages.FILES, views = settings(database.parent)
    pages.store = Store(database, views)
    app.register_blueprint(pages.blueprint)
    if _MLFLOW_SGI_NAME.get() == "uvicorn":
        from mlflow.server.fastapi_app import create_fastapi_app

        return create_fastapi_app(app)
    return app
