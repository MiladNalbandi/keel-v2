package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** POST /api/projects/{pid}/workflows/explain-step passes a saved workflow, a draft or a thread to the engine's /steps/explain. */
class ExplainApiTest : ApiTest() {
    @Test
    fun `a saved workflow, a draft and a thread go through with the project's folder and agents`() {
        val (pid, root) = newProject("explain-api")
        val saved = post("/api/projects/$pid/workflows/explain-step", """{"workflow_id":"feature","step_id":"red"}""")
            .andExpect(status().isOk).json()
        assertThat(saved["id"].asText()).isEqualTo("red")
        val sent = engine.lastBody("/steps/explain")!!
        assertThat(sent["workflow"]["id"].asText()).isEqualTo("feature")
        assertThat(sent["workflow"]["steps"][2]["per_ac"].asBoolean()).isTrue()
        assertThat(sent["root"].asText()).isEqualTo(root.toString())
        assertThat(sent["project_id"].asText()).isEqualTo(pid)
        assertThat(sent.has("agents")).isTrue()
        assertThat(sent.has("thread_id")).isFalse()

        post("/api/projects/$pid/workflows/explain-step",
            """{"workflow":{"name":"draft","steps":[{"id":"a","kind":"agent","name":"a","agent":"explorer"}]},"step_id":"a"}""")
            .andExpect(status().isOk)
        assertThat(engine.lastBody("/steps/explain")!!["workflow"]["name"].asText()).isEqualTo("draft")

        val withThread = post("/api/projects/$pid/workflows/explain-step", """{"step_id":"red","thread_id":"t-1"}""")
            .andExpect(status().isOk).json()
        assertThat(withThread["thread"].asBoolean()).isTrue()
        assertThat(engine.lastBody("/steps/explain")!!.has("workflow")).isFalse()

        post("/api/projects/$pid/workflows/explain-step", """{"workflow_id":"feature","step_id":"nope"}""").andExpect(status().isNotFound)
        post("/api/projects/$pid/workflows/explain-step", """{"step_id":"red"}""").andExpect(status().isBadRequest)
        post("/api/projects/$pid/workflows/explain-step", """{"workflow_id":"feature"}""").andExpect(status().isBadRequest)
        post("/api/projects/missing/workflows/explain-step", """{"workflow_id":"feature","step_id":"red"}""").andExpect(status().isNotFound)
    }
}
