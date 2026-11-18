package keel.api.review

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * 0.18.64 the routes an MCP write token may call here: the Code Review part's acting MCP tools (keel_review_comment,
 * _draft, _move_drafts, _viewed, _base, _reply, _resolve, _explain, _findings, _check, _button, _finding, _address,
 * _fix_finding, _checkout, _merge, _submit). Those that post to the host, start a flow or move the project folder ask
 * the person first, in the MCP server. The thread settings and every other non-GET route need admin.
 */
@Component
class ReviewMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/projects/[^/]+/review/(drafts|drafts/move|viewed|submit|merge|checkout|threads/judge|ai/[^/]+)$"),
        post("^/api/projects/[^/]+/review/threads/[^/]+/(reply|resolve|judge|address|buttons/[^/]+)$"),
        post("^/api/projects/[^/]+/review/ai/findings/[^/]+(/fix)?$"),
        "PUT" to Regex("^/api/projects/[^/]+/review/(drafts/[^/]+|base)$"),
        "DELETE" to Regex("^/api/projects/[^/]+/review/drafts/[^/]+$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
