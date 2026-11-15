-- Views over the lab's database for q and the pages, which attach lab.db as
-- the catalog lab (experiment_lab/query.py): lab.experiments, lab.runs,
-- lab.params, lab.tags, lab.metrics, lab.files, lab.records. Each project's
-- view files (lab.experiments.views) run after this one and may use its views.
-- query.py makes the macro current_project() first: the project whose rows the
-- views hold, or NULL for every project's (q --all).

-- A time of lab.db (milliseconds since 1970) as the views write it: local
-- time, to the minute.
CREATE MACRO local_time(ms) AS strftime(to_timestamp(ms / 1000), '%Y-%m-%d %H:%M');

-- One row per experiment of current_project(): its project (the root of its
-- tree; a project is its own), parent, depth (0 for a project), path (the
-- names from the project down, joined by ' › '), whether it is a dead end (an
-- idea that failed), description, and when it was made (local time).
CREATE VIEW experiments AS
WITH RECURSIVE tree AS (
    SELECT name, name AS project, 0 AS depth, name AS path FROM lab.experiments
    WHERE parent IS NULL AND name = coalesce(current_project(), name)
    UNION ALL
    SELECT e.name, t.project, t.depth + 1, t.path || ' › ' || e.name
    FROM lab.experiments e JOIN tree t ON e.parent = t.name)
SELECT e.name, t.project, e.parent, t.depth, t.path, e.dead_end <> 0 AS dead_end, e.description,
    local_time(e.created) AS created
FROM lab.experiments e JOIN tree t USING (name);

-- One row per run: tags, params and the latest value of each metric as maps
-- (metrics['machine/cpu_busy_mean_pct']), in key order. A metric's latest
-- value is the one of the highest step, then of the latest time. start_time and
-- end_time are lab.db's milliseconds; started and ended the same in local time,
-- and minutes the time from start to end, NULL until the run ends.
CREATE VIEW runs AS
WITH t AS (SELECT run, map_from_entries(list(struct_pack(k := key, v := value) ORDER BY key)) AS tags
           FROM lab.tags GROUP BY run),
     p AS (SELECT run, map_from_entries(list(struct_pack(k := key, v := value) ORDER BY key)) AS params
           FROM lab.params GROUP BY run),
     latest AS (SELECT run, key, arg_max_null(value, (step, time, value)) AS value FROM lab.metrics GROUP BY run, key),
     m AS (SELECT run, map_from_entries(list(struct_pack(k := key, v := value) ORDER BY key)) AS metrics
           FROM latest GROUP BY run)
SELECT r.id AS run, r.experiment, x.project, r.name, r.status, r.start_time, t.tags, p.params, m.metrics, r.end_time,
    local_time(r.start_time) AS started, local_time(r.end_time) AS ended, (r.end_time - r.start_time) / 60000.0 AS minutes,
    r.note
FROM lab.runs r JOIN experiments x ON x.name = r.experiment
LEFT JOIN t ON t.run = r.id LEFT JOIN p ON p.run = r.id LEFT JOIN m ON m.run = r.id;

-- The latest FINISHED run of the experiment experiment_name, by start_time, for
-- each combination of the values of the params named in the list keys, as rows
-- of runs: SELECT name, params, metrics FROM latest_runs('rate test', ['rate']).
-- A param that a run lacks counts as NULL.
CREATE MACRO latest_runs(experiment_name, keys) AS TABLE
    SELECT * FROM runs WHERE experiment = experiment_name AND status = 'FINISHED'
    QUALIFY row_number() OVER (PARTITION BY [params[k] FOR k IN keys] ORDER BY start_time DESC, run DESC) = 1;

-- One row per item of each message of a Claude Code transcript, the JSONL file
-- or files of path (a glob, as '~/.claude/projects/*/*/subagents/*.jsonl'):
-- file, line, time, role (user or assistant), kind (text, thinking, tool_use,
-- tool_result, image), tool (the call's tool), call (the tool call's id),
-- input (the call's input, JSON), text (of a text, thinking or tool_result),
-- error (whether the call failed), message (an API call's id), model, and the
-- API call's tokens on its first row only, so that sum() counts each call once:
-- context_tokens (input, cache read and cache creation) and output_tokens.
-- tool and error are on both rows of a tool call, its tool_use and its
-- tool_result. output_tokens is NULL where the call has no stop_reason, as in
-- claude -p's stream-json output, which counts them before the call ends; a
-- session's own transcript has them all.
CREATE MACRO transcript(path) AS TABLE
WITH texts AS (
    SELECT filename AS file, string_split(content, chr(10)) AS lines FROM read_text(path)
), entries AS (
    SELECT file, generate_subscripts(lines, 1) AS line,
        CASE WHEN json_valid(unnest(lines)) THEN json(unnest(lines)) END AS e
    FROM texts
), items AS (
    SELECT file, line, e,
        unnest(CASE WHEN json_type(e, '$.message.content') = 'ARRAY' THEN json_extract(e, '$.message.content[*]')
            ELSE [json_object('type', 'text', 'text', e ->> '$.message.content')] END) AS c,
        generate_subscripts(CASE WHEN json_type(e, '$.message.content') = 'ARRAY'
            THEN json_extract(e, '$.message.content[*]') ELSE [NULL::json] END, 1) AS item
    FROM entries WHERE (e ->> '$.type') IN ('user', 'assistant') AND json_type(e, '$.message') = 'OBJECT'
), parts AS (
    SELECT file, line, item, e ->> '$.timestamp' AS time, e ->> '$.type' AS role, c ->> 'type' AS kind,
        coalesce(c ->> 'id', c ->> 'tool_use_id') AS call, c ->> 'name' AS tool, c -> 'input' AS input,
        CASE WHEN (c ->> 'type') <> 'tool_result' THEN coalesce(c ->> 'text', c ->> 'thinking')
            WHEN json_type(c, '$.content') = 'ARRAY'
            THEN array_to_string(json_extract_string(c, '$.content[*].text'), chr(10))
            ELSE c ->> 'content' END AS text,
        (c ->> 'is_error')::boolean AS error, e ->> '$.message.id' AS message, e ->> '$.message.model' AS model,
        e -> '$.message.usage' AS usage, e ->> '$.message.stop_reason' AS stop_reason,
        role = 'assistant' AND row_number() OVER (PARTITION BY file, message ORDER BY line, item) = 1 AS first
    FROM items
)
SELECT file, line, time, role, kind, max(tool) OVER (PARTITION BY file, call) AS tool, call, input, text,
    bool_or(error) OVER (PARTITION BY file, call) AS error,
    message, model,
    CASE WHEN first THEN (usage ->> 'input_tokens')::bigint + coalesce((usage ->> 'cache_read_input_tokens')::bigint, 0)
        + coalesce((usage ->> 'cache_creation_input_tokens')::bigint, 0) END AS context_tokens,
    CASE WHEN first AND stop_reason IS NOT NULL THEN (usage ->> 'output_tokens')::bigint END AS output_tokens
FROM parts ORDER BY file, line, item;

-- One row per file logged for a run: run, experiment, project, name (which may
-- hold folders), bytes and path, the absolute path of the copy, files/<run>/<name>
-- in the data folder (the macro data_folder(), which query.attach makes).
CREATE VIEW files AS SELECT f.run, r.experiment, x.project, f.name, f.bytes,
    data_folder() || '/files/' || f.run || '/' || f.name AS path
FROM lab.files f JOIN lab.runs r ON r.id = f.run JOIN experiments x ON x.name = r.experiment;

-- Decisions, findings, protocol rules and questions. experiment is the label a
-- record is shown under; lab.records.experiment is the experiment it is filed
-- in, and project that experiment's project. time is local time, and time_ms
-- the same in lab.db's milliseconds, as runs.start_time.
CREATE VIEW records AS SELECT r.id, local_time(r.time) AS time, r.time AS time_ms, x.project,
    r.label AS experiment, r.kind, r.title, r.text, r.author, r.status, r.answers, r.evidence
    FROM lab.records r JOIN experiments x ON x.name = r.experiment;

-- The text of each experiment, run and record, for searching: an experiment's
-- description; a run's note, then its params and tags as key=value lines; a
-- record's text. kind is experiment, run or record, id the name or id that its
-- page (/lab/<kind>/<id>) takes, and experiment the one it is in.
CREATE VIEW experiments_runs_records AS
SELECT 'experiment' AS kind, name AS id, project, name AS experiment, name AS title, description AS text FROM experiments
UNION ALL
SELECT 'run', r.id, x.project, r.experiment, r.name, concat_ws(chr(10), r.note,
    (SELECT string_agg(p.key || '=' || p.value, chr(10) ORDER BY p.key) FROM lab.params p WHERE p.run = r.id),
    (SELECT string_agg(t.key || '=' || t.value, chr(10) ORDER BY t.key) FROM lab.tags t WHERE t.run = r.id))
FROM lab.runs r JOIN experiments x ON x.name = r.experiment
UNION ALL
SELECT 'record', r.id, x.project, r.experiment, r.title, r.text
FROM lab.records r JOIN experiments x ON x.name = r.experiment;

-- Whether text holds every word of words (split at white space), in any
-- letter case: the search boxes of the pages, as in
-- SELECT id, kind, status, title FROM records WHERE holds_words(title || ' ' || text, 'sample rate').
CREATE MACRO holds_words(text, words) AS
    list_bool_and([contains(lower(text), w) FOR w IN string_split_regex(lower(words), '\s+')]);
