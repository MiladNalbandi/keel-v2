package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** A gate a workflow says only a person answers (`person: true`): an MCP client with the write level is refused at both doors. */
class PersonGateApiTest : ApiTest() {
    private fun waiting(tid: String, pid: String, person: Boolean) {
        val question = mapOf("step" to "g", "kind" to "gate", "title" to "g", "detail" to "", "options" to listOf("approve", "reject")) +
            (if (person) mapOf("person" to true) else emptyMap())
        engine.extraRoutes["/threads/$tid"] = { _ -> 200 to mapOf("thread_id" to tid, "project_id" to pid, "status" to "waiting", "waiting" to question) }
        engine.extraRoutes["/threads/$tid/resume"] = { _ -> 200 to mapOf("thread_id" to tid, "project_id" to pid, "status" to "running") }
        post("/internal/events", listOf(mapOf("type" to "thread.started", "thread_id" to tid, "project_id" to pid, "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "t"))), mapOf("X-Keel-Token" to TOKEN))
    }

    private fun mcp(level: String): Map<String, String> {
        val token = post("/api/mcp/token", mapOf("level" to level), mapOf("X-Keel-Token" to TOKEN)).json()["token"].asText()
        return mapOf("X-Keel-Via" to "mcp", "X-Keel-Mcp-Token" to token)
    }

    private fun answers(tid: String) = engine.calls.count { it.path == "/threads/$tid/resume" && it.method == "POST" }

    @Test
    fun `an MCP client with the write level cannot answer a person's gate, at the flow or in the Inbox`() {
        val (pid, _) = newProject("person-mcp")
        waiting("t_person", pid, true)
        val body = mapOf("decision" to "approve")
        val res = post("/api/threads/t_person/resume", body, mcp("write")).andExpect(status().isForbidden).json()
        assertThat(res["error"].asText()).contains("Only a person")
        post("/api/inbox/t_person/act", body, mcp("write")).andExpect(status().isForbidden)
        assertThat(answers("t_person")).isEqualTo(0)
    }

    @Test
    fun `the person answers it, and so does the admin level`() {
        val (pid, _) = newProject("person-ok")
        waiting("t_person2", pid, true)
        post("/api/threads/t_person2/resume", mapOf("decision" to "approve")).andExpect(status().isOk)
        post("/api/threads/t_person2/resume", mapOf("decision" to "approve"), mcp("admin")).andExpect(status().isOk)
        assertThat(answers("t_person2")).isEqualTo(2)
    }

    @Test
    fun `any other gate takes an MCP client's answer as before`() {
        val (pid, _) = newProject("person-plain")
        waiting("t_plain", pid, false)
        post("/api/threads/t_plain/resume", mapOf("decision" to "approve"), mcp("write")).andExpect(status().isOk)
        assertThat(answers("t_plain")).isEqualTo(1)
    }
}
