"""experiment-lab: experiments' runs, and the decisions, findings, protocol
rules and questions about them, in one SQLite database, lab.db, in the data
folder.

settings.py   where the data folder and lab.db are
schema.sql    the tables of lab.db
lab.py        writing projects, experiments and runs, and reading runs (standard library only)
records.py    adding and changing records (standard library only)
query.py      DuckDB SQL over lab.db and the views of lab.sql and a project's view files
q.py, rec.py  the commands q and rec
pages.py      the /lab pages and the command lab-server

This file imports nothing, so that lab.py and records.py stay importable
without the package's dependencies.
"""
