-- keel v2 api schema (SQLite)

CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    root        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE threads (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    workflow_id  TEXT,
    title        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'running',
    current      TEXT,
    phase        TEXT,
    ac           TEXT,
    estimate     INTEGER,
    state_json   TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX threads_project ON threads(project_id, updated_at);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT,
    project_id  TEXT,
    type        TEXT NOT NULL,
    step        TEXT,
    call_id     TEXT,
    at          TEXT NOT NULL,
    data_json   TEXT
);
CREATE INDEX events_thread ON events(thread_id);

CREATE TABLE agent_calls (
    id                TEXT PRIMARY KEY,
    project_id        TEXT,
    thread_id         TEXT,
    agent             TEXT,
    provider          TEXT,
    model             TEXT,
    step              TEXT,
    phase             TEXT,
    ac                TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    tokens_in         INTEGER NOT NULL DEFAULT 0,
    tokens_out        INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    premium_requests  INTEGER NOT NULL DEFAULT 0,
    steps_count       INTEGER NOT NULL DEFAULT 0,
    mcp_calls         INTEGER NOT NULL DEFAULT 0,
    result            TEXT
);
CREATE INDEX agent_calls_project ON agent_calls(project_id, started_at);

CREATE TABLE agent_steps (
    call_id  TEXT NOT NULL,
    n        INTEGER NOT NULL,
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    text     TEXT,
    tool     TEXT,
    server   TEXT,
    path     TEXT,
    diff     TEXT,
    ms       INTEGER,
    ok       INTEGER,
    PRIMARY KEY (call_id, n)
);

CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    project_id  TEXT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    link        TEXT,
    at          TEXT NOT NULL,
    read        INTEGER NOT NULL DEFAULT 0
);

-- scope = 'general' or a project id; json = full settings (general) or overrides (project)
CREATE TABLE settings (
    scope  TEXT PRIMARY KEY,
    json   TEXT NOT NULL
);

-- small documents: notification settings, limits, connection modes, mcp allowlists
CREATE TABLE kv (
    key   TEXT PRIMARY KEY,
    json  TEXT NOT NULL
);

CREATE TABLE secrets (
    name        TEXT PRIMARY KEY,
    iv          BLOB NOT NULL,
    ciphertext  BLOB NOT NULL,
    hint        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE facts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    source      TEXT NOT NULL,
    at          TEXT NOT NULL
);

-- project_id NULL = installed for every project
CREATE TABLE workflows (
    id          TEXT PRIMARY KEY,
    project_id  TEXT,
    name        TEXT NOT NULL,
    based_on    TEXT,
    keel_rules  INTEGER NOT NULL DEFAULT 1,
    version     INTEGER NOT NULL DEFAULT 1,
    yaml        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'yours',
    library_id  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE workflow_versions (
    workflow_id  TEXT NOT NULL,
    version      INTEGER NOT NULL,
    yaml         TEXT NOT NULL,
    at           TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version)
);

CREATE TABLE custom_agents (
    id          TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    json        TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE agent_overrides (
    project_id  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    json        TEXT NOT NULL,
    PRIMARY KEY (project_id, agent_id)
);

CREATE TABLE custom_skills (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    stack       TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE skill_assign (
    project_id  TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    json        TEXT NOT NULL,
    PRIMARY KEY (project_id, skill_id)
);

CREATE TABLE mcp_servers (
    name        TEXT PRIMARY KEY,
    command     TEXT NOT NULL,
    args_json   TEXT NOT NULL DEFAULT '[]',
    env_json    TEXT,
    cwd         TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    builtin     INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'off',
    tools_json  TEXT NOT NULL DEFAULT '[]'
);
