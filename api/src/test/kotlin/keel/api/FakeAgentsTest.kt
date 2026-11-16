package keel.api

import keel.api.flow.SESSION_AGENTS
import keel.api.flow.StartThread
import keel.api.flow.ThreadSettings
import keel.api.flow.fakeAgents
import keel.api.settings.Model
import keel.api.workflows.Step
import keel.api.workflows.Workflow
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

/** 0.29.1 a connected session plans and builds: a keel without any key (the first start) may run that flow. */
class FakeAgentsTest {
    private fun start(models: Map<String, Model>) = StartThread(
        projectId = "p", root = "/tmp/p", title = "t", acs = null, models = models,
        settings = ThreadSettings(gatesMode = "manual", capTokens = 0, onCap = "pause", cheaperModel = null), mcp = emptyList(), skills = emptyMap(),
        workflow = Workflow("harness", "harness", null, true, 1, listOf(
            Step(id = "plan", agent = "harness-planner"), Step(id = "build", agent = "harness-builder"), Step(id = "look", agent = "reviewer")), ""),
    )

    @Test
    fun `the session's agents call no model of keel's`() {
        val s = start(mapOf("default" to Model()))
        assertThat(fakeAgents(s)).containsExactly("harness-planner", "harness-builder", "reviewer")
        assertThat(fakeAgents(s, SESSION_AGENTS)).containsExactly("reviewer")
        assertThat(fakeAgents(start(mapOf("default" to Model(), "reviewer" to Model(provider = "claude", model = "sonnet"))), SESSION_AGENTS)).isEmpty()
    }
}
