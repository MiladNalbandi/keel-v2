package keel.api.tasks

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: keel_answer on a task's Inbox item; 0.34 a new task
 * (keel_task_create) and Hand to Org (keel_task_hand_to_org). Every other non-GET route needs admin.
 */
@Component
class TaskMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/inbox/tasks/[^/]+/act$"),
        post("^/api/projects/[^/]+/tasks$"),
        post("^/api/tasks/[^/]+/org$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
