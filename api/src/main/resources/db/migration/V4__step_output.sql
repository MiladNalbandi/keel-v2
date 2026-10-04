-- Tool steps carry their output (engine v0.3): command output, MCP result.
ALTER TABLE agent_steps ADD COLUMN output TEXT;
