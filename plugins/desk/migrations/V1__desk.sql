-- The Desk plugin's requests: what a person pasted in, how it was sorted, and what was chosen.
CREATE TABLE IF NOT EXISTS desk_request (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    origin      TEXT,                      -- who or where it came from, the person's own words; never goes to a model
    status      TEXT NOT NULL DEFAULT 'new',   -- new | sorting | sorted | handed | closed
    kind        TEXT,                      -- what the sorter said: bug | question | feature | not-ours
    urgency     TEXT,                      -- high | normal | low
    area        TEXT,
    suggest     TEXT,                      -- fix | change | task | reply
    sorted_text TEXT,                      -- the sorter's whole answer
    note        TEXT,                      -- why a person closed it
    thread_id   TEXT,                      -- the sorting flow
    choice      TEXT,                      -- what the person chose at its gate
    task_id     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS desk_request_project ON desk_request (project_id, created_at);
CREATE INDEX IF NOT EXISTS desk_request_thread ON desk_request (thread_id);
