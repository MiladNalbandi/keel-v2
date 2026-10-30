package keel.api.settings

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.common.BadRequest

@JsonInclude(JsonInclude.Include.NON_NULL)
data class Model(
    val provider: String = "fake",
    val mode: String = "api",
    val model: String = "fake",
    /** How hard the model thinks (low | medium | high | xhigh | max, or the provider's own words); null: the engine's
     *  own default. The engine passes it on only when the provider and model take it (models/catalog.py effort_for). */
    val effort: String? = null,
) {
    /** The effort as keel keeps it: "default" or blank is none, the rest lower case; a word only (it ends up in a CLI flag). */
    fun checked(): Model {
        val e = effort?.trim()?.lowercase()
        if (e.isNullOrEmpty() || e == "default") return copy(effort = null)
        if (!EFFORT.matches(e)) throw BadRequest("effort cannot be \"$effort\"", "Pick one the model offers, for example low, medium, high, xhigh or max, or default.")
        return copy(effort = e)
    }

    companion object {
        private val EFFORT = Regex("^[a-z][a-z0-9-]{0,15}$")
    }
}

/**
 * 0.18.46 "Local models only" (setting local_only): code that may not leave the machine. Only local models (and the fake
 * model, which sends nothing anywhere) may run this project's agents.
 */
object LocalOnly {
    val ALLOWED = setOf("local", "fake")

    /** Refuses when [s] says local models only and one of [models] is a cloud model. [what]: who would run it. */
    fun check(s: Settings, models: Collection<Model>, what: String = "This project's agents") {
        if (!s.localOnly) return
        val cloud = models.filter { it.provider !in ALLOWED }.map { "${it.provider} ${it.model}".trim() }.distinct()
        if (cloud.isEmpty()) return
        throw BadRequest("This project uses local models only, but $what would use ${cloud.joinToString()}.",
            "Pick a local model (Connections › Local models) in Settings › Models or Agents, or turn off \"Local models only\" for this project.")
    }
}

/** Settings, general or effective. A project stores only the keys it overrides. */
data class Settings(
    val gatesMode: String = "every-ac",
    /** v0.4.1: how much keel decides by itself (manual | important | auto | readonly); a flow may pick another at start. */
    val runMode: String = "manual",
    val fixAttempts: Int = 3,
    val coverageMin: Int = 80,
    val defaultModel: Model = Model(),
    val implementerModel: Model = Model(),
    val reviewerModel: Model = Model(),
    /** Used when a cap or a plan window is near and you choose "cheaper": never the fake model by default. */
    val cheaperModel: Model = Model("claude", "subscription", "haiku"),
    /** 0.18.46 this project's agents run on local models only (provider local): keel refuses a cloud model for them. */
    val localOnly: Boolean = false,
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
    /** 0.18.57 the skills the repo keeps for coding agents (.claude/skills, .opencode/skills), found at init and at a
     *  knowledge refresh, are loaded for the agents they are for; false: listed on the Skills page, loaded by none. */
    val repoSkills: Boolean = true,
    /** v0.13.0 what this keel does (general only): auto = Product and Dev when the Product add-on is installed, else Dev. */
    val keelMode: String = "auto",
    /** 0.19.0 who builds new work (change, feature, bug): harness = the default model's coding harness (Claude Code,
     *  Codex, Copilot CLI, OpenCode) in one session of its own, keel around it (flow/Harness.kt); flow = keel's own flows. */
    val buildRuns: String = "harness",
    /** 0.19.0 harness mode: the harness writes a plan first and the person approves it before it builds (plan mode). */
    val harnessPlan: Boolean = true,
    /** 0.19.0 who fixes a review finding: harness = the review's harness session (KeelBot), flow = a keel flow. */
    val fixRuns: String = "harness",
) {
    companion object {
        val RUN_MODES = setOf("manual", "important", "auto", "readonly")
        val KEYS = listOf(
            "gates_mode", "run_mode", "fix_attempts", "coverage_min", "default_model", "implementer_model",
            "reviewer_model", "cheaper_model", "local_only", "cap_tokens", "on_cap", "usage_warn", "usage_pause", "branch_pattern", "web_lane_worktree",
            "push_pr", "commit_author", "commit_coauthor", "ci_on_failure", "notify", "env_names", "mcp", "repo_skills", "keel_mode",
            "build_runs", "harness_plan", "fix_runs",
        )
        /** Settings of the whole keel only: a project cannot override them. */
        val GENERAL_ONLY = setOf("keel_mode")
        /** "Name <email>", the form git and GitHub show. */
        val AUTHOR = Regex("""^\s*[^<>]+?\s*<\s*[^<>\s]+@[^<>\s]+\s*>\s*$""")
        val CHOICES = mapOf(
            "gates_mode" to setOf("every-ac", "end-of-lane", "end"),
            "run_mode" to RUN_MODES,
            "on_cap" to setOf("pause", "cheaper", "stop"),
            "notify" to setOf("all", "needs_you", "none"),
            "push_pr" to setOf("ask", "auto", "never"),
            "ci_on_failure" to setOf("notify", "fix", "quiet"),
            "keel_mode" to setOf("auto", "dev", "product", "both"),
            "build_runs" to setOf("harness", "flow"),
            "fix_runs" to setOf("harness", "flow"),
        )
    }
}
