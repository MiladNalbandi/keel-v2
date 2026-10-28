package keel.api.plugins

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: the Git part's acting MCP tools (keel_git_commit, keel_git_push,
 * keel_pr_create; each asks first). Every other non-GET route needs admin.
 */
@Component
class GitMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/projects/[^/]+/git/(commit|push|pr)$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
