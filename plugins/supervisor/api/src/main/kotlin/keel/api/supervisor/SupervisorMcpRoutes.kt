package keel.api.supervisor

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: keel_supervisor_set: the switch keel-wide, for a project and
 * for one flow. Every other non-GET route needs admin.
 */
@Component
class SupervisorMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/supervisor/switch$"),
        post("^/api/projects/[^/]+/supervisor/switch$"),
        post("^/api/projects/[^/]+/flows/[^/]+/supervisor$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
