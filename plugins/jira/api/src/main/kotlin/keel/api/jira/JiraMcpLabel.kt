package keel.api.jira

import keel.api.mcp.McpServerLabel
import org.springframework.stereotype.Component

/** Tools › MCP servers: the label of a `jira-<project>` server this plugin added from its catalog (uvx mcp-atlassian). */
@Component
class JiraMcpLabel : McpServerLabel {
    override fun label(name: String, command: String): String? =
        if (name.startsWith("jira-") && command == "uvx") "Jira (mcp-atlassian, optional)" else null
}
