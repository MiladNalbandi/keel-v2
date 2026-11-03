-- 0.21.3 a browser's session is bound to the address (host and port) it was opened on (auth/WebSessions). Browsers
-- send a localhost cookie to every port of localhost, so a cookie that comes in on another host or port is refused.
-- A session opened before 0.21.3 has no host yet: its next use binds it.
ALTER TABLE web_sessions ADD COLUMN host TEXT;
