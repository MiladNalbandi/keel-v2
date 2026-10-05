-- How a job ran: subscription (the plan pays), api (a key is billed) or fake. Only api rows count as API spend.
ALTER TABLE agent_calls ADD COLUMN mode TEXT;
UPDATE agent_calls SET mode = 'fake' WHERE provider IS NULL OR provider = 'fake';

-- What each provider says is used and what remains, per window (5-hour, weekly, monthly). One row per window:
-- the newest reading wins. source says where it came from ("last run", "codex app-server", "GitHub (unofficial)").
CREATE TABLE provider_usage (
    provider    TEXT NOT NULL,
    "window"    TEXT NOT NULL,
    used_pct    REAL,
    used        REAL,
    cap         REAL,
    remaining   REAL,
    resets_at   TEXT,
    status      TEXT,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    raw_json    TEXT,
    PRIMARY KEY (provider, "window")
);
