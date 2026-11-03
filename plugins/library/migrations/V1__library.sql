-- The Library plugin's documents: text a person put here. project_id NULL is for every project.
CREATE TABLE IF NOT EXISTS library_doc (
    id          TEXT PRIMARY KEY,
    project_id  TEXT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    source      TEXT,                      -- where it comes from, the person's own words
    for_agents  INTEGER NOT NULL DEFAULT 1, -- 0: people only, no agent tool ever returns it
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS library_doc_project ON library_doc (project_id);
