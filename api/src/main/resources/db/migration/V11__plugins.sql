-- v0.10.0 plugins. project_plugins: a plugin on or off for a project ('*' = every project; a project's own row wins).
-- db_connections: the Database plugin's connections; the address with its password is a secret (db.<id>), here only
-- the address with the password hidden, and the last test's result.
CREATE TABLE project_plugins (
    project_id  TEXT NOT NULL,
    plugin      TEXT NOT NULL,
    enabled     INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, plugin)
);

CREATE TABLE db_connections (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    env         TEXT NOT NULL,
    shown       TEXT NOT NULL,
    source      TEXT,
    ok          INTEGER,
    server      TEXT,
    tables      INTEGER,
    error       TEXT,
    checked_at  TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);
