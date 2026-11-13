package keel.api.goals

import keel.api.mcp.McpRouteLevels
import org.springframework.stereotype.Component

/**
 * #98 the routes an MCP write token may call here: what the Goals plugin's tools send (keel_goal_create, keel_goal_plan,
 * keel_goal_start_step, keel_goal_skip_step, keel_goal_status). A goal's limits have no tool, so they stay admin.
 */
@Component
class GoalsMcpRoutes : McpRouteLevels {
    override val write = listOf(
        post("^/api/projects/[^/]+/goals$"),
        post("^/api/projects/[^/]+/goals/[^/]+/plan$"),
        post("^/api/projects/[^/]+/goals/[^/]+/steps/[^/]+/(start|skip)$"),
        post("^/api/projects/[^/]+/goals/[^/]+/status/(done|stopped)$"),
    )

    private fun post(re: String) = "POST" to Regex(re)
}
