---
name: experiment-db
description: "Experiment data kept in the local MLflow server's database: runs, parameters, metrics, decisions, findings, protocol rules and open questions. Use it to answer any question about an experiment's state or numbers with one SQL call (q), and to record a decision, finding, rule or question (rec), before writing a script over result files or MLflow's REST API."
---

# Experiment database (MLflow)

Answer from `q` before writing a new script.

- `~/mlflow-lab` is the clone of mlflow-lab that the MLflow server was installed from. If
  `~/mlflow-lab/bin/q` does not exist, ask the owner where the clone is and use its `bin/` instead.
  The data folder is `$MLFLOW_LAB_DATA`, default `~/mlflow`.
- `~/mlflow-lab/bin/q "SQL"` runs DuckDB SQL over the views (those of `mlflow_lab/lab.sql`, then
  those of the files listed under `views` in the data folder's `lab.toml`) on the data folder's
  `mlflow.db`, attached read-only, and prints at most 40 rows (`--max N`, `--full` for long cells).
  `q .views` lists the views and their columns. MLflow's own tables are in the schema `mlflow`
  (`mlflow.runs`, `mlflow.tags`, `mlflow.params`, `mlflow.latest_metrics`, `mlflow.experiments`).
- `~/mlflow-lab/bin/rec add KIND "TITLE" --in EXPERIMENT [--experiment LABEL] --text T --by WHO
  [--answers ID] [--evidence ITEM ...]` records a decision, finding, protocol rule or question as a
  run of the MLflow experiment EXPERIMENT, which must exist; `--answers` closes that question.
  `rec set ID FIELD=VALUE` (`status=closed`, `title=...`, `evidence=a;b`); `rec open`. Evidence
  items: `mlflow:<run id>`, `file:<~ or absolute path>`, `sql:<query over the views>`,
  `record:<id>`. rec writes to the server at `$MLFLOW_TRACKING_URI`, default
  http://127.0.0.1:5000.
- The owner reads the same records and views at http://127.0.0.1:5000/lab. Record texts and `.md`
  files there are Markdown; a ```` ```mermaid ```` block is drawn as a diagram by mermaid 11.17.2,
  the version mmdc uses, so check a diagram with `mmdc -i x.mmd -o x.svg` before recording it.
  MLflow cuts a record text longer than 8000 characters.
- Quote column names that are SQL keywords (`by`, `load`) with double quotes.

| View | One row per | Columns |
|---|---|---|
| `runs` | MLflow run | run, experiment (name), name, status, start_time (ms), tags, params, metrics (maps: `metrics['accuracy']`, `tags['mlflow.note.content']`, `params['lr']`) |
| `records` | record | id, time, experiment (label), kind, title, text, author, status, answers, evidence, run |

The project's own views are in the files that `lab.toml` lists under `views`; read those files for
what each view holds.

```sql
SELECT id, kind, status, title FROM records WHERE status = 'open' OR kind = 'decision' ORDER BY time;
SELECT experiment, count(*) AS runs FROM runs GROUP BY ALL ORDER BY 2 DESC;
```
