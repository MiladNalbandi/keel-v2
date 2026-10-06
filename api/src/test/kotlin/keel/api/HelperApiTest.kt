package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.6.0 keel's Helper: the api's sessions proxy the engine, a turn carries what the engine needs, and its events are agent calls. */
class HelperApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private fun patch(url: String, body: Any) = mvc.perform(
        MockMvcRequestBuilders.patch(url).contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsString(body)))

    @Test
    fun `a session is the engine's, for this project, with the helper agent's model`() {
        val (pid, root) = newProject("helper-sessions")
        val s = post("/api/projects/$pid/helper/sessions", mapOf("title" to "Scores")).andExpect(status().isOk).json()
        val sent = engine.lastBody("/helper/sessions")!!
        assertThat(sent["project_id"].asText()).isEqualTo(pid)
        assertThat(sent["root"].asText()).isEqualTo(root.toString())
        assertThat(sent["mode"].asText()).isEqualTo("ask")
        assertThat(sent["model"]["model"].asText()).isNotBlank()
        val sid = s["id"].asText()
        assertThat(get("/api/projects/$pid/helper/sessions/$sid").andExpect(status().isOk).json()["title"].asText()).isEqualTo("Scores")
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("title" to "Ranks")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!["title"].asText()).isEqualTo("Ranks")
        post("/api/projects/$pid/helper/sessions", mapOf("mode" to "edit-everything")).andExpect(status().isBadRequest)

        // a session of another project is not found here
        val (other, _) = newProject("helper-other")
        get("/api/projects/$other/helper/sessions/$sid").andExpect(status().isNotFound)
        post("/api/projects/$other/helper/sessions/$sid/turn", mapOf("text" to "hi")).andExpect(status().isNotFound)

        delete("/api/projects/$pid/helper/sessions/$sid").andExpect(status().isOk)
        get("/api/projects/$pid/helper/sessions/$sid").andExpect(status().isNotFound)
    }

    @Test
    fun `a turn carries the knowledge, MCP, what the person points at and the waiting flow`() {
        val (pid, _) = newProject("helper-turn")
        val sid = post("/api/projects/$pid/helper/sessions").json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "")).andExpect(status().isBadRequest)

        // a flow that waits at a gate: its title, criteria and gate go along
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Discount codes", "allow_fake" to true)).andExpect(status().isOk)
        engine.overrides["t-stub-1"] = mapOf("status" to "waiting", "title" to "Discount codes",
            "waiting" to mapOf("step" to "g-spec", "kind" to "gate", "title" to "spec approval", "detail" to "4 criteria", "options" to listOf("approve", "reject")))
        val r = post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf(
            "text" to "Why does AC-1 fail?",
            "mentions" to listOf(mapOf("kind" to "file", "value" to "src/a.kt"), mapOf("kind" to "symbol", "value" to "")),
            "selection" to mapOf("path" to "src/a.kt", "from" to 3, "to" to 4, "text" to "val x = 1"),
            "open_file" to "src/a.kt",
        )).andExpect(status().isOk).json()
        assertThat(r["call_id"].asText()).isEqualTo("call-$sid")
        val body = engine.lastBody("/helper/sessions/$sid/turn")!!
        assertThat(body["text"].asText()).isEqualTo("Why does AC-1 fail?")
        assertThat(body["agents"]["helper"]["knowledge"]["sections"].map { it.asText() }).containsExactly("architecture", "conventions")
        assertThat(body["mcp"].isArray).isTrue()
        assertThat(body["mentions"].map { it["value"].asText() }).containsExactly("src/a.kt")       // empty ones dropped
        assertThat(body["selection"]["from"].asInt()).isEqualTo(3)
        assertThat(body["open_file"].asText()).isEqualTo("src/a.kt")
        assertThat(body["flow"]["title"].asText()).isEqualTo("Discount codes")
        assertThat(body["flow"]["status"].asText()).isEqualTo("waits")
        assertThat(body["flow"]["waiting"]["title"].asText()).isEqualTo("spec approval")
        assertThat(body["flow"]["acs"][0]["id"].asText()).isEqualTo("AC-001")
        assertThat(get("/api/projects/$pid/helper/commands").json()[0]["name"].asText()).isEqualTo("explain")
    }

    @Test
    fun `its events are agent calls with their steps, counted in the budget, and never a flow`() {
        val (pid, _) = newProject("helper-events")
        fun ev(type: String, data: Map<String, Any?>) = mapOf("type" to type, "thread_id" to "h_abc", "project_id" to pid, "step" to "helper",
            "call_id" to "c-helper-1", "at" to java.time.Instant.now().toString(), "data" to data)
        post("/internal/events", listOf(
            ev("helper.started", mapOf("agent" to "helper", "provider" to "claude", "model" to "sonnet", "mode" to "subscription", "phase" to "helper-ask")),
            ev("helper.step", mapOf("n" to 1, "kind" to "read", "text" to "line 1", "path" to "src/a.kt", "ok" to true)),
            ev("helper.finished", mapOf("agent" to "helper", "status" to "done", "tokens_in" to 1200, "tokens_out" to 300, "cost_usd" to 0.01, "result" to "It is in src/a.kt:1")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val row = jdbc.queryForMap("SELECT agent, thread_id, status, tokens_in, mode, phase FROM agent_calls WHERE id = 'c-helper-1'")
        assertThat(row["agent"]).isEqualTo("helper")
        assertThat(row["thread_id"]).isEqualTo("h_abc")
        assertThat(row["status"]).isEqualTo("done")
        assertThat((row["tokens_in"] as Number).toInt()).isEqualTo(1200)
        assertThat(row["mode"]).isEqualTo("subscription")
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM agent_steps WHERE call_id = 'c-helper-1'", Int::class.java)).isEqualTo(1)
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE id = 'h_abc'", Int::class.java)).isEqualTo(0)
        assertThat(get("/api/projects/$pid/budget/now").json()["today"]["tokens"].asLong()).isEqualTo(1500)
        assertThat(get("/api/projects/$pid/flow").json()["thread"].isNull).isTrue()
    }
}
