package keel.api.mcp

/**
 * A label for MCP servers a plugin adds to Tools › MCP servers (the Jira plugin's `jira-<project>` entry from its
 * catalog). [McpService] asks every bean for a server that is not keel's own; the first label that is not null wins.
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may implement it.
 */
fun interface McpServerLabel {
    /** What the server is, for the Tools page; null = not one of mine. */
    fun label(name: String, command: String): String?
}
