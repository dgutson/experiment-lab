# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`README.md` is the user documentation: the record format, the architecture diagram, the table of
databases, setup, logging from Python, `lab.toml`, moving from mlflow-lab, and the steps for changing
the Mermaid or DuckDB version. Read it before changing behavior. This file holds what a developer
needs beyond it.

## Commands

There is no test suite or build step. The checks:

- `pyright experiment_lab` (it reads `.venv`, which `uv sync` makes, with the package editable).
- `uv run --script bin/check_views.py` and `uv run --script bin/sqlite_filter_bug.py`: the views
  read the same with `lab.db` attached as with its tables copied. They read the data folder's
  `lab.db`; run them while nothing is logged to it.
- `uv run --script bin/check_pages.py [URL]`: every page of a running server, and its diagrams in
  Chrome, light and dark.

Try changes on a data folder and port of their own, with the commands of `.venv`:

```sh
export PATH=$PWD/.venv/bin:$PATH EXPERIMENT_LAB_DATA=$(mktemp -d) EXPERIMENT_LAB_PORT=5001
bin/start.sh
rec experiment test --text "Trying a change."
rec add question "Which rate?" --in test --text "..."
q "SELECT id, kind, status, title FROM records"    # pages at http://127.0.0.1:5001/lab
```

lab-server reads Python and templates when it starts, so restart it after changing them; a change
to `lab.sql`, `lab.toml` or a view file shows without a restart. start.sh exits at once if a server
already answers on the port. To stop a server, kill the process listening on its port
(`ss -ltnp 'sport = :5001'`).

The commands on the PATH come from `uv tool install --editable`, which may point at a different
clone than this one: the `editable` path in `$(uv tool dir)/experiment-lab/uv-receipt.toml` says
which.

## Architecture

- Writers: `lab.py` (experiments and runs) and `records.py` (records) use only the standard
  library and Python's sqlite3, so a logging script imports them with `PYTHONPATH` alone. Every
  write is one `Lab.write()` transaction (`BEGIN IMMEDIATE`). `settings.py` and `__init__.py` must
  stay importable without the package's dependencies too.
- Readers: `query.py` attaches `lab.db` read-only to an in-memory DuckDB and runs `lab.sql`, then the
  view files of `lab.toml`. q, the pages, `from_mlflow.py`'s check and `bin/check_views.py` all read
  through it.
- No process may have `lab.db` open with Python's sqlite3 while DuckDB reads it, since the two copies
  of SQLite do not see each other's locks (`query.py`'s docstring). So lab-server makes a missing
  `lab.db` in a child process, and `from_mlflow.py` closes its sqlite3 connections before its check.
- The pages (`pages.py`) keep one DuckDB database (`Views`), made again when the modification time
  of `lab.toml`, `lab.sql` or a view file changes, and give each request its own cursor. Attaching
  costs about 140 ms, since each new DuckDB database loads the SQLite extension again. File access
  is off and the configuration locked. SQL from the query box and from `sql:` evidence runs in a
  transaction that is rolled back; `REFUSED` lists the statement types that would act outside it.
- `lab.sql` is the one set of views that q and the pages run. DuckDB 1.5.5 returned wrong rows for
  a filter over attached SQLite tables (`bin/sqlite_filter_bug.py`), which is why
  `bin/check_views.py` exists.

## What is written in more than one place

Change these together:

- The tables: `schema.sql`; the inserts in `lab.py`, `records.save` and `from_mlflow.Move.write`;
  the views in `lab.sql`.
- The evidence kinds: `EVIDENCE` in `records.py`, `record_page` in `pages.py` and
  `templates/lab/record.html`.
- The DuckDB version: `pyproject.toml`, `bin/check_views.py`, `bin/sqlite_filter_bug.py`.
- The usage of q, rec and `lab.py`, the record fields and the views' columns: the module docstrings
  of `q.py`, `rec.py` and `lab.py`, `README.md`, and `plugin/skills/experiment-db/SKILL.md`, which
  is what an agent using the lab reads.
- The Mermaid file name, `experiment_lab/static/mermaid-<version>.min.js`, in
  `templates/lab/base.html`; its version must match the one mermaid-cli uses.
