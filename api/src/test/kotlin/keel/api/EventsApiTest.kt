package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

class EventsApiTest : ApiTest() {

    private fun ev(type: String, pid: String, tid: String, callId: String? = null, step: String? = null, data: Map<String, Any?> = emptyMap()) =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "at" to "2026-10-03T10:00:00Z", "call_id" to callId, "data" to data)

    private fun send(events: List<Map<String, Any?>>) =
        post("/internal/events", events, mapOf("X-Keel-Token" to TOKEN))

    @Test
    fun `events need the internal token`() {
        post("/internal/events", emptyList<Any>()).andExpect(status().isForbidden)
        post("/internal/events", emptyList<Any>(), mapOf("X-Keel-Token" to "wrong")).andExpect(status().isForbidden)
        send(emptyList()).andExpect(status().isOk)
    }

    @Test
    fun `agent events become a job with steps, tokens and mcp calls`() {
        val (pid, _) = newProject("events-jobs")
        val tid = "t-jobs"
        send(listOf(
            ev("thread.started", pid, tid, data = mapOf("title" to "Scores")),
            ev("agent.started", pid, tid, "c1", "red", mapOf("agent" to "test-author", "provider" to "fake", "model" to "fake", "phase" to "red", "ac" to "AC-001")),
            ev("agent.step", pid, tid, "c1", "red", mapOf("n" to 1, "kind" to "thinking", "text" to "Reading the AC")),
            ev("agent.step", pid, tid, "c1", "red", mapOf("n" to 2, "kind" to "tool", "text" to "keel_next", "tool" to "keel_next", "server" to "keel", "ms" to 12, "ok" to true)),
            ev("agent.step", pid, tid, "c1", "red", mapOf("n" to 3, "kind" to "write", "text" to "wrote test", "path" to "src/test/ScoreTest.kt", "diff" to "+ test")),
            // a retried duplicate must not count twice
            ev("agent.step", pid, tid, "c1", "red", mapOf("n" to 3, "kind" to "write", "text" to "wrote test")),
            ev("agent.finished", pid, tid, "c1", "red", mapOf("status" to "done", "tokens_in" to 1200, "tokens_out" to 300, "cost_usd" to 0.05, "premium_requests" to 1)),
        )).andExpect(status().isOk)

        val jobs = get("/api/jobs?project=$pid").andExpect(status().isOk).json()
        assertThat(jobs.size()).isEqualTo(1)
        val job = jobs[0]
        assertThat(job["id"].asText()).isEqualTo("c1")
        assertThat(job["agent"].asText()).isEqualTo("test-author")
        assertThat(job["status"].asText()).isEqualTo("done")
        assertThat(job["tokens_in"].asLong()).isEqualTo(1200)
        assertThat(job["tokens_out"].asLong()).isEqualTo(300)
        assertThat(job["cost_usd"].asDouble()).isEqualTo(0.05)
        assertThat(job["premium_requests"].asInt()).isEqualTo(1)
        assertThat(job["steps_count"].asInt()).isEqualTo(3)
        assertThat(job["mcp_calls"].asInt()).isEqualTo(1)
        assertThat(job["phase"].asText()).isEqualTo("red")

        val detail = get("/api/jobs/c1").json()
        assertThat(detail["agent"].asText()).isEqualTo("test-author")
        assertThat(detail["steps"].map { it["kind"].asText() }).containsExactly("thinking", "tool", "write")
        assertThat(detail["steps"][1]["server"].asText()).isEqualTo("keel")
        assertThat(detail["steps"][1]["ok"].asBoolean()).isTrue()

        val after = get("/api/jobs/c1/steps?after=2").json()
        assertThat(after["steps"].map { it["n"].asInt() }).containsExactly(3)
        assertThat(after["running"].asBoolean()).isFalse()

        assertThat(get("/api/jobs?project=$pid&status=running").json().size()).isEqualTo(0)
        assertThat(get("/api/jobs?project=$pid&agent=test-author&provider=fake").json().size()).isEqualTo(1)

        val budget = get("/api/projects/$pid/budget").json()
        assertThat(budget["days"].size()).isEqualTo(14)
        assertThat(budget["top"][0]["agent"].asText()).isEqualTo("test-author")
    }

    @Test
    fun `gates, failures, budget and done become notifications by kind`() {
        val (pid, _) = newProject("events-notes")
        val tid = "t-notes"
        send(listOf(
            ev("agent.started", pid, tid, "n1", "green", mapOf("agent" to "implementer", "provider" to "fake")),
            ev("gate.waiting", pid, tid, step = "g1", data = mapOf("kind" to "gate", "title" to "AC gate waits for you", "detail" to "AC-002 Refuse a negative score")),
            ev("guard.refused", pid, tid, step = "green", data = mapOf("agent" to "implementer", "path" to "src/test/A.kt", "phase" to "green")),
            ev("agent.finished", pid, tid, "n1", "green", mapOf("status" to "failed", "result" to "tests still red")),
            ev("budget.warn", pid, tid, data = mapOf("tokens" to 400000, "cap_tokens" to 500000)),
            ev("thread.done", pid, tid),
        )).andExpect(status().isOk)

        val mine = get("/api/notifications?limit=50").json().filter { it["project_id"].asText() == pid }
        val types = mine.map { it["type"].asText() }
        // started is off by default
        assertThat(types).containsExactlyInAnyOrder("finished", "budget", "failed", "failed", "review")
        val review = mine.first { it["type"].asText() == "review" }
        assertThat(review["title"].asText()).isEqualTo("AC gate waits for you")
        assertThat(review["body"].asText()).contains("AC-002")
        assertThat(review["read"].asBoolean()).isFalse()
        assertThat(mine.first { it["type"].asText() == "budget" }["body"].asText()).isEqualTo("400k of 500k tokens used.")

        val project = get("/api/projects/$pid").json()
        assertThat(project["waiting"].asInt()).isEqualTo(0) // thread.done after the gate

        post("/api/notifications/${review["id"].asLong()}/read").andExpect(status().isOk)
        assertThat(get("/api/notifications").json().first { it["id"].asLong() == review["id"].asLong() }["read"].asBoolean()).isTrue()
        post("/api/notifications/999999/read").andExpect(status().isNotFound)
    }

    @Test
    fun `notification settings and the project notify setting are respected`() {
        val (pid, _) = newProject("events-settings")
        val s = get("/api/notification-settings").json()
        assertThat(s["kinds"]["started"].asBoolean()).isFalse()

        val on = mapper.convertValue(s, Map::class.java).toMutableMap()
        on["kinds"] = mapOf("review" to true, "failed" to false, "budget" to true, "finished" to true, "started" to true)
        put("/api/notification-settings", on).andExpect(status().isOk)
        try {
            send(listOf(
                ev("agent.started", pid, "t-s", "s1", data = mapOf("agent" to "explorer")),
                ev("thread.failed", pid, "t-s", data = mapOf("error" to "boom")),
            ))
            var types = get("/api/notifications?limit=100").json().filter { it["project_id"].asText() == pid }.map { it["type"].asText() }
            assertThat(types).containsExactly("started")

            // Project says "needs you" only: a finished flow is quiet, a gate is not.
            put("/api/projects/$pid/settings", mapOf("notify" to "needs_you")).andExpect(status().isOk)
            send(listOf(
                ev("thread.done", pid, "t-s"),
                ev("gate.waiting", pid, "t-s2", data = mapOf("title" to "Spec ready")),
            ))
            types = get("/api/notifications?limit=100").json().filter { it["project_id"].asText() == pid }.map { it["type"].asText() }
            assertThat(types).containsExactlyInAnyOrder("started", "review")
            assertThat(get("/api/projects/$pid").json()["waiting"].asInt()).isEqualTo(1)
        } finally {
            put("/api/notification-settings", s).andExpect(status().isOk)
        }
        post("/api/notifications/read-all").andExpect(status().isOk)
        assertThat(get("/api/notifications").json().none { !it["read"].asBoolean() }).isTrue()
    }
}
