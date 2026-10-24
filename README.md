# experiment-lab

Experiments' runs, and the decisions, findings, protocol rules and open questions about them, in one
SQLite database, `lab.db`. `q` answers questions about runs and records with one DuckDB SQL query;
`rec` adds and changes records; logging scripts write runs with the module `experiment_lab.lab`;
pages at `/lab` show the records with their evidence, the experiments and their runs, a search of
their texts, the SQL views as tables, a read-only query box and the experiments' files. It is meant for a coding agent and a
person working on the same experiments: the agent reads and writes with `q` and `rec`, the person
reads the pages.

| Path | What it is |
|---|---|
| `experiment_lab/` | the package; its `__init__.py` lists what each module holds |
| `experiment_lab/schema.sql` | the tables of `lab.db` |
| `experiment_lab/lab.sql` | the views `runs` and `records`, which q and the pages query |
| `experiment_lab/lab.py` | logging experiments and runs from Python, with the standard library only |
| `bin/start.sh` | starts the pages' server, `lab-server`, in the background |
| `bin/check_pages.py` | opens every page of a running server and checks it |
| `bin/check_views.py`, `bin/sqlite_filter_bug.py` | checks to run before changing the DuckDB version |
| `plugin/` | a Claude Code plugin holding the skill `experiment-db`, which tells the agent how to use q, rec and `lab.py` |

The commands, installed with the package: `q`, `rec`, `lab-server` and `lab-from-mlflow`.

## Records

A record is a row of the table `records`: an id, the experiment it is filed in, the label it is
shown under (the experiment's name unless given), a kind (decision, finding, protocol or question),
a title, a text in Markdown, an author, a status (open, closed or superseded), the question it
answers, and its evidence: items `run:<run id>`, `file:<path>`, `sql:<query over the views>` or
`record:<id>`, joined by `; `. A new question is open and any other record closed unless a status is
given, and a record that answers an open question closes it. The view `records` has one row per
record, with the label as its column `experiment`.

## Searching

The view `texts` has one row per experiment, run and record: its kind, id, experiment, title and
text. An experiment's text is its description; a run's is its note, then its params and tags as
`key=value` lines; a record's is its text. The search box on every page lists those whose title or
text holds every word searched for, in any letter case. An agent searches the same view with q:

```sh
q "SELECT kind, id, experiment, title FROM texts WHERE contains(lower(title || ' ' || text), 'sample rate')"
```

So that the search finds them, a new experiment needs a description (what it measures and why) and
a record a text; `rec`, `lab.py` and `records.py` refuse one without. A run's note is optional.

## Architecture

```mermaid
flowchart LR
    subgraph browser["Owner's browser"]
        labpage[["«component»<br/>/lab pages"]]
        mermaid[["«component»<br/>mermaid.js"]]
    end
    shell(["Agent's shell"])
    scripts(["Experiments'<br/>logging scripts"])

    subgraph server["lab-server process (bin/start.sh)"]
        pages[["«component»<br/>pages<br/>FastAPI under uvicorn"]]
        pagesdb[["«component»<br/>one in-memory DuckDB"]]
    end

    subgraph commands["Commands"]
        rec[["«component»<br/>rec"]]
        q[["«component»<br/>q, with its own<br/>in-memory DuckDB"]]
    end
    lab[["«component»<br/>experiment_lab.lab<br/>and .records"]]

    subgraph data["Data folder (~/lab-data)"]
        labdb[("lab.db<br/>SQLite, WAL mode")]
        files[/"files/"/]
        toml[/"lab.toml"/]
    end

    views[/"lab.sql and view files"/]
    shown[/"the folder lab.toml names"/]

    labpage -->|"HTTP /lab"| pages
    labpage --> mermaid
    mermaid -.->|"fetched from /lab/static/"| pages
    shell --> rec
    shell --> q
    rec --> lab
    scripts --> lab
    lab -->|"sqlite3, writes"| labdb
    lab -->|"copies run files"| files
    q -->|"ATTACH, read-only"| labdb
    q -.->|"reads"| toml
    q -.->|"reads"| views
    pages -->|"a cursor per request"| pagesdb
    pagesdb -->|"ATTACH, read-only"| labdb
    pagesdb -.->|"reads"| views
    pages -.->|"reads"| toml
    pages -.->|"/lab/run/ID/file/"| files
    pages -.->|"/lab/file"| shown
```

There is one database of record, `lab.db`. The DuckDB databases hold no data, only the views over
it.

| Database | Engine | Where | Written by | Read by | Lasts |
|---|---|---|---|---|---|
| `lab.db` | SQLite, WAL mode | data folder | `experiment_lab.lab` and `experiment_lab.records`, with Python's sqlite3: rec, the logging scripts, lab-from-mlflow | q and the pages, through DuckDB | always |
| the pages' database | DuckDB, in memory | the lab-server process | the pages: `lab.db` attached read-only as the catalog `lab`, then the views | the pages, a cursor per request, with file access off | until `lab.sql`, `lab.toml` or a view file changes |
| q's database | DuckDB, in memory | q's process | q: `lab.db` attached read-only as the catalog `lab`, then the views | the SQL given to q | one q call |

Several processes may write at once and no server is needed for it: each write is one short
transaction that starts with `BEGIN IMMEDIATE` and waits up to 60 s for another writer to finish.
In WAL mode readers do not block writers and see only finished transactions.

DuckDB reads `lab.db` through its own copy of SQLite, which it downloads once as its
`sqlite_scanner` extension into `~/.duckdb/extensions/`. Two copies of SQLite in one process do not
see each other's locks (https://www.sqlite.org/howtocorrupt.html, section 2.2.1), so no process
opens `lab.db` with both: the writers use only Python's sqlite3, and q and the pages only DuckDB.
That is why lab-server makes a missing `lab.db` in a child process.

## Setting up a computer

It needs Linux (start.sh uses `setsid` and `ionice`), [uv](https://docs.astral.sh/uv/), and network
access the first time q or the pages read the database, when DuckDB downloads its SQLite reader.

```sh
git clone https://github.com/dgutson/experiment-lab ~/experiment-lab
uv tool install --editable ~/experiment-lab
~/experiment-lab/bin/start.sh
rec experiment "my project" --text "What it measures."
rec add question "Which sample rate do we keep?" --in "my project" --text "..."
q "SELECT id, kind, status, title FROM records"
```

Then open http://127.0.0.1:5000/lab. `lab.db` and its tables are made by the first program that
writes, and by lab-server when it starts.

The install is editable, so a change to the clone's Python, templates or views needs no reinstall:
q and rec read it at once, and lab-server after a restart (a change to `lab.sql` or a view file
shows without one). After changing `pyproject.toml`, reinstall with
`uv tool install --editable ~/experiment-lab --reinstall`.

For a Claude Code agent, install the plugin `experiment-lab`:

```sh
claude plugin marketplace add dgutson/experiment-lab
claude plugin install experiment-lab@experiment-lab-marketplace
```

Its skill, `experiment-lab:experiment-db`, runs `q` and `rec` from the PATH and finds a project's
views through `lab.toml`, so it needs no editing. Eval cases for the plugin go in `plugin/evals/`;
run them with `claude plugin eval ~/experiment-lab/plugin`.

## Logging runs from Python

`experiment_lab/lab.py` uses only Python's standard library, so a logging script imports it from
any Python 3.12 without installing anything: `PYTHONPATH=~/experiment-lab python3 script.py`.

```python
from experiment_lab.lab import Lab

lab = Lab()                                       # $EXPERIMENT_LAB_DATA, else ~/lab-data
lab.experiment("my project", "What it measures and why.")      # a new one needs a description
with lab.run("my project", "run 1", params={"rate": 48000}, note="Markdown") as run:
    run.log_metrics({"accuracy": 0.91}, step=3)   # every value is kept, with its step and time
    run.set_tags({"outcome": "pass"})
    run.log_file("plot.png")                      # copied to files/<run id>/plot.png
```

A run ends FINISHED when its `with` block ends, FAILED on an exception and KILLED on Ctrl-C; without
a `with` block, call `run.end()`. A script that logged with MLflow changes these calls:

| MLflow | experiment-lab |
|---|---|
| `mlflow.set_experiment(name)` | `lab.experiment(name, description)` |
| `mlflow.start_run(run_name=n)` | `lab.run(experiment, n)` |
| `mlflow.log_param(s)` | `run.log_params({...})` |
| `mlflow.log_metric(s)(..., step=s)` | `run.log_metrics({...}, step=s)` |
| `mlflow.set_tag(s)` | `run.set_tags({...})` |
| `mlflow.log_artifact(path)` | `run.log_file(path)` |

To record a decision or finding from a script, `experiment_lab.records.add` does what `rec add`
does.

## The data folder and lab.toml

The lab's files are in the data folder, `$EXPERIMENT_LAB_DATA` (default `~/lab-data`): `lab.db`,
the runs' files in `files/`, `lab.toml`, and start.sh's `server.log`. start.sh serves the pages on
port `$EXPERIMENT_LAB_PORT` (default 5000) and exits at once if a server already answers there.
There is no stop command: kill the `lab-server` process, which outlives the shell that started it.

`lab.toml` is optional:

```toml
# The folder /lab/file shows; nothing outside it is shown. Without it, there is no files page.
files = "~/experiments"
# The project's views, run after lab.sql, in this order.
views = ["~/experiments/views.sql"]
```

Paths may start with `~`; others are taken relative to the data folder. q reads `lab.toml` each
time it runs, the pages whenever it changes.

A view file holds DuckDB `CREATE VIEW` statements over the views `runs` and `records` and the tables
of `lab.db` in the catalog `lab` (`lab.experiments`, `lab.runs`, `lab.params`, `lab.tags`,
`lab.metrics`, `lab.records`). For example:

```sql
-- One row per finished run of "my project", with its accuracy.
CREATE VIEW accuracy AS SELECT name, metrics['accuracy'] AS accuracy
    FROM runs WHERE experiment = 'my project' AND status = 'FINISHED';
```

## Moving from mlflow-lab

`lab-from-mlflow OLD_DATA_FOLDER` copies a data folder of mlflow-lab, where an MLflow server kept
the runs and records in `mlflow.db`, into a new `lab.db` in `$EXPERIMENT_LAB_DATA` (default
`~/lab-data`), which must have none yet. It only reads the old folder, so the MLflow server may keep
running, and it copies `lab.toml` when the new folder has none. Its docstring lists what is copied
and what is left out. It then compares the copy with the old database, table by table and view by
view, and ends with "the copy agrees" or "the copy differs".

The comparison runs the view files on both databases, so they must read only the views `runs` and
`records`, which both define; rewrite a view that reads MLflow's tables (the schema `mlflow`) over
the tables in `lab` first.

## Diagrams

Record texts, notes, descriptions and `.md` files are shown as Markdown, and their
```` ```mermaid ```` blocks are drawn as diagrams in the browser by mermaid.js 11.17.2. The pages
fetch it from the package itself (`experiment_lab/static/`, with its MIT license), so no diagram text
leaves the computer and the server needs no Node. 11.17.2 is the version mermaid-cli 11.16.0 uses,
so `mmdc -i x.mmd -o x.svg` checks a diagram the way the pages draw it. A diagram that does not
parse shows Mermaid's error above its text.

To change the version, take `package/dist/mermaid.min.js` and `package/LICENSE` from
`npm pack mermaid@VERSION`, store them as `experiment_lab/static/mermaid-VERSION.min.js` and
`mermaid-VERSION.LICENSE`, and change the file name in `experiment_lab/templates/lab/base.html`.

## Checking the pages

```sh
uv run --script ~/experiment-lab/bin/check_pages.py [--shots FOLDER] [URL]
```

It opens every page of the server at URL (default `http://127.0.0.1:$EXPERIMENT_LAB_PORT`) and
fails unless each returns 200, or 403 for a file outside the folder `lab.toml` names. It then opens
the pages with diagrams in the installed Google Chrome, in the light and the dark scheme, and checks
that each diagram is drawn or shows Mermaid's error, follows the scheme and fetches nothing from
another host. With `--shots`, it saves screenshots of those pages.

## Changing the DuckDB version

DuckDB is pinned in `pyproject.toml`, `bin/check_views.py` and `bin/sqlite_filter_bug.py`. Set the
new version in the two scripts first and run both while nothing is logged to the lab:

```sh
cd ~/experiment-lab && uv run --script bin/check_views.py && uv run --script bin/sqlite_filter_bug.py
```

check_views.py must end with "the two reads agree" and sqlite_filter_bug.py must say "fixed". Then
set the version in `pyproject.toml`, run `uv lock`, and reinstall:
`uv tool install --editable ~/experiment-lab --reinstall`.
