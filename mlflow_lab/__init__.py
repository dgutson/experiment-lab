"""Pages at /lab of the MLflow server: the records (decisions, findings,
protocol rules, questions) with their evidence, lab.sql's views as tables, a
read-only query box, and the experiment folders' files. Markdown (record texts,
.md files) is shown as HTML, its ```mermaid blocks drawn as diagrams in the
browser by the copy of mermaid.js in static/.

`mlflow server --app-name lab` runs create_app, which adds the pages to
MLflow's Flask app the way MLflow's login add-on (basic-auth) adds its own.
"""
import os

from mlflow.environment_variables import _MLFLOW_SGI_NAME
from mlflow.server import BACKEND_STORE_URI_ENV_VAR
from mlflow.server import app as mlflow_app

from mlflow_lab import pages
from mlflow_lab.store import Store


def create_app(app=mlflow_app):
    uri = os.environ[BACKEND_STORE_URI_ENV_VAR]
    if not uri.startswith("sqlite:///"):
        raise ValueError(f"the lab pages read a SQLite store, not {uri}")
    pages.store = Store(uri.removeprefix("sqlite:///"))
    app.register_blueprint(pages.blueprint)
    if _MLFLOW_SGI_NAME.get() == "uvicorn":
        from mlflow.server.fastapi_app import create_fastapi_app

        return create_fastapi_app(app)
    return app
