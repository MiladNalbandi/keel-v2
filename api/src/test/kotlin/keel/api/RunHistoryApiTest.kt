package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.15.4 the Flow page's run history: each run's cost and whether it is the newest, delete (a mark), and its refusals. */
class RunHistoryApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private fun start(pid: String, title: String) =
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to title, "allow_fake" to true)).andExpect(status().isOk)

    private fun end(tid: String, status: String) {
        engine.overrides[tid] = mapOf("status" to status)
        jdbc.update("UPDATE threads SET status = ? WHERE id = ?", status, tid)
    }

    private fun runIds(pid: String) = get("/api/projects/$pid/runs").andExpect(status().isOk).json().map { it["thread_id"].asText() }

    @Test
    fun `a run that runs or waits, or the last stopped one, is never deleted, and a finished one is only hidden from the history`() {
        val (pid, _) = newProject("run-history")
        engine.nextThreadIds.add("t-rh-1")
        start(pid, "Euro prices")
        // it runs, then it waits: both refused, and it stays
        delete("/api/projects/$pid/flows/t-rh-1").andExpect(status().isConflict)
        engine.overrides["t-rh-1"] = mapOf("status" to "waiting")
        val waits = delete("/api/projects/$pid/flows/t-rh-1").andExpect(status().isConflict).json()
        assertThat(waits["error"].asText()).contains("waits for you")
        assertThat(runIds(pid)).containsExactly("t-rh-1")

        end("t-rh-1", "done")
        jdbc.update("INSERT INTO agent_calls(id, project_id, thread_id, agent, status, started_at, tokens_in, tokens_out, cost_usd) " +
            "VALUES ('c-rh-1', ?, 't-rh-1', 'implementer', 'done', '2026-10-01T00:00:00Z', 1000, 500, 0.25)", pid)
        engine.nextThreadIds.add("t-rh-2")
        start(pid, "Dollar prices")
        end("t-rh-2", "stopped")
        jdbc.update("UPDATE threads SET created_at = '2026-01-01T00:00:00Z' WHERE id = 't-rh-1'")

        val runs = get("/api/projects/$pid/runs").json().associateBy { it["thread_id"].asText() }
        assertThat(runs["t-rh-2"]!!["latest"].asBoolean()).isTrue()
        assertThat(runs["t-rh-1"]!!["latest"].asBoolean()).isFalse()
        assertThat(runs["t-rh-1"]!!["cost_usd"].asDouble()).isEqualTo(0.25)
        assertThat(runs["t-rh-1"]!!["tokens"].asLong()).isEqualTo(1500)

        // opening the earlier run (read only) does not make it the project's flow
        val before = jdbc.queryForObject("SELECT updated_at FROM threads WHERE id = 't-rh-1'", String::class.java)
        get("/api/projects/$pid/flows/t-rh-1").andExpect(status().isOk)
        get("/api/projects/$pid/flows/t-rh-1").andExpect(status().isOk)
        assertThat(jdbc.queryForObject("SELECT updated_at FROM threads WHERE id = 't-rh-1'", String::class.java)).isEqualTo(before)
        assertThat(get("/api/projects/$pid/flow").json()["thread"]["thread_id"].asText()).isEqualTo("t-rh-2")

        // the last stopped flow can still be resumed: it stays
        val keep = delete("/api/projects/$pid/flows/t-rh-2").andExpect(status().isConflict).json()
        assertThat(keep["error"].asText()).contains("can still be resumed")

        // the finished one goes from the history; nothing is sent to the engine, and its calls stay for the budget
        val calls = engine.calls.size
        delete("/api/projects/$pid/flows/t-rh-1").andExpect(status().isOk)
        assertThat(engine.calls.drop(calls).filter { it.method != "GET" }).isEmpty()
        assertThat(runIds(pid)).containsExactly("t-rh-2")
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM agent_calls WHERE thread_id = 't-rh-1'", Int::class.java)).isEqualTo(1)
        assertThat(jdbc.queryForObject("SELECT hidden_at FROM threads WHERE id = 't-rh-1'", String::class.java)).isNotBlank()
        assertThat(get("/api/projects/$pid/workflows").json().first { it["id"].asText() == "feature" }["runs"].asInt()).isEqualTo(1)
        // a link to it still opens it
        get("/api/projects/$pid/flows/t-rh-1").andExpect(status().isOk)
        // twice, or a flow of another project or none: 404
        delete("/api/projects/$pid/flows/t-rh-1").andExpect(status().isNotFound)
        delete("/api/projects/$pid/flows/t-nope").andExpect(status().isNotFound)
        val (other, _) = newProject("run-history-2")
        delete("/api/projects/$other/flows/t-rh-2").andExpect(status().isNotFound)

        // a hidden flow that runs again shows again
        engine.overrides["t-rh-1"] = mapOf("status" to "running")
        jdbc.update("UPDATE threads SET status = 'running' WHERE id = 't-rh-1'")
        assertThat(runIds(pid)).contains("t-rh-1")
    }

    @Test
    fun `the newest flow stays when it failed too, and an older stopped one can go`() {
        val (pid, _) = newProject("run-history-failed")
        engine.nextThreadIds.add("t-rhf-1")
        start(pid, "Old stop")
        end("t-rhf-1", "stopped")
        jdbc.update("UPDATE threads SET created_at = '2026-01-01T00:00:00Z' WHERE id = 't-rhf-1'")
        engine.nextThreadIds.add("t-rhf-2")
        start(pid, "New fail")
        end("t-rhf-2", "failed")
        delete("/api/projects/$pid/flows/t-rhf-2").andExpect(status().isConflict)
        delete("/api/projects/$pid/flows/t-rhf-1").andExpect(status().isOk)
        assertThat(runIds(pid)).containsExactly("t-rhf-2")
    }
}
