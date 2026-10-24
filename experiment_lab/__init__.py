"""experiment-lab: experiments' runs, and the decisions, findings, protocol
rules and questions about them, in one SQLite database, lab.db, in the data
folder.

settings.py   where the data folder, lab.db and lab.toml are
schema.sql    the tables of lab.db
lab.py        writing experiments and runs (standard library only)
records.py    adding and changing records (standard library only)
query.py      DuckDB SQL over lab.db and the views of lab.sql and lab.toml
q.py, rec.py  the commands q and rec
pages.py      the /lab pages and the command lab-server
from_mlflow.py  the command lab-from-mlflow, which moves an mlflow-lab data folder

This file imports nothing, so that lab.py and records.py stay importable
without the package's dependencies.
"""
