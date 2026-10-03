-- v0.2: budget caps per project (GET/POST/PUT/DELETE /api/projects/{pid}/caps)

CREATE TABLE caps (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    scope       TEXT NOT NULL,
    cap_limit   REAL NOT NULL,
    unit        TEXT NOT NULL,
    action      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX caps_project ON caps(project_id, created_at);
