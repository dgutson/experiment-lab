-- Views over MLflow's database for q and the /lab pages. Both define the
-- schema mlflow before running this file, with MLflow's tables experiments,
-- experiment_tags, runs, tags, params and latest_metrics: q as views over the
-- database attached read-only (bin/q.py), the pages as tables copied from a
-- snapshot of it (mlflow_lab/store.py). The files listed under views in the
-- data folder's lab.toml run after this one and may use its views.

-- One row per run, deleted runs left out: tags, params and metrics as maps
-- (metrics['machine/cpu_busy_mean_pct']).
CREATE VIEW runs AS
WITH t AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS tags FROM mlflow.tags GROUP BY run_uuid),
     p AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS params FROM mlflow.params GROUP BY run_uuid),
     m AS (SELECT run_uuid, map_from_entries(list(struct_pack(k := key, v := value))) AS metrics FROM mlflow.latest_metrics GROUP BY run_uuid)
SELECT r.run_uuid AS run, e.name AS experiment, r.name, r.status, r.start_time, t.tags, p.params, m.metrics
FROM mlflow.runs r JOIN mlflow.experiments e USING (experiment_id)
LEFT JOIN t USING (run_uuid) LEFT JOIN p USING (run_uuid) LEFT JOIN m USING (run_uuid)
WHERE r.lifecycle_stage = 'active';

-- Decisions, findings, protocol rules and questions: runs tagged record.kind, named "<kind>: <title>".
CREATE VIEW records AS SELECT tags['record.id'] AS id, strftime(to_timestamp(start_time / 1000), '%Y-%m-%dT%H:%M') AS time,
    tags['record.experiment'] AS experiment, tags['record.kind'] AS kind,
    regexp_replace(name, '^' || tags['record.kind'] || ': ', '') AS title, tags['mlflow.note.content'] AS text,
    tags['record.by'] AS author, tags['record.status'] AS status, tags['record.answers'] AS answers,
    tags['record.evidence'] AS evidence, run
    FROM runs WHERE tags['record.kind'] IS NOT NULL;
