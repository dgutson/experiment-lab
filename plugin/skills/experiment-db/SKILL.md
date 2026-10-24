---
name: experiment-db
description: "Experiment data kept in the lab's database, lab.db: runs, parameters, metrics, decisions, findings, protocol rules and open questions. Use it to find past experiments by their text and answer any question about an experiment's state or numbers with one SQL call (q), to record a decision, finding, rule or question (rec), and to log runs from Python (experiment_lab.lab), before writing a script over result files."
---

# Experiment database

Answer from `q` before writing a new script. To find past experiments, runs or records about a
subject, search the view `texts` first, then query the numbers of what it finds.

- `q`, `rec` and `lab-server` are commands of experiment-lab, installed with `uv tool install` from the
  clone at `~/experiment-lab`. If `q` is not on the PATH, ask the owner where experiment-lab is.
  The data folder is `$EXPERIMENT_LAB_DATA`, default `~/lab-data`; the database is its `lab.db`.
- `q "SQL"` runs DuckDB SQL over the views (those of `experiment_lab/lab.sql`, then those of the
  files listed under `views` in the data folder's `lab.toml`) on `lab.db`, attached read-only as the
  catalog `lab`, and prints at most 40 rows (`--max N`, `--full` for long cells). `q .views` lists
  the views and their columns. The tables are `lab.experiments`, `lab.runs`, `lab.params`,
  `lab.tags`, `lab.metrics` (every value logged, with `step` and `time`) and `lab.records`.
- `rec experiment NAME --text DESCRIPTION` makes an experiment (or sets its description). A new
  experiment needs a description, in Markdown: what it measures and why, in the words a later
  search would use. `rec add KIND "TITLE" --in EXPERIMENT --text T [--experiment LABEL] --by WHO
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
  lab.experiment("my project", "What it measures and why.")   # a new one needs a description
  with lab.run("my project", "run 1", params={"rate": 48000}, note="What this run tries") as run:
      run.log_metrics({"accuracy": 0.91}, step=0)  # every value is kept
      run.set_tags({"outcome": "pass"})
      run.log_file("table.html")                 # copied to the data folder's files/<run id>/
  ```

  A run ends FINISHED when the `with` block ends and FAILED on an exception. To attach files or
  numbers to a record, log them as a run and cite it as `run:<run id>`.
- The owner reads the same records, experiments, runs and views at http://127.0.0.1:5000/lab, and
  searches them there with a box that searches `texts` the same way.
  Record texts, notes, descriptions and `.md` files there are Markdown; a ```` ```mermaid ```` block
  is drawn as a diagram by mermaid 11.17.2, the version mmdc uses, so check a diagram with
  `mmdc -i x.mmd -o x.svg` before recording it.
- Quote column names that are SQL keywords (`by`, `load`) with double quotes.

| View | One row per | Columns |
|---|---|---|
| `runs` | run | run, experiment, name, status, start_time (ms), tags, params, metrics (maps: `metrics['accuracy']` is the latest value, `params['lr']`), end_time, note |
| `records` | record | id, time, experiment (the label it is shown under), kind, title, text, author, status, answers, evidence |
| `texts` | experiment, run and record | kind (`experiment`, `run` or `record`), id, experiment, title, text: an experiment's description; a run's note, then its params and tags as `key=value` lines; a record's text |

The project's own views are in the files that `lab.toml` lists under `views`; read those files for
what each view holds.

```sql
SELECT kind, id, experiment, title FROM texts WHERE contains(lower(title || ' ' || text), 'sample rate');
SELECT id, kind, status, title FROM records WHERE status = 'open' OR kind = 'decision' ORDER BY time;
SELECT experiment, count(*) AS runs FROM runs GROUP BY ALL ORDER BY 2 DESC;
SELECT key, step, value FROM lab.metrics WHERE run = '<run id>' ORDER BY key, step;
```
