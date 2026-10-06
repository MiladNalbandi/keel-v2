-- v0.5.0: tasks (local or from Jira), their history, the task items of the Inbox, and a Jira connection per project.

CREATE TABLE tasks (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    type             TEXT NOT NULL DEFAULT 'task',      -- bug | story | task
    status           TEXT NOT NULL DEFAULT 'todo',      -- todo | in_progress | in_review | testing_pp | ready_prod | done | cancelled | blocked
    source           TEXT NOT NULL DEFAULT 'local',     -- local | jira
    external_key     TEXT,                              -- ABC-123
    external_url     TEXT,
    external_status  TEXT,                              -- the Jira status keel saw last (or moved the ticket to)
    assignee         TEXT,
    priority         TEXT,
    thread_id        TEXT,                              -- the flow working on it now
    workflow_id      TEXT,
    pr_url           TEXT,
    reviewers        TEXT NOT NULL DEFAULT '[]',        -- [{login, on: github|jira, state}]
    blocked_reason   TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX tasks_project ON tasks(project_id, updated_at);
CREATE INDEX tasks_thread ON tasks(thread_id);
CREATE UNIQUE INDEX tasks_external ON tasks(project_id, external_key) WHERE external_key IS NOT NULL;

CREATE TABLE task_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    at           TEXT NOT NULL,
    kind         TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    note         TEXT,
    actor        TEXT NOT NULL DEFAULT 'keel'           -- user | keel | jira
);
CREATE INDEX task_events_task ON task_events(task_id, id);

-- What a task needs from a person (Inbox kinds "task" and "jira-manual"); done_at is set when it is answered or obsolete.
CREATE TABLE task_inbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,                          -- task | jira-manual
    stage       TEXT,                                   -- task: pp | prod; jira-manual: the Jira status (or "reviewers")
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    done_at     TEXT
);
CREATE INDEX task_inbox_open ON task_inbox(project_id, done_at);

-- The token is a secret (SecretService, AES-GCM), never in this table.
CREATE TABLE jira_connections (
    project_id       TEXT PRIMARY KEY,
    json             TEXT NOT NULL,
    me_json          TEXT,
    last_sync_at     TEXT,
    last_sync_error  TEXT,
    updated_at       TEXT NOT NULL
);
