-- The Goals plugin: a goal with its limits, and its steps. A step's state is read from its flow, not kept here.
CREATE TABLE IF NOT EXISTS goals_goal (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    why         TEXT,
    done_when   TEXT NOT NULL,
    cap_tokens  INTEGER,                   -- the most all its flows may use together, NULL for no limit of its own
    max_flows   INTEGER,                   -- the most flows it may start, NULL for no limit of its own
    status      TEXT NOT NULL DEFAULT 'new',   -- new | planning | active | done | stopped
    plan_thread TEXT,                      -- the planning flow
    plan_draft  TEXT,                      -- the steps the planner proposed, JSON, until a person approves them
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goals_goal_project ON goals_goal (project_id, created_at);
CREATE INDEX IF NOT EXISTS goals_goal_plan ON goals_goal (plan_thread);

CREATE TABLE IF NOT EXISTS goals_step (
    id          TEXT PRIMARY KEY,
    goal_id     TEXT NOT NULL,
    n           INTEGER NOT NULL,
    title       TEXT NOT NULL,
    request     TEXT NOT NULL,
    workflow    TEXT NOT NULL,
    skipped     INTEGER NOT NULL DEFAULT 0,
    thread_id   TEXT,                      -- its flow, once a person started it
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goals_step_goal ON goals_step (goal_id, n);
