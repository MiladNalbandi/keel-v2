package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.15.2 Jobs and Live agents: the Finished tab (status "finished"), the search (`q`) and the count behind the tabs. */
class JobsSearchApiTest : ApiTest() {

    @Autowired lateinit var jdbc: JdbcTemplate

    private fun ev(type: String, pid: String, callId: String, step: String, data: Map<String, Any?> = emptyMap()) =
        mapOf("type" to type, "thread_id" to "t-$pid", "project_id" to pid, "step" to step, "at" to "2026-10-08T10:00:00Z", "call_id" to callId, "data" to data)

    private fun ids(url: String) = get(url).andExpect(status().isOk).json().map { it["id"].asText() }
    private fun count(url: String) = get(url).andExpect(status().isOk).json()["count"].asInt()

    /** A project called [name] with three calls: [tag]-a test-author done, [tag]-b implementer failed (codex), [tag]-c reviewer still running (claude). */
    private fun seed(tag: String, name: String): String {
        val (pid, _) = newProject("jobs-$tag")
        jdbc.update("UPDATE projects SET name = ? WHERE id = ?", name, pid)
        post("/internal/events", listOf(
            ev("agent.started", pid, "$tag-a", "s1", mapOf("agent" to "test-author", "provider" to "fake", "model" to "fake", "phase" to "red", "ac" to "AC-001")),
            ev("agent.finished", pid, "$tag-a", "s1", mapOf("status" to "done")),
            ev("agent.started", pid, "$tag-b", "s5", mapOf("agent" to "implementer", "provider" to "codex", "model" to "o4", "phase" to "green", "ac" to "AC-002")),
            ev("agent.finished", pid, "$tag-b", "s5", mapOf("status" to "failed", "result" to "tests still red")),
            ev("agent.started", pid, "$tag-c", "s7", mapOf("agent" to "reviewer", "provider" to "claude", "model" to "sonnet", "phase" to "review", "ac" to "AC-002")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        return pid
    }

    @Test
    fun `status finished is every call that does not run any more, and the count has no limit`() {
        val pid = seed("fin", "Finished Tab")
        assertThat(ids("/api/jobs?project=$pid&status=finished")).containsExactlyInAnyOrder("fin-a", "fin-b")
        assertThat(ids("/api/jobs?project=$pid&status=running")).containsExactly("fin-c")
        assertThat(ids("/api/jobs?project=$pid&status=finished&limit=1")).hasSize(1)
        assertThat(count("/api/jobs/count?project=$pid&status=finished")).isEqualTo(2)
        assertThat(count("/api/jobs/count?project=$pid")).isEqualTo(3)
        // the old groups still work
        assertThat(ids("/api/jobs?project=$pid&status=failed")).containsExactly("fin-b")
        assertThat(ids("/api/jobs?project=$pid&status=done")).containsExactly("fin-a")
    }

    @Test
    fun `q finds calls by agent, provider name, model, step, AC, status and project name, and every word must match`() {
        val pid = seed("qq", "Zebra Shop")
        val base = "/api/jobs?project=$pid"
        assertThat(ids("$base&q=implementer")).containsExactly("qq-b")
        assertThat(ids("$base&q=GPT")).containsExactly("qq-b") // the web's name for codex is "GPT / Codex"
        assertThat(ids("$base&q=sonnet")).containsExactly("qq-c")
        assertThat(ids("$base&q=ac-002")).containsExactlyInAnyOrder("qq-b", "qq-c")
        assertThat(ids("$base&q=s5")).containsExactly("qq-b")
        assertThat(ids("$base&q=FAILED")).containsExactly("qq-b")
        assertThat(ids("$base&q=ac-002 green")).containsExactly("qq-b")
        assertThat(ids("$base&q=green review")).isEmpty()
        assertThat(ids("$base&q=  ")).hasSize(3) // only spaces: no search
        // the project's name, not only its id (here across all projects)
        assertThat(ids("/api/jobs?q=zebra shop")).containsExactlyInAnyOrder("qq-a", "qq-b", "qq-c")
        // % and _ are plain letters, not SQL wildcards
        assertThat(ids("$base&q=%")).isEmpty()
        assertThat(ids("$base&q=_")).isEmpty()
        // search and Finished together, and the count agrees with the list
        assertThat(ids("$base&status=finished&q=ac-002")).containsExactly("qq-b")
        assertThat(count("/api/jobs/count?project=$pid&status=finished&q=ac-002")).isEqualTo(1)
        assertThat(count("/api/jobs/count?project=$pid&q=nothing-like-this")).isEqualTo(0)
    }
}
