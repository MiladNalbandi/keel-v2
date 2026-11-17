-- 0.34 a task handed to an Org (the Org plugin): the mission it went to, that mission's org and last status; and a
-- part task (one part of the work the Org's lead gave out) points at the task it is part of.
ALTER TABLE tasks ADD COLUMN mission_id TEXT;
ALTER TABLE tasks ADD COLUMN mission_org TEXT;
ALTER TABLE tasks ADD COLUMN mission_status TEXT;
ALTER TABLE tasks ADD COLUMN parent_task_id TEXT;
CREATE INDEX tasks_mission ON tasks(mission_id);
CREATE INDEX tasks_parent ON tasks(parent_task_id);
