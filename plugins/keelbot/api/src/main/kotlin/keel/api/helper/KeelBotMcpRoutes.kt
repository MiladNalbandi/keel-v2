package keel.api.helper

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: what keel2 mcp's KeelBot tools send (engine mcp_tui.py,
 * mcp_models.py): a new chat, a turn, keep or undo its changes, its model. Every other non-GET route needs admin.
 */
@Component
class KeelBotMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/projects/[^/]+/helper/sessions$"),
        post("^/api/projects/[^/]+/helper/sessions/[^/]+/(turn|done|undo)$"),
        "PATCH" to Regex("^/api/projects/[^/]+/helper/sessions/[^/]+$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
