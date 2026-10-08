-- keel Product's own tables (its own migration history: product_schema_history). keel's tables are never changed here.

CREATE TABLE product_initiatives (
    id           TEXT PRIMARY KEY,           -- INI-12
    n            INTEGER NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    idea         TEXT NOT NULL,
    why_now      TEXT,
    outcome_hope TEXT,
    owner        TEXT,
    stage        TEXT NOT NULL,              -- idea, brief, impact, decision, plan, delivery, outcome, done
    status       TEXT NOT NULL,              -- new, running, waiting, ready, parked, failed, done
    option       TEXT,                       -- the option chosen at the decision (A, B, C)
    repos_json   TEXT NOT NULL DEFAULT '[]', -- the keel projects keel may read for this initiative
    metric       TEXT,                       -- the outcome metric the product owner entered after the release
    revisit_at   TEXT,                       -- "not now": when it comes back
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE product_docs (
    initiative_id TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- brief, impact, decision, plan, deck, outcome
    version       INTEGER NOT NULL,
    path          TEXT NOT NULL,             -- in the product repo
    sha           TEXT,
    text          TEXT NOT NULL DEFAULT '',
    data_json     TEXT,                      -- impact: per repo; plan: the checked plan
    thread_id     TEXT,
    created_at    TEXT NOT NULL,
    approved_at   TEXT,
    PRIMARY KEY (initiative_id, kind, version)
);

CREATE TABLE product_runs (
    thread_id     TEXT PRIMARY KEY,
    initiative_id TEXT NOT NULL,
    stage         TEXT NOT NULL,
    workflow_id   TEXT NOT NULL,
    status        TEXT NOT NULL,             -- running, waiting, done, failed, stopped
    reason        TEXT,                      -- why it ran: first run, send-back, objection, re-run
    started_at    TEXT NOT NULL,
    ended_at      TEXT
);

CREATE TABLE product_questions (
    id            TEXT PRIMARY KEY,
    initiative_id TEXT NOT NULL,
    stage         TEXT NOT NULL,
    role          TEXT NOT NULL,             -- keel, po, lead, dev, legal, or a team id
    asked_to      TEXT,                      -- a person's name when someone named was asked
    about         TEXT,                      -- the text the question is about
    text          TEXT NOT NULL,
    answer        TEXT,
    answered_by   TEXT,
    status        TEXT NOT NULL,             -- open, answered
    created_at    TEXT NOT NULL,
    answered_at   TEXT
);

CREATE TABLE product_disagreements (
    id            TEXT PRIMARY KEY,
    initiative_id TEXT NOT NULL,
    stage         TEXT NOT NULL,
    author        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    proposal      TEXT,
    decider       TEXT NOT NULL,             -- po, architect, lead, team
    status        TEXT NOT NULL,             -- open, rerun, settled
    outcome       TEXT,
    decided_by    TEXT,
    created_at    TEXT NOT NULL,
    decided_at    TEXT
);

CREATE TABLE product_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    initiative_id TEXT NOT NULL,
    at            TEXT NOT NULL,
    actor         TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- created, question, answer, version, run, approved, sent_back, disagreed,
                                             -- settled, decision, jira, tasks, follow_up, released, outcome
    text          TEXT NOT NULL,
    data_json     TEXT
);
CREATE INDEX product_events_by_initiative ON product_events (initiative_id, id);

CREATE TABLE product_follow_ups (
    id               TEXT PRIMARY KEY,
    initiative_id    TEXT NOT NULL,
    kind             TEXT NOT NULL,          -- gate, question, revisit, outcome, manual
    text             TEXT NOT NULL,
    owner            TEXT,
    due_at           TEXT NOT NULL,
    repeat_days      INTEGER,
    ref              TEXT,                   -- what it follows: a thread id, a question id
    done_at          TEXT,
    last_reminded_at TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE product_plan_items (
    initiative_id TEXT NOT NULL,
    version       INTEGER NOT NULL,
    id            TEXT NOT NULL,             -- PAY-S1
    epic          TEXT NOT NULL,
    team          TEXT,
    repo          TEXT,
    title         TEXT NOT NULL,
    body_json     TEXT NOT NULL,
    jira_key      TEXT,
    project_id    TEXT,
    task_id       TEXT,
    PRIMARY KEY (initiative_id, version, id)
);

CREATE TABLE product_teams (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    lead          TEXT,
    jira_project  TEXT,
    capacity_days REAL,
    members_json  TEXT NOT NULL DEFAULT '[]',
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE product_team_paths (
    team_id    TEXT NOT NULL,
    project_id TEXT NOT NULL,
    glob       TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (team_id, project_id, glob)
);

CREATE TABLE product_team_suggestions (
    id         TEXT PRIMARY KEY,
    team_id    TEXT NOT NULL,
    page       TEXT NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT,
    status     TEXT NOT NULL,                -- open, accepted, rejected
    created_at TEXT NOT NULL,
    decided_at TEXT
);
