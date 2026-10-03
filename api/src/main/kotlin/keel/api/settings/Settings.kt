package keel.api.settings

import com.fasterxml.jackson.annotation.JsonInclude

@JsonInclude(JsonInclude.Include.NON_NULL)
data class Model(
    val provider: String = "fake",
    val mode: String = "api",
    val model: String = "fake",
    val effort: String? = null,
)

/** Settings, general or effective. A project stores only the keys it overrides. */
data class Settings(
    val gatesMode: String = "every-ac",
    val keelRules: Boolean = true,
    val fixAttempts: Int = 3,
    val coverageMin: Int = 80,
    val defaultModel: Model = Model(),
    val implementerModel: Model = Model(),
    val reviewerModel: Model = Model(),
    val cheaperModel: Model = Model(),
    val capTokens: Int = 500_000,
    val onCap: String = "pause",
    val branchPattern: String = "feat/{slug}",
    val webLaneWorktree: Boolean = true,
    val pushPr: String = "ask",
    val notify: String = "all",
    val envNames: List<String> = emptyList(),
    val mcp: List<String> = listOf("keel"),
) {
    companion object {
        val KEYS = listOf(
            "gates_mode", "keel_rules", "fix_attempts", "coverage_min", "default_model", "implementer_model",
            "reviewer_model", "cheaper_model", "cap_tokens", "on_cap", "branch_pattern", "web_lane_worktree",
            "push_pr", "notify", "env_names", "mcp",
        )
        val CHOICES = mapOf(
            "gates_mode" to setOf("every-ac", "end-of-lane", "end"),
            "on_cap" to setOf("pause", "cheaper", "stop"),
            "notify" to setOf("all", "needs_you", "none"),
            "push_pr" to setOf("ask", "auto", "never"),
        )
    }
}
