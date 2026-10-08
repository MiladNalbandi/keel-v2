-- v0.14.0 the Code Review plugin: your pending line comments (sent together with Submit), the files you marked as
-- viewed (per head commit), keel's AI runs on a review (overview, findings) and your decision on each finding.
CREATE TABLE review_drafts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    review_key  TEXT NOT NULL,
    path        TEXT,
    line        INTEGER,
    side        TEXT NOT NULL DEFAULT 'RIGHT',
    body        TEXT NOT NULL,
    finding_id  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX review_drafts_key ON review_drafts (project_id, review_key);

CREATE TABLE review_viewed (
    project_id  TEXT NOT NULL,
    review_key  TEXT NOT NULL,
    path        TEXT NOT NULL,
    head_sha    TEXT NOT NULL,
    at          TEXT NOT NULL,
    PRIMARY KEY (project_id, review_key, path)
);

CREATE TABLE review_runs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    review_key  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    head_sha    TEXT NOT NULL,
    status      TEXT NOT NULL,
    sessions    TEXT NOT NULL DEFAULT '[]',
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX review_runs_key ON review_runs (project_id, review_key);

CREATE TABLE review_decisions (
    project_id  TEXT NOT NULL,
    review_key  TEXT NOT NULL,
    finding_id  TEXT NOT NULL,
    decision    TEXT NOT NULL,
    why         TEXT,
    at          TEXT NOT NULL,
    PRIMARY KEY (project_id, review_key, finding_id)
);
