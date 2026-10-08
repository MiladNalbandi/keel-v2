-- v0.15.4 run history: "Delete" on the Flow page's Runs only hides a flow from the history (a mark, so it can be undone).
-- The flow's checkpoints (in the engine), its agent calls and its events stay: budgets and other flows may still need them.
ALTER TABLE threads ADD COLUMN hidden_at TEXT;
