# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`README.md` is the user documentation: projects and the experiment tree, the record format, the
architecture diagram, the table of databases, setup, logging from Python, a project's settings,
moving from mlflow-lab, and the steps for changing the Mermaid or DuckDB version. Read it before changing behavior. This file holds what a developer
needs beyond it.

## Commands

There is no test suite or build step. `.github/workflows/checks.yml` runs the checks below, except
the evals, on every push and pull request, on a lab that `uv run python bin/make_scratch.py FOLDER`
makes: the evals' lab with a run with tags and files, a record of every kind, evidence of every
kind, a diagram, an audio file and an image. The checks:

- `pyright experiment_lab` (it reads `.venv`, which `uv sync` makes, with the package editable).
- `uv run --script bin/check_views.py` and `uv run --script bin/sqlite_filter_bug.py`: the views
  read the same with `lab.db` attached as with its tables copied. They read the data folder's
  `lab.db`; run them while nothing is logged to it.
- `uv run python bin/check_commands.py`: runs a fixed list of rec, q, lab-server and
  lab-from-mlflow commands, wrong input among them, in order on a new lab of `make_scratch.py`,
  and compares what they print and their exit statuses with `bin/check_commands.txt`. When a change
  to what they print is meant, `--update` rewrites the file, and its diff shows the change. The
  output depends on the Python version, which `.python-version` pins.
- `uv run --script bin/check_pages.py [URL]`: every page of a running server, and its diagrams,
  audio players and images in Chrome, light and dark.
- `bin/evals.sh [--case NAME] [--model sonnet]`: the skill's eval cases, run by agents with and
  without the plugin; on Linux it needs `bubblewrap` and `socat`. The whole suite costs about $5 on
  Sonnet; `evals/baseline.md` has the last full run. `--keep-temp` keeps each run's folder,
  with its `lab.db` and transcript. The runs' sandbox hides the repository that holds the plugin,
  the real home folder and the network, which is why `bin/evals.sh` installs the clone into a venv
  of its own and `evals/lab.sh` copies the package and DuckDB's SQLite extension into the run's home
  folder. `claude plugin eval` takes the cases only from a folder inside the plugin's, with no links,
  so `bin/evals.sh` runs a copy of `plugin/` with `evals/` copied into it.

Try changes on a data folder and port of their own, with the commands of `.venv`:

```sh
export PATH=$PWD/.venv/bin:$PATH EXPERIMENT_LAB_DATA=$(mktemp -d) EXPERIMENT_LAB_PORT=5001
bin/start.sh
rec project test --text "Trying a change."    # its folder is this clone
rec experiment first --text "What it tries."
rec add question "Which rate?" --in first --text "..."
q "SELECT id, kind, status, title FROM records"    # pages at http://127.0.0.1:5001/lab
```

For a lab with data already in it, make one with `bin/make_scratch.py` and set
`EXPERIMENT_LAB_DATA=FOLDER/lab-data` instead.

lab-server reads Python and templates when it starts, so restart it after changing them; a change
to `lab.sql`, a view file or a project's list of view files shows without a restart. start.sh exits at once if a server
already answers on the port. To stop a server, kill the process listening on its port
(`ss -ltnp 'sport = :5001'`).

The commands on the PATH come from `uv tool install --editable`, which may point at a different
clone than this one: the `editable` path in `$(uv tool dir)/experiment-lab/uv-receipt.toml` says
which.

An installed plugin is kept by its version: a change to `plugin/` reaches those who installed it
only with a new `version` in `plugin/.claude-plugin/plugin.json`, since `claude plugin update`
reports a copy of the same version as the latest. Raise it when the skill or what the plugin does
changes, not for evals, checks or CI. After the change is on `main`:
`claude plugin marketplace update experiment-lab-marketplace`, then
`claude plugin update experiment-lab@experiment-lab-marketplace`; a running session loads it with
`/reload-plugins`.

## Architecture

- Projects are the rows of `lab.experiments` with no parent; only they have `folder`, `files` and
  `views` (a CHECK in `schema.sql`). `lab.holding` finds the project of the current folder, for
  `Lab.current` and q.
- Writers: `lab.py` (projects, experiments and runs) and `records.py` (records) use only the
  standard library and Python's sqlite3, so a logging script imports them with `PYTHONPATH` alone.
  Every write is one `Lab.write()` transaction (`BEGIN IMMEDIATE`). `settings.py` and `__init__.py`
  must stay importable without the package's dependencies too.
- Readers: `query.py` attaches `lab.db` read-only to an in-memory DuckDB and runs `lab.sql`, then the
  view files of one project, so that each project's views are in a database of their own and may
  reuse names. q, the pages, `from_mlflow.py`'s check and `bin/check_views.py` all read through it.
- No process may have `lab.db` open with Python's sqlite3 while DuckDB reads it, since the two copies
  of SQLite do not see each other's locks (`query.py`'s docstring). So lab-server makes a missing
  `lab.db` in a child process, and `from_mlflow.py` closes its sqlite3 connections before its check.
- The pages (`pages.py`) keep one DuckDB database per project (`Views`), made again when
  `query.version` changes (the modification time of `lab.sql` or a view file, or the list of view
  files), and give each request a cursor of the project in the cookie `project`. Attaching costs
  about 140 ms, since each new DuckDB database loads the SQLite extension again. File access
  is off and the configuration locked. SQL from the query box and from `sql:` evidence runs in a
  transaction that is rolled back; `REFUSED` lists the statement types that would act outside it.
- `lab.sql` is the one set of views that q and the pages run. DuckDB 1.5.5 returned wrong rows for
  a filter over attached SQLite tables (`bin/sqlite_filter_bug.py`), which is why
  `bin/check_views.py` exists.

## What is written in more than one place

Change these together:

- The tables: `schema.sql`; the inserts in `lab.py`, `records.save` and `from_mlflow.Move.write`;
  the views in `lab.sql`.
- The record kinds and statuses: the CHECKs in `schema.sql`, and `KINDS` (in the order the pages
  show them) and `STATUSES` in `records.py`.
- The evidence kinds: `EVIDENCE` in `records.py`, `record_page` in `pages.py` and
  `templates/lab/record.html`.
- What rec prints after `rec project` (`project NAME, folder ...`) and `rec experiment`
  (`experiment PROJECT › ... › NAME`): `rec.py` and the graders in `evals/*/case.yaml`,
  which match it.
- The order of experiments below the same parent (`created, name`): `rec.tree`, `pages.tree` and
  the Branches list of `pages.experiment`.
- The DuckDB version: `pyproject.toml`, `bin/check_views.py`, `bin/sqlite_filter_bug.py`.
- The usage of q, rec and `lab.py`, the record fields and the views' columns: the module docstrings
  of `q.py`, `rec.py` and `lab.py`, `README.md`, and `plugin/skills/experiment-db/SKILL.md`, which
  is what an agent using the lab reads.
- The Mermaid file name, `experiment_lab/static/mermaid-<version>.min.js`, in
  `templates/lab/base.html`; its version must match the one mermaid-cli uses.
