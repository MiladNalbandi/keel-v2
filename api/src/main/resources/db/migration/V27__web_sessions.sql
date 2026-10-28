-- 0.18.64 a browser's sign-in (auth/WebSessions). Until 0.18.63 the sign-in cookie was the web key itself, for 400 days,
-- and signing out never ended it. Now each browser gets its own random session id; only its SHA-256 is kept here. A
-- session ends 30 days after its last use, at sign-out (this browser) or at "sign out everywhere" (every row goes).
CREATE TABLE web_sessions (
    id_hash       TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    expires_ms    INTEGER NOT NULL          -- epoch milliseconds
);
