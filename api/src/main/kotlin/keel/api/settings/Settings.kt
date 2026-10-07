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
    /** v0.4.1: how much keel decides by itself (manual | important | auto | readonly); a flow may pick another at start. */
    val runMode: String = "manual",
    val keelRules: Boolean = true,
    val fixAttempts: Int = 3,
    val coverageMin: Int = 80,
    val defaultModel: Model = Model(),
    val implementerModel: Model = Model(),
    val reviewerModel: Model = Model(),
    /** Used when a cap or a plan window is near and you choose "cheaper": never the fake model by default. */
    val cheaperModel: Model = Model("claude", "subscription", "haiku"),
    val capTokens: Int = 500_000,
    val onCap: String = "pause",
    /** Plan windows (5-hour, weekly): warn before a subscription agent at this much used, pause at usagePause. */
    val usageWarn: Double = 0.8,
    val usagePause: Double = 0.95,
    val branchPattern: String = "feat/{slug}",
    val webLaneWorktree: Boolean = true,
    val pushPr: String = "ask",
    /** Who keel's commits are by, as "Name <email>"; empty: the project's git name, else KeelBot (engine commit_ident). */
    val commitAuthor: String = "",
    /** keel's commits end with "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>". */
    val commitCoauthor: Boolean = true,
    /** v0.11.0 CI/CD plugin: when a pipeline fails, notify (default), fix (start the ci-fix flow) or quiet. */
    val ciOnFailure: String = "notify",
    val notify: String = "all",
    val envNames: List<String> = emptyList(),
    val mcp: List<String> = listOf("keel"),
) {
    companion object {
        val RUN_MODES = setOf("manual", "important", "auto", "readonly")
        val KEYS = listOf(
            "gates_mode", "run_mode", "keel_rules", "fix_attempts", "coverage_min", "default_model", "implementer_model",
            "reviewer_model", "cheaper_model", "cap_tokens", "on_cap", "usage_warn", "usage_pause", "branch_pattern", "web_lane_worktree",
            "push_pr", "commit_author", "commit_coauthor", "ci_on_failure", "notify", "env_names", "mcp",
        )
        /** "Name <email>", the form git and GitHub show. */
        val AUTHOR = Regex("""^\s*[^<>]+?\s*<\s*[^<>\s]+@[^<>\s]+\s*>\s*$""")
        val CHOICES = mapOf(
            "gates_mode" to setOf("every-ac", "end-of-lane", "end"),
            "run_mode" to RUN_MODES,
            "on_cap" to setOf("pause", "cheaper", "stop"),
            "notify" to setOf("all", "needs_you", "none"),
            "push_pr" to setOf("ask", "auto", "never"),
            "ci_on_failure" to setOf("notify", "fix", "quiet"),
        )
    }
}
