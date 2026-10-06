-- v0.8.0: quality runs. keel runs flows on small eval projects (content/evals) with chosen models and scores them.
-- An eval case's project is a hidden project: it runs flows like any other, but no list, Inbox or notification shows it.
ALTER TABLE projects ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;

CREATE TABLE quality_runs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,              -- queued | running | done | stopped | failed
    trigger     TEXT NOT NULL,              -- manual | nightly
    flows_json  TEXT NOT NULL,
    models_json TEXT NOT NULL,
    sets_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    ended_at    TEXT,
    error       TEXT
);

CREATE TABLE quality_cases (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    n            INTEGER NOT NULL,
    eval_set     TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    title        TEXT NOT NULL,
    workflow_id  TEXT NOT NULL,
    model_json   TEXT NOT NULL,
    status       TEXT NOT NULL,             -- queued | running | done | stopped
    outcome      TEXT,                      -- end | stuck | failed | timeout | refused | stopped
    reached      TEXT,                      -- where it ended: the gate's title, the error
    project_id   TEXT,
    thread_id    TEXT,
    score        INTEGER,
    tokens       INTEGER,
    cost_usd     REAL,
    ms           INTEGER,
    sendbacks    INTEGER,
    estimate     INTEGER,
    progress     REAL,
    started_at   TEXT,
    ended_at     TEXT
);
CREATE INDEX quality_cases_run ON quality_cases(run_id, n);

CREATE TABLE quality_schedule (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    enabled       INTEGER NOT NULL DEFAULT 0,
    at            TEXT NOT NULL DEFAULT '02:00',   -- UTC
    flows_json    TEXT NOT NULL DEFAULT '["change"]',
    models_json   TEXT NOT NULL DEFAULT '[]',
    last_run_date TEXT
);
