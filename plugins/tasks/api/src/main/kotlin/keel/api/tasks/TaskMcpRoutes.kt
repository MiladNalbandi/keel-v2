package keel.api.tasks

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: keel_answer on a task's Inbox item. Every other non-GET route
 * needs admin.
 */
@Component
class TaskMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/inbox/tasks/[^/]+/act$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
