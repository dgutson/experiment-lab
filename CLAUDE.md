# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`README.md` is the user documentation: projects and the experiment tree, the record format, the
architecture diagram, the table of databases, setup, logging from Python, a project's settings,
and the steps for changing the Mermaid or DuckDB version. Read it before changing behavior. This
file holds what a developer needs beyond it.

## Commands

There is no test suite or build step. `.github/workflows/checks.yml` runs the checks below, except
the evals, on every push and pull request, on a lab that `uv run python checks/make_scratch.py FOLDER`
makes: the evals' lab with a run with tags and files, a record of every kind, evidence of every
kind, a diagram, an audio file and an image. The checks:

- `uv run mypy` and `uv run pyright`: the types of `experiment_lab/` and `checks/`, mypy with
  `--strict`. `uv sync` installs mypy, the Markdown stubs, pyright, ruff, pylint,
  pylint-complex-struct and Playwright into `.venv` from the `dev` group of `pyproject.toml`, with
  the package editable. If the two disagree about correct code, mypy stays and pyright goes. mypy
  refuses a `TypedDict` made from a dict built at run time (`Record(**row)`), so a row or rec set's
  fields become one through a function that names every key (`records.as_record`). mypy reports
  nothing for a file under the repository that its `files` setting leaves out: the editable
  install's `.pth` makes it look installed.
- `uv run ruff check experiment_lab checks`: every function's parameters and return are annotated
  (ruff's `ANN` rules only).
- `uv run pylint experiment_lab checks`: pylint-complex-struct's messages only, and a failure if the
  plugin does not load. An annotation nests at most two levels, a name counting as one, so a deeper
  type becomes a type alias (`type Spans = list[Span]`), a `NamedTuple` or a `TypedDict`, and a
  function returns a `NamedTuple` rather than a tuple of different types.
- `uv run --script checks/check_views.py` and `uv run --script checks/sqlite_filter_bug.py`: the views
  read the same with `lab.db` attached as with its tables copied. They read the data folder's
  `lab.db`; run them while nothing is logged to it.
- `uv run python checks/check_commands.py`: runs a fixed list of rec, q and lab-server
  commands, wrong input among them, in order on a new lab of `make_scratch.py`,
  and compares what they print and their exit statuses with `checks/check_commands.txt`. When a change
  to what they print is meant, `--update` rewrites the file, and its diff shows the change. The
  output depends on the Python version, which `.python-version` pins.
- `uv run --script checks/check_pages.py [URL]`: every page of a running server, and its diagrams,
  audio players and images in Chrome, light and dark.
- `evals/run.sh [--case NAME] [--model sonnet]`: the skill's eval cases, run by agents with and
  without the plugin; on Linux it needs `bubblewrap` and `socat`. The whole suite costs about $5 on
  Sonnet; `evals/baseline.md` has the last full run. `--keep-temp` keeps each run's folder,
  with its `lab.db` and transcript. The runs' sandbox hides the repository that holds the plugin,
  the real home folder and the network, which is why `evals/run.sh` installs the clone into a venv
  of its own and `evals/lab.sh` copies the package and DuckDB's SQLite extension into the run's home
  folder. `claude plugin eval` takes the cases only from a folder inside the plugin's, with no links,
  so `evals/run.sh` runs a copy of `plugin/` with `evals/` copied into it.

Every function in `experiment_lab/` and `checks/` has a cyclomatic complexity of B or less (at most
10): `uvx radon cc experiment_lab checks -n C --show-closures` lists none.

Try changes on a data folder and port of their own, with the commands of `.venv`:

```sh
export PATH=$PWD/.venv/bin:$PATH EXPERIMENT_LAB_DATA=$(mktemp -d) EXPERIMENT_LAB_PORT=5001
bin/start.sh
rec project test --text "Trying a change."    # its folder is this clone
rec experiment first --text "What it tries."
rec add question "Which rate?" --in first --text "..."
q "SELECT id, kind, status, title FROM records"    # pages at http://127.0.0.1:5001/lab
```

For a lab with data already in it, make one with `checks/make_scratch.py` and set
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
  `views` (a CHECK in `schema.sql`). `lab.working_project` gives the project named (`--project`,
  `Lab(project=)`), else the project of the current folder (`lab.holding`), for `Lab.current` and q.
- Writers: `lab.py` (projects, experiments and runs) and `records.py` (records) use only the
  standard library and Python's sqlite3, so a logging script imports them with `PYTHONPATH` alone.
  Every write is one `Lab.write()` transaction (`BEGIN IMMEDIATE`). `settings.py` and `__init__.py`
  must stay importable without the package's dependencies too.
- Readers: `query.py` attaches `lab.db` read-only to an in-memory DuckDB, makes the macro
  `current_project()` and runs `lab.sql`, whose views keep that project's rows, then the project's
  view files, so that each project's views are in a database of their own and may reuse names. With
  no project, `lab.sql`'s views hold every project's rows. q, the pages and `checks/check_views.py`
  all read through it.
- No process may have `lab.db` open with Python's sqlite3 while DuckDB reads it, since the two copies
  of SQLite do not see each other's locks (`query.py`'s docstring). So lab-server makes `lab.db`, or
  the tables it lacks, and deletes a project (`python -m experiment_lab.lab --delete NAME`) in a
  child process.
- The pages (`pages.py`) keep one DuckDB database per project (`Views`), made again when
  `query.version` changes (the modification time of `lab.sql` or a view file, or the list of view
  files), and give each request a cursor of the project in the query parameter `project`, else in
  the cookie `project`; the pages of an experiment, a run or a record and the Projects page read
  the database of no project (`every_project`). Attaching costs
  about 140 ms, since each new DuckDB database loads the SQLite extension again. File access
  is off and the configuration locked. SQL from the query box and from `sql:` evidence runs in a
  transaction that is rolled back; `REFUSED` lists the statement types that would act outside it.
- The templates only lay out what `pages.py` gives them. `pages.py` writes every sentence, number,
  link (`url`, `file_url`, `view_url` and `query_url`, which the templates may call) and piece of
  SQL as the page shows it, and a name in SQL text through `query.literal` or `query.identifier`. A
  template's `if` only shows or hides markup or picks an attribute or a class.
- `lab.sql` is the one set of views, with the macro `local_time`, that q and the pages run. DuckDB 1.5.5 returned wrong rows for
  a filter over attached SQLite tables (`checks/sqlite_filter_bug.py`), which is why
  `checks/check_views.py` exists.

## What is written in more than one place

Change these together:

- The tables: `schema.sql`; the inserts in `lab.py` and `records.save`; the views in `lab.sql`.
- The record kinds and statuses: the CHECKs in `schema.sql`, and `KINDS` (in the order the pages
  show them) and `STATUSES` in `records.py`.
- The evidence kinds: `EVIDENCE` in `records.py`, `record_page` in `pages.py` and
  `templates/lab/record.html`.
- What rec prints after `rec project` (`project NAME, folder ...`) and `rec experiment`
  (`experiment PROJECT › ... › NAME`): `rec.py` and the graders in `evals/*/case.yaml`,
  which match it.
- The order of experiments below the same parent (`created, name`): `rec.tree`, `pages.tree` and
  the Branches list of `pages.experiment`.
- The DuckDB version: `pyproject.toml`, `checks/check_views.py`, `checks/sqlite_filter_bug.py`.
- The Playwright version: `checks/check_pages.py` and the `dev` group of `pyproject.toml`.
- The Markdown version: `markdown` in the dependencies of `pyproject.toml` and `types-Markdown`, its
  stubs, in the `dev` group.
- The usage of q, rec and `lab.py`, the record fields and the views' columns: the module docstrings
  of `q.py`, `rec.py` and `lab.py`, `README.md`, and `plugin/skills/experiment-db/SKILL.md`, which
  is what an agent using the lab reads.
- The Mermaid file name, `experiment_lab/static/mermaid-<version>.min.js`, in
  `templates/lab/base.html`; its version must match the one mermaid-cli uses.
