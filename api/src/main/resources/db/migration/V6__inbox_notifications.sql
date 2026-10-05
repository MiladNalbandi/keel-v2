-- v0.4.1: a gate's notification knows its thread and step, so deciding that gate (Flow page, Inbox, MCP) marks it done.
ALTER TABLE notifications ADD COLUMN thread_id TEXT;
ALTER TABLE notifications ADD COLUMN step TEXT;
ALTER TABLE notifications ADD COLUMN done INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS notifications_thread ON notifications(thread_id);
