-- Cache reads (re-sent context) are counted apart: they cost about a tenth of new input.
ALTER TABLE agent_calls ADD COLUMN tokens_cached INTEGER NOT NULL DEFAULT 0;
