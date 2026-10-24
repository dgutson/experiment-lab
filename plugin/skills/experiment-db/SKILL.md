---
name: experiment-db
description: "Experiment data kept in the lab's database, lab.db: projects with their experiment trees, runs, parameters, metrics, decisions, findings, protocol rules and open questions. Use it to find past experiments by their text and answer any question about an experiment's state or numbers, or about a project's views, with one SQL call (q), to record a decision, finding, rule or question (rec), and to log runs from Python (experiment_lab.lab), before writing a script over result files."
---

# Experiment database

Answer from `q` before writing a new script. To find past experiments, runs or records about a
subject, search the view `texts` first, then query the numbers of what it finds.

An experiment, in order:

1. `rec tree` prints the project's experiments, one per line, each indented below the one it came from.
2. `rec experiment NAME --text DESCRIPTION`, with `--in PARENT` when it follows from the result of
   PARENT, an experiment of `rec tree`; without it for an idea unrelated to the earlier ones.
3. The work, inside `with lab.run(...)` (below), so that the lab measures how long each run took.
4. `rec add finding "TITLE" --in NAME --text "what the runs showed" --evidence run:<run id>`.
5. If the idea failed, `rec experiment NAME --dead-end`, and the finding says why.

- `q`, `rec` and `lab-server` are commands of experiment-lab, installed with `uv tool install` from the
  clone at `~/experiment-lab`. If `q` is not on the PATH, ask the owner where experiment-lab is.
  The data folder is `$EXPERIMENT_LAB_DATA`, default `~/lab-data`; the database is its `lab.db`.
- The lab holds several projects. q, rec and `lab.py` work in the project of the current folder
  (the one whose folder holds it or its git repository's main worktree). If rec says no project
  holds the folder, make it once there: `rec project NAME --text "what the project is, in one line"`.
- `q "SQL"` runs DuckDB SQL over the views (those of `experiment_lab/lab.sql`, then the project's
  view files; `--project P` for another project's) on `lab.db`, attached read-only as the catalog
  `lab`, and prints at most 40 rows (`--max N`, `--full` for long cells). `q .views` lists the views
  and their columns. The views of `lab.sql` hold every project's rows; filter on their column
  `project`. The tables are `lab.experiments`, `lab.runs`, `lab.params`, `lab.tags`, `lab.metrics`
  (every value logged, with `step` and `time`) and `lab.records`.
- `rec experiment NAME --text DESCRIPTION [--in PARENT] [--dead-end | --no-dead-end]` makes an
  experiment, or changes it when it exists. A new experiment needs a description, in Markdown: what
  it measures and why, in the words a later search would use.
  `rec add KIND "TITLE" --in EXPERIMENT --text T [--experiment LABEL] [--by WHO]
  [--answers ID] [--evidence ITEM ...]` records a decision, finding, protocol rule or question in an
  existing experiment; its text may not be empty, and `--answers` closes that question. `rec set ID FIELD=VALUE` (`status=closed`,
  `title=...`, `evidence=a;b`); `rec open`. Evidence items: `run:<run id>`, `file:<~ or absolute
  path>`, `sql:<query over the views>`, `record:<id>`. rec writes `lab.db` itself; no server is
  needed, and a text has no length limit.
- To log runs, use the module `experiment_lab.lab` (standard library only):
  `PYTHONPATH=~/experiment-lab python3 script.py`, with

  ```python
  from experiment_lab.lab import Lab
  lab = Lab()
  lab.experiment("rate test", "What it measures and why.")   # parent="..." as rec's --in
  with lab.run("rate test", "run 1", params={"rate": 48000}, note="What this run tries") as run:
      run.log_metrics({"accuracy": 0.91}, step=0)  # every value is kept
      run.set_tags({"outcome": "pass"})
      run.log_file("table.html")                 # copied to the data folder's files/<run id>/
      print(run.id)                              # what evidence cites as run:<run id>
  ```

  Open the run before the work starts, with the work inside the `with` block; the owner's pages add
  up an experiment's run durations. A run ends FINISHED
  when the `with` block ends and FAILED on an exception. To attach files or
  numbers to a record, log them as a run and cite it as `run:<run id>`.
- The owner reads the same records, experiments, runs and views at http://127.0.0.1:5000/lab, and
  searches them on its Experiments page, which searches `texts` the same way.
  Record texts, notes, descriptions and `.md` files there are Markdown; a ```` ```mermaid ```` block
  is drawn as a diagram by mermaid 11.17.2, the version mmdc uses, so check a diagram with
  `mmdc -i x.mmd -o x.svg` before recording it.
- Quote column names that are SQL keywords (`by`, `load`) with double quotes.

| View | One row per | Columns |
|---|---|---|
| `experiments` | experiment | name, project, parent, depth (0 for a project), path (`a › b › c`), dead_end, description, created |
| `runs` | run | run, experiment, project, name, status, start_time (ms), tags, params, metrics (maps: `metrics['accuracy']` is the latest value, `params['lr']`), end_time, note |
| `records` | record | id, time, project, experiment (the label it is shown under), kind, title, text, author, status, answers, evidence |
| `texts` | experiment, run and record | kind (`experiment`, `run` or `record`), id, project, experiment, title, text: an experiment's description; a run's note, then its params and tags as `key=value` lines; a record's text |

The project's own view files are listed in its row of `lab.experiments`, column `views`; read
those files for what each view holds.

```sql
SELECT kind, id, experiment, title FROM texts WHERE project = 'my-project' AND contains(lower(title || ' ' || text), 'sample rate');
SELECT id, kind, status, title FROM records WHERE project = 'my-project' AND (status = 'open' OR kind = 'decision') ORDER BY time;
SELECT experiment, count(*) AS runs FROM runs WHERE project = 'my-project' GROUP BY ALL ORDER BY 2 DESC;
SELECT key, step, value FROM lab.metrics WHERE run = '<run id>' ORDER BY key, step;
```
