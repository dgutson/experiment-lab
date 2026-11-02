---
name: experiment-db
description: "Experiment data kept in the lab's database, lab.db: projects with their experiment trees, runs, parameters, metrics, decisions, findings, protocol rules and open questions. Use it to find past experiments by their text and answer any question about an experiment's state or numbers, or about a project's views, with one SQL call (q), to record a decision, finding, rule or question (rec), and to log runs from Python and read them back in a script (experiment_lab.lab), before writing a script over result files."
---

# Experiment database

Answer from `q` before writing a new script. To find past experiments, runs or records about a
subject, search the view `experiments_runs_records` with `holds_words` first (`records` for records
only), then query the numbers of what it finds.

An experiment, in order:

1. `rec tree` prints the project's experiments, one per line, each indented below the one it came from
   (`rec tree EXPERIMENT`, only those below EXPERIMENT), then the protocol rules the experiment follows.
2. `rec experiment NAME --text DESCRIPTION`, with `--in PARENT` when it follows from the result of
   PARENT, an experiment of `rec tree`; without it for an idea unrelated to the earlier ones.
3. When a protocol rule asks for a prediction, `rec add question "Will ...?" --in NAME --text
   "the outcome expected, and why"` before the first run.
4. The work, inside `with lab.run(...)` (below), so that the lab measures how long each run took.
   For work that runs elsewhere (a background job or agent, another tool), read
   [references/runs-elsewhere.md](references/runs-elsewhere.md) before it starts.
5. `rec add finding "TITLE" --in NAME --text "what the runs showed" --evidence run:<run id>`, with
   `--answers <question id>` when step 3 wrote one.
6. If the idea failed, `rec experiment NAME --dead-end`, and the finding says why.

- `q` and `rec` are on the PATH, and `lab.py` is in the clone at `~/experiment-lab`; run them in the
  project's folder, not the clone's. If `q` is not on the PATH, ask the owner where experiment-lab is.
  They work in the project whose folder holds the current folder; when none does, their error lists
  the projects' folders and gives the command that makes one.
- `q "SQL"` runs DuckDB SQL over the project's views (the table below, then the project's view
  files; `--project P` for another project's, `--all` for every project's) on `lab.db`, attached
  read-only as the catalog `lab`, and prints the last statement's rows, at most 40 (`--max N`,
  `--full` for long cells); one cell prints whole, as stored
  (`q "SELECT text FROM records WHERE id = '...'"`). `q .views` lists the views and their columns,
  and `q .keys` the keys of the project's runs' tags, params and metrics, with how many runs have
  each; a key no run has gives NULL, with no error. The tables `lab.experiments`, `lab.runs`,
  `lab.params`, `lab.tags`, `lab.metrics`, `lab.files` and `lab.records` hold every project's rows,
  only as stored: query them for what the views leave out, such as every value logged of a metric,
  with its `step` and `time` (`lab.metrics`).
- `rec experiment NAME --text DESCRIPTION [--in PARENT] [--dead-end]` makes an experiment, or
  changes it; the description, in Markdown, says what it measures and why, in the words a later
  search would use. `rec add KIND "TITLE" --in EXPERIMENT --text T [--answers ID] [--evidence ITEM
  ...]` records a `decision`, `finding`, `protocol` rule or `question`, with `--in PROJECT` when no
  experiment fits; `--answers` closes that question. Evidence items: `run:<run id>`, `file:<~ or
  absolute path>`, `sql:<query over the views>`, `record:<id>`. `rec set ID FIELD=VALUE`
  (`status=closed`, `evidence=a;b`) changes a record; `rec open` lists the open ones. rec writes
  `lab.db` itself, with no server, and a text has no length limit; `rec COMMAND --help` gives every
  option. `--text -` reads the text from stdin, and so does `FIELD=-` of `rec set`, so a text with
  line breaks, quotes or backticks goes in a quoted heredoc, which needs no escaping:

  ```sh
  rec add finding "TITLE" --in NAME --evidence run:<run id> --text - <<'EOF'
  What the runs showed, in Markdown.
  EOF
  rec set ID text=- <<'EOF'
  The whole new text.
  EOF
  ```
- Evidence must show later what it showed when the record was written. Log a file that a later run
  or checkout can overwrite, and numbers, in a run (`run.log_file`, below) and cite `run:<run id>`;
  `file:` is for a file that stays as it is, such as one committed to git.
- To log runs, use the module `experiment_lab.lab` (standard library only):
  `PYTHONPATH=~/experiment-lab python3 script.py`, with

  ```python
  from experiment_lab.lab import Lab
  lab = Lab()
  with lab.run("rate test", "run 1", params={"rate": 48000}, note="What this run tries") as run:
      run.log_metrics({"accuracy": 0.91}, step=0)  # every value is kept
      run.set_tags({"outcome": "pass"})
      run.log_file("table.html")                 # copied to the data folder's files/<run id>/
      run.log_file("out/a.json", name="case-1/a.json")  # name: its path there, which may hold folders
      run.log_json("result.json", {"passed": [1, 3]})  # an object or text in memory: log_json, log_text
      print(run.id)                              # what evidence cites as run:<run id>
  ```

  Open the run before the work starts, with the work inside the `with` block; the owner's pages add
  up an experiment's run durations. A run ends FINISHED
  when the `with` block ends and FAILED on an exception. A result loaded rather than computed again
  is not a new run: add to the run that produced it, `lab.run_by_id("<run id>")`, with `set_tags`
  or `log_metrics`. N measurements of one number in a run (one
  per session or repetition) go under one key with `step=` set to each, and their mean or count
  under a key of its own: `runs.metrics` holds a key's value of the highest step, `lab.metrics`
  every step. Run `q .keys` before naming a new key, and reuse the project's spelling. A result
  that is not one number, such as each judge's choice on each criterion, goes in a JSON file
  logged with `run.log_json`, not in many metric keys; q reads it through its path in the view
  `files` (a table function takes no column):
  `q "SET VARIABLE f = (SELECT path FROM files WHERE run = '<run id>' AND name = 'x.json');
  FROM read_json(getvariable('f'))"`.
- A script reads runs back with `lab.runs`, not by opening lab.db itself:

  ```python
  for run in lab.runs("rate test", tags={"outcome": "pass"}, latest_by="rate"):  # FINISHED, oldest first
      print(run.id, run.params["rate"], run.metrics["accuracy"])  # params and tags are text
      result = lab.json(run.id, "case-1/a.json")  # lab.file(run.id, name): the file's path
  ```

  Every keyword is optional: `status=None` (any status), `params=` and `tags=` (`{"rate": (16000,
  48000)}`: a value or a tuple of values), `has_metric="loss"`, and `latest_by`, a param or a list,
  which keeps the latest run for each combination of their values, as `latest_runs`.
- The owner reads the same data on pages at http://127.0.0.1:5000/lab. Texts are Markdown; before
  putting a diagram, a file path or an image in one, read [references/pages.md](references/pages.md).
- Quote column names that are SQL keywords (`by`, `load`) with double quotes.

| View | One row per | Columns |
|---|---|---|
| `experiments` | experiment | name, project, parent, depth (0 for a project), path (`a › b › c`, from the project down to the experiment itself), dead_end, description, created |
| `runs` | run | run, experiment, project, name, status (RUNNING, FINISHED, FAILED or KILLED), start_time (ms), tags, params, metrics (maps: `metrics['accuracy']` is the latest value, a double; `params['lr']` and tags are text, so `params['lr']::DOUBLE` for a number), end_time (ms), started, ended, minutes (NULL until the run ends), note |
| `files` | file logged for a run | run, experiment, project, name, bytes, path (the copy's absolute path, `files/<run id>/<name>` in the data folder) |
| `records` | record | id, time, time_ms (ms, to compare with a run's start_time), project, experiment (the label it is shown under), kind, title, text, author, status, answers, evidence (items joined by `; `) |
| `experiments_runs_records` | experiment, run and record | kind (`experiment`, `run` or `record`), id, project, experiment, title, text: an experiment's description; a run's note, then its params and tags as `key=value` lines; a record's text |

`created`, `time`, `started` and `ended` are local time, to the minute (`2026-08-31 21:03`).
`local_time(ms)` writes any time of the tables (milliseconds, as `lab.metrics.time`) the same way.
`holds_words(text, words)` is true when text holds every word of words, in any letter case.
`latest_runs(experiment_name, keys)` is the experiment's latest FINISHED run, by start time, for
each combination of the values of the params named in the list keys, as rows of `runs`.

The project's own view files are listed in its row of `lab.experiments`, column `views`; read
those files for what each view holds. A query asked again and again goes in a view file
([references/view-files.md](references/view-files.md)).

```sql
SELECT kind, id, experiment, title FROM experiments_runs_records WHERE holds_words(title || ' ' || text, 'sample rate');
SELECT id, kind, status, title FROM records WHERE holds_words(title || ' ' || text, 'sample rate');
SELECT id, kind, status, title FROM records WHERE status = 'open' OR kind = 'decision' ORDER BY time;
SELECT experiment, count(*) AS runs FROM runs GROUP BY ALL ORDER BY 2 DESC;
SELECT name, params['rate'] AS rate, metrics['accuracy'] AS accuracy FROM latest_runs('rate test', ['rate']);
SELECT key, step, value FROM lab.metrics WHERE run = '<run id>' ORDER BY key, step;
```
