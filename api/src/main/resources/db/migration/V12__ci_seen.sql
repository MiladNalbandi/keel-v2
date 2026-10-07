-- v0.11.0 the CI/CD plugin's watcher: the failed runs it already told the person about (once per run).
CREATE TABLE ci_seen (
    project_id  TEXT NOT NULL,
    run_id      INTEGER NOT NULL,
    conclusion  TEXT,
    fixed_by    TEXT,
    at          TEXT NOT NULL,
    PRIMARY KEY (project_id, run_id)
);
