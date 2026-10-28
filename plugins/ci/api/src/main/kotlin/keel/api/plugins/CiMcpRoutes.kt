package keel.api.plugins

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: the CI/CD part's acting MCP tool (keel_ci_rerun). Every other
 * non-GET route needs admin.
 */
@Component
class CiMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/projects/[^/]+/ci/runs/[^/]+/rerun$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
