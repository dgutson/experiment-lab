-- The SQLite SQL with which Lab.runs (lab.py) reads the runs of the experiment
-- given as the parameter, in this order: the runs (by start time, then id),
-- their params, their tags, and the latest value of each of their metrics as
-- the view runs of lab.sql takes it: of the highest step, then time, then
-- value, NULL the highest as in DuckDB. lab.py splits this file at each
-- semicolon, so no comment or string holds one.

SELECT id, name, status, start_time, end_time, note FROM runs WHERE experiment = ? ORDER BY start_time, id;

SELECT x.run, x.key, x.value FROM params AS x JOIN runs AS r ON x.run = r.id WHERE r.experiment = ? ORDER BY x.key;

SELECT x.run, x.key, x.value FROM tags AS x JOIN runs AS r ON x.run = r.id WHERE r.experiment = ? ORDER BY x.key;

SELECT m.run, m.key, m.value FROM (
    SELECT x.run, x.key, x.value, row_number() OVER (
        PARTITION BY x.run, x.key ORDER BY x.step DESC, x.time DESC, x.value IS NULL DESC, x.value DESC) AS n
    FROM metrics AS x JOIN runs AS r ON x.run = r.id WHERE r.experiment = ?) AS m
WHERE m.n = 1 ORDER BY m.key;
