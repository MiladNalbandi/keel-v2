package keel.api.flow

import keel.api.settings.Model
import keel.api.settings.Settings

/**
 * keel 0.19.0 harness mode: keel as the control room over the project's coding harness (Claude Code, Codex, Copilot
 * CLI, OpenCode). With Settings › Who does the work on `harness` (build_runs), a start of one of keel's build flows
 * runs the `harness` workflow instead: the harness plans in one session of its own, the person approves the plan, the
 * same session builds it, keel runs the tests and commits (content/workflows/harness.yaml). keel's own flows stay:
 * build_runs `flow`, or `engine: flow` in a start's options, runs the flow that was asked for.
 */
object Harness {
    const val WORKFLOW = "harness"

    /** keel's flows that build or fix code, and what each asks the harness's planner for (harness_actions.INTENTS). */
    val BUILD_FLOWS = mapOf(
        "change" to "change", "quick" to "change", "small" to "change", "implement" to "change",
        "feature" to "feature", "fix" to "bug",
    )

    /** The CLIs keel can hand a whole task to (an api model, a local or the fake model has no harness of its own). */
    val HARNESSES = setOf("claude", "codex", "copilot")

    fun isHarness(m: Model): Boolean = m.mode != "api" && m.provider in HARNESSES

    /** The harness's name as people know it. */
    fun name(m: Model): String = when {
        m.mode == "opencode" -> "OpenCode"
        m.provider == "claude" -> "Claude Code"
        m.provider == "codex" -> "Codex"
        m.provider == "copilot" -> "Copilot CLI"
        else -> m.provider
    }

    /** The workflow and options a start runs: [workflowId] as asked, or the harness workflow with the intent. */
    fun route(s: Settings, workflowId: String, options: Map<String, Any?>?, model: Model?): Pair<String, Map<String, Any?>?> {
        val intent = BUILD_FLOWS[workflowId] ?: return workflowId to options
        val engine = options?.get("engine")?.toString()
        val rest = options?.minus("engine")
        if (engine == "flow" || (engine != WORKFLOW && s.buildRuns != WORKFLOW)) return workflowId to rest
        if (!isHarness(model ?: s.defaultModel)) return workflowId to rest
        // a caller may say what it wants of the planner (a review fix: intent review-fix, no plan step)
        val data = mapOf("intent" to intent, "requested_workflow" to workflowId,
            "plan_first" to if (s.harnessPlan) "yes" else "no") + rest.orEmpty()
        return WORKFLOW to data
    }
}
