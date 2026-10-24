-- Views over the lab's database for q and the pages, which attach lab.db as
-- the catalog lab (experiment_lab/query.py): lab.experiments, lab.runs,
-- lab.params, lab.tags, lab.metrics, lab.records. The view files listed under
-- views in the data folder's lab.toml run after this one and may use its views.

-- One row per run: tags, params and the latest value of each metric as maps
-- (metrics['machine/cpu_busy_mean_pct']), in key order. A metric's latest
-- value is the one of the highest step, then of the latest time.
CREATE VIEW runs AS
WITH t AS (SELECT run, map_from_entries(list(struct_pack(k := key, v := value) ORDER BY key)) AS tags
           FROM lab.tags GROUP BY run),
     p AS (SELECT run, map_from_entries(list(struct_pack(k := key, v := value) ORDER BY key)) AS params
           FROM lab.params GROUP BY run),
     latest AS (SELECT run, key, arg_max_null(value, (step, time, value)) AS value FROM lab.metrics GROUP BY run, key),
     m AS (SELECT run, map_from_entries(list(struct_pack(k := key, v := value) ORDER BY key)) AS metrics
           FROM latest GROUP BY run)
SELECT r.id AS run, r.experiment, r.name, r.status, r.start_time, t.tags, p.params, m.metrics, r.end_time, r.note
FROM lab.runs r LEFT JOIN t ON t.run = r.id LEFT JOIN p ON p.run = r.id LEFT JOIN m ON m.run = r.id;

-- Decisions, findings, protocol rules and questions. experiment is the label a
-- record is shown under; lab.records.experiment is the experiment it is filed in.
CREATE VIEW records AS SELECT id, strftime(to_timestamp(time / 1000), '%Y-%m-%dT%H:%M') AS time,
    label AS experiment, kind, title, text, author, status, answers, evidence
    FROM lab.records;

-- The text of each experiment, run and record, for searching: an experiment's
-- description; a run's note, then its params and tags as key=value lines; a
-- record's text. kind is experiment, run or record, and id the name or id that
-- its page (/lab/<kind>/<id>) takes.
CREATE VIEW texts AS
SELECT 'experiment' AS kind, name AS id, name AS experiment, name AS title, description AS text FROM lab.experiments
UNION ALL
SELECT 'run', r.id, r.experiment, r.name, concat_ws(chr(10), r.note,
    (SELECT string_agg(key || '=' || value, chr(10) ORDER BY key) FROM lab.params p WHERE p.run = r.id),
    (SELECT string_agg(key || '=' || value, chr(10) ORDER BY key) FROM lab.tags t WHERE t.run = r.id))
FROM lab.runs r
UNION ALL
SELECT 'record', id, experiment, title, text FROM records;
