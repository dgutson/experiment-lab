# Runs whose work runs elsewhere

For work that runs outside the Python that logs it (a background job or agent, another tool),
`rec run EXPERIMENT NAME [--note T]` before it starts prints the run's id. When it ends,
`rec end <run id> [--metric K=V ...] [--tag K=V ...] [--file PATH[=NAME] ...] [--status FAILED|KILLED]`
logs its results and ends the run (metrics at step 0; NAME, the file's path in the run, its own
name unless given). A result that needs computing goes through a script instead:
`run = lab.run_by_id("<run id>")`, then `run.log_metrics(...)`, `run.log_file(...)`, `run.end()`.

`rec end --at "2026-09-10 12:58"` (`run.end(at=...)`) gives an end earlier than now, and
`rec run --start T` (`lab.run(start=...)`) a start earlier than now. A time, in rec and in `lab.py`
alike, is ISO 8601 text, local unless it ends in `Z` or an offset: `"2026-09-10 12:00"`, or
`2026-09-10T10:00Z` as it is, with no conversion by hand; in Python it may be a `datetime` too. A
run logged after the work without its times shows a duration of seconds, which the pages count as
measured.
