-- The tables of a lab.db of version 0.0.0, which has no version set.
-- checks/check_upgrades.py makes a lab.db from them, with and without the
-- table files, as lab.db files of 0.0.0 exist in both forms, and upgrades it.
-- The tables of 0.0.0 never change, so neither does this file.

CREATE TABLE IF NOT EXISTS experiments (
    name TEXT PRIMARY KEY,
    parent TEXT REFERENCES experiments (name) ON UPDATE CASCADE ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    dead_end INTEGER NOT NULL DEFAULT 0 CHECK (dead_end IN (0, 1)),
    created INTEGER NOT NULL,
    folder TEXT UNIQUE,
    files TEXT,
    views TEXT NOT NULL DEFAULT '',
    CHECK ((parent IS NULL) = (folder IS NOT NULL) AND (folder IS NULL) = (files IS NULL))
);
CREATE INDEX IF NOT EXISTS experiments_by_parent ON experiments (parent);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL REFERENCES experiments (name) ON UPDATE CASCADE ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'FINISHED', 'FAILED', 'KILLED')),
    start_time INTEGER NOT NULL,
    end_time INTEGER,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_experiment ON runs (experiment);

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

CREATE TABLE IF NOT EXISTS metrics (
    run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value REAL,
    step INTEGER NOT NULL,
    time INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS metrics_by_run ON metrics (run, key);

CREATE TABLE IF NOT EXISTS files (
    run TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (run, name)
);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL REFERENCES experiments (name) ON UPDATE CASCADE ON DELETE CASCADE,
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
