-- v0.16.0 approvals: keel's one place for "ask a person and wait" (docs/plugins/09-step2-contract.md §1). V15: keel
-- 0.15.4 shipped V14__thread_hidden.sql first (MigrationUpgradeTest: a 0.15.4 database migrates to this). A row per
-- question: KeelBot's commands and keel2 mcp's acting tools (the engine's, from approval.* events), and questions the api
-- asks itself. status: waiting | approved | denied | expired | closed.
CREATE TABLE approvals (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    payload_json  TEXT,
    status        TEXT NOT NULL DEFAULT 'waiting',
    decision      TEXT,
    why           TEXT,
    requested_by  TEXT,
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    decided_by    TEXT
);
CREATE INDEX approvals_waiting ON approvals (status, project_id);
