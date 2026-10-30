package keel.api.flow

import keel.api.settings.Model
import keel.api.settings.Settings
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

/** 0.19.0 harness mode: which workflow a start runs (Settings › Who does the work). */
class HarnessRouteTest {
    private val claude = Model("claude", "subscription", "sonnet")
    private val harness = Settings(defaultModel = claude)

    @Test
    fun `a build flow goes to the harness with what was asked for`() {
        val (wf, data) = Harness.route(harness, "feature", mapOf("seed" to 1), null)
        assertThat(wf).isEqualTo("harness")
        assertThat(data).containsEntry("intent", "feature").containsEntry("requested_workflow", "feature")
            .containsEntry("plan_first", "yes").containsEntry("seed", 1)
        assertThat(Harness.route(harness, "fix", null, null).second).containsEntry("intent", "bug")
        assertThat(Harness.route(harness.copy(harnessPlan = false), "change", null, null).second).containsEntry("plan_first", "no")
    }

    @Test
    fun `keel's own flow when the setting or the start says so`() {
        assertThat(Harness.route(harness.copy(buildRuns = "flow"), "change", null, null).first).isEqualTo("change")
        val (wf, data) = Harness.route(harness, "change", mapOf("engine" to "flow"), null)
        assertThat(wf).isEqualTo("change")
        assertThat(data).doesNotContainKey("engine")
        assertThat(Harness.route(harness.copy(buildRuns = "flow"), "change", mapOf("engine" to "harness"), null).first)
            .isEqualTo("harness")
    }

    @Test
    fun `other flows and models without a harness keep their flow`() {
        assertThat(Harness.route(harness, "review-pr", null, null).first).isEqualTo("review-pr")
        assertThat(Harness.route(harness, "init", null, null).first).isEqualTo("init")
        assertThat(Harness.route(Settings(), "change", null, null).first).isEqualTo("change")       // the fake model
        assertThat(Harness.route(harness, "change", null, Model("claude", "api", "sonnet")).first).isEqualTo("change")
        assertThat(Harness.route(harness, "change", null, Model("copilot", "opencode", "gpt-5")).first).isEqualTo("harness")
    }

    @Test
    fun `harness names`() {
        assertThat(Harness.name(claude)).isEqualTo("Claude Code")
        assertThat(Harness.name(Model("copilot", "opencode", "gpt-5"))).isEqualTo("OpenCode")
        assertThat(Harness.name(Model("copilot", "subscription", "auto"))).isEqualTo("Copilot CLI")
        assertThat(Harness.name(Model("codex", "subscription", "gpt-5.5"))).isEqualTo("Codex")
    }

    @Test
    fun `the harness flow's session and MCP gateway reach the engine`() {
        // the api reads a workflow into its Step model and sends that to the engine: a field it does not know is lost,
        // and then plan and build each ran in a session of their own
        val yaml = java.nio.file.Files.readString(java.nio.file.Paths.get("../content/workflows/harness.yaml"))
        val sent = keel.api.common.Json.write(keel.api.workflows.WorkflowDoc.parse(yaml))
        val steps = keel.api.common.Json.readMap(sent)["steps"] as List<*>
        val byId = steps.associateBy { (it as Map<*, *>)["id"] }
        for (id in listOf("plan", "build")) {
            val st = byId[id] as Map<*, *>
            assertThat(st["session"]).isEqualTo("thread")
            assertThat(st["mcp"]).isEqualTo("gateway")
        }
    }
}
