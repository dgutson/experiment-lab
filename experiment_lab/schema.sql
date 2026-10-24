-- The tables of lab.db. experiment_lab/lab.py makes them when it opens the
-- file; q and the pages read them through the views of lab.sql. Times are
-- milliseconds since 1970; descriptions, notes and record texts are Markdown.

CREATE TABLE IF NOT EXISTS experiments (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL REFERENCES experiments (name) ON UPDATE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'FINISHED', 'FAILED', 'KILLED')),
    start_time INTEGER NOT NULL,
    end_time INTEGER,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_experiment ON runs (experiment);

-- One value per run and key; logging a key again replaces its value.
CREATE TABLE IF NOT EXISTS params (
    run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (run, key)
);
CREATE TABLE IF NOT EXISTS tags (
    run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (run, key)
);

-- Every value logged, so a metric's progress is kept; value is NULL for NaN.
CREATE TABLE IF NOT EXISTS metrics (
    run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value REAL,
    step INTEGER NOT NULL,
    time INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS metrics_by_run ON metrics (run, key);

-- experiment is the experiment the record is filed in, label the name it is
-- shown under; answers is a record id or ''; evidence is items joined by "; ".
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL REFERENCES experiments (name) ON UPDATE CASCADE,
    label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('decision', 'finding', 'protocol', 'question')),
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'superseded')),
    answers TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    time INTEGER NOT NULL
);
