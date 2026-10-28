package keel.api.review

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: the Code Review part's acting MCP tools (keel_review_comment,
 * _reply, _resolve, _explain, _findings, _submit). Every other non-GET route needs admin.
 */
@Component
class ReviewMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/projects/[^/]+/review/(drafts|submit|ai/[^/]+)$"),
        post("^/api/projects/[^/]+/review/threads/[^/]+/(reply|resolve)$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
