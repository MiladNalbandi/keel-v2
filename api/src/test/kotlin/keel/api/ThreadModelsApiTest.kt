package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** 0.18.64 a model for one flow (an agent in it, one step, an agent's steps in one phase), not the whole project. */
class ThreadModelsApiTest : ApiTest() {

    @Test
    fun `a flow's own model reaches the engine checked, and the project's agents keep theirs`() {
        val (pid, _) = newProject("thread-models")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Ranks")).andExpect(status().isOk)
        val before = get("/api/projects/$pid/agents").json().first { it["id"].asText() == "explorer" }["model"]

        val res = post("/api/threads/t-stub-1/models", mapOf("agent" to "explorer", "phase" to "spec",
            "model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake", "effort" to "HIGH"))).andExpect(status().isOk).json()
        assertThat(res["changed"].map { it.asText() }).containsExactly("explorer")
        val sent = engine.lastBody("/threads/t-stub-1/models")!!
        assertThat(sent["agent"].asText()).isEqualTo("explorer")
        assertThat(sent["phase"].asText()).isEqualTo("spec")
        assertThat(sent["model"]["effort"].asText()).isEqualTo("high")          // checked as the Agents page checks one
        assertThat(get("/api/projects/$pid/agents").json().first { it["id"].asText() == "explorer" }["model"]).isEqualTo(before)

        post("/api/threads/t-stub-1/models", mapOf("step" to "spec")).andExpect(status().isOk)       // no model: the pick goes
        assertThat(engine.lastBody("/threads/t-stub-1/models")!!.has("model")).isFalse()
        post("/api/threads/t-stub-1/models", mapOf("model" to mapOf("provider" to "fake"))).andExpect(status().isBadRequest)
        post("/api/threads/t-stub-1/models", mapOf("agent" to "explorer", "model" to mapOf("provider" to "fake", "effort" to "a b")))
            .andExpect(status().isBadRequest)
        post("/api/threads/nope/models", mapOf("agent" to "explorer")).andExpect(status().isNotFound)

        // a project on local models only refuses a cloud model for one flow too
        put("/api/projects/$pid/settings", mapOf("local_only" to true)).andExpect(status().isOk)
        post("/api/threads/t-stub-1/models", mapOf("agent" to "explorer",
            "model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "opus"))).andExpect(status().isBadRequest)
        put("/api/projects/$pid/settings", mapOf("local_only" to null)).andExpect(status().isOk)
    }
}
