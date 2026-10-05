package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.4.1: the inbox (everything waiting, across projects), notification clean-up, and run modes through the api. */
class InboxApiTest : ApiTest() {

    private fun ev(type: String, pid: String, tid: String, step: String? = null, at: String = "2026-10-06T10:00:00Z", data: Map<String, Any?> = emptyMap()) =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "at" to at, "data" to data)

    private fun send(vararg events: Map<String, Any?>) =
        post("/internal/events", events.toList(), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    private fun waits(tid: String, waiting: Map<String, Any?>, extra: Map<String, Any?> = emptyMap()) {
        engine.overrides[tid] = mapOf("status" to "waiting", "waiting" to waiting, "title" to "t") + extra
    }

    private fun notesOf(tid: String) = get("/api/notifications?limit=500").json().filter { it["thread_id"]?.asText() == tid }

    @Test
    fun `the inbox lists what waits in every project, filters it and answers it like the Flow page`() {
        val (pa, _) = newProject("inbox-alpha")
        val (pb, _) = newProject("inbox-beta")
        val long = "Spec: docs/specs/ranks.md\n" + "x".repeat(1000)
        send(
            ev("thread.started", pa, "ib-a", data = mapOf("workflow" to "feature", "title" to "Player ranks")),
            ev("gate.waiting", pa, "ib-a", "spec_gate", "2026-10-06T09:00:00Z", mapOf("kind" to "clarify", "title" to "The explorer has 1 question", "detail" to long)),
            ev("thread.started", pb, "ib-b", data = mapOf("workflow" to "fix", "title" to "Fix the tally")),
            ev("gate.waiting", pb, "ib-b", "commit", "2026-10-06T09:30:00Z", mapOf("kind" to "fix", "title" to "Approve new dependency", "detail" to "httpx")),
            ev("thread.started", pb, "ib-gone", data = mapOf("workflow" to "fix", "title" to "Moved on")),
            ev("gate.waiting", pb, "ib-gone", "gate_r", "2026-10-06T09:40:00Z", mapOf("kind" to "gate", "title" to "gate R")),
        )
        waits("ib-a", mapOf("step" to "spec_gate", "kind" to "clarify", "title" to "The explorer has 1 question", "detail" to long,
            "options" to listOf("approve"), "id" to "qa", "labels" to mapOf("approve" to "Send my answers"),
            "questions" to listOf(mapOf("id" to "q1", "question" to "Who sees it?", "options" to listOf(mapOf("label" to "players", "recommended" to true))))),
            mapOf("run_mode" to "important", "phase" to "spec",
                "gate_log" to listOf("gate options approve", "ac AC-1 approve: auto-approved (mode important)")))
        waits("ib-b", mapOf("step" to "commit", "kind" to "fix", "title" to "Approve new dependency", "detail" to "httpx = \">=0.27\"",
            "options" to listOf("approve", "reject"), "id" to "qb", "labels" to mapOf("approve" to "Allow", "reject" to "Refuse")))
        // ib-gone: the engine says it runs again (no override), so it is not listed and the api's row follows

        val view = get("/api/inbox").andExpect(status().isOk).json()
        val mine = view["items"].filter { it["project_id"].asText() in setOf(pa, pb) }
        assertThat(mine.map { it["thread_id"].asText() }).containsExactly("ib-a", "ib-b")       // oldest first
        val a = mine[0]
        assertThat(a["project_name"].asText()).isEqualTo("inbox-alpha")
        assertThat(a["flow"].asText()).isEqualTo("Player ranks")
        assertThat(a["workflow_id"].asText()).isEqualTo("feature")
        assertThat(a["kind"].asText()).isEqualTo("clarify")
        assertThat(a["step"].asText()).isEqualTo("spec_gate")
        assertThat(a["detail"].asText()).hasSizeLessThan(720).endsWith("…")
        assertThat(a["more"].asBoolean()).isTrue()
        assertThat(a["questions"][0]["id"].asText()).isEqualTo("q1")
        assertThat(a["options"].map { it.asText() }).containsExactly("approve")
        assertThat(a["id"].asText()).isEqualTo("qa")
        assertThat(a["run_mode"].asText()).isEqualTo("important")
        assertThat(a["auto_approved"].asInt()).isEqualTo(1)
        assertThat(a["last_auto"].asText()).isEqualTo("ac AC-1 approve: auto-approved (mode important)")
        assertThat(a["since"].asText()).isEqualTo("2026-10-06T09:00:00Z")
        val b = mine[1]
        assertThat(b["kind"].asText()).isEqualTo("dependency")
        assertThat(b["labels"]["approve"].asText()).isEqualTo("Allow")
        assertThat(view["kinds"].map { it.asText() }).contains("clarify", "dependency")
        assertThat(view["projects"].first { it["id"].asText() == pb }["count"].asInt()).isEqualTo(1)

        assertThat(get("/api/inbox?project=$pa").json()["items"].map { it["thread_id"].asText() }).containsExactly("ib-a")
        val deps = get("/api/inbox?kind=dependency").json()["items"].map { it["thread_id"].asText() }
        assertThat(deps).contains("ib-b").doesNotContain("ib-a")
        val count = get("/api/inbox/count").andExpect(status().isOk).json()
        assertThat(count["projects"][pa].asInt()).isEqualTo(1)
        assertThat(count["projects"][pb].asInt()).isEqualTo(1)                 // ib-gone's row was corrected by the list
        assertThat(get("/api/projects/$pb").json()["waiting"].asInt()).isEqualTo(1)

        // acting: a stale question is refused, the right one goes through the Flow page's resume
        post("/api/inbox/ib-b/act", mapOf("decision" to "approve", "id" to "an-older-question")).andExpect(status().isConflict)
        post("/api/inbox/ib-b/act", mapOf("decision" to "maybe")).andExpect(status().isBadRequest)
        post("/api/inbox/nope/act", mapOf("decision" to "approve")).andExpect(status().isNotFound)
        assertThat(notesOf("ib-a").single()["done"].asBoolean()).isFalse()
        val next = post("/api/inbox/ib-a/act", mapOf("decision" to "approve", "id" to "qa", "payload" to mapOf("answers" to mapOf("q1" to "players"))))
            .andExpect(status().isOk).json()
        assertThat(next["status"].asText()).isEqualTo("running")
        val sent = engine.lastBody("/threads/ib-a/resume")!!
        assertThat(sent["decision"].asText()).isEqualTo("approve")
        assertThat(sent["payload"]["answers"]["q1"].asText()).isEqualTo("players")
        assertThat(notesOf("ib-a").single()["done"].asBoolean()).isTrue()          // its gate is decided
        post("/api/inbox/ib-a/act", mapOf("decision" to "approve")).andExpect(status().isConflict)   // it waits no more
        post("/api/inbox/ib-b/act", mapOf("decision" to "reject", "why" to "no new libraries", "id" to "qb")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/ib-b/resume")!!["why"].asText()).isEqualTo("no new libraries")
        assertThat(get("/api/inbox").json()["items"].none { it["project_id"].asText() in setOf(pa, pb) }).isTrue()
    }

    @Test
    fun `a gate's notification is done once the gate is decided, from anywhere`() {
        val (pid, _) = newProject("inbox-notes")
        send(ev("gate.waiting", pid, "nd-1", "spec_gate", data = mapOf("kind" to "gate", "title" to "Spec ready")))
        val n = notesOf("nd-1").single()
        assertThat(n["step"].asText()).isEqualTo("spec_gate")
        assertThat(n["done"].asBoolean()).isFalse()
        assertThat(n["read"].asBoolean()).isFalse()
        // decided in the engine (MCP, the run mode, another tab): the gate.decided event marks it
        send(ev("gate.decided", pid, "nd-1", "spec_gate", data = mapOf("gate" to "spec", "decision" to "approve")))
        assertThat(notesOf("nd-1").single()["done"].asBoolean()).isTrue()
        assertThat(notesOf("nd-1").single()["read"].asBoolean()).isTrue()

        // decided on the Flow page (POST /threads/{id}/resume): done; the next gate's notification stays open
        send(ev("gate.waiting", pid, "nd-2", "ac_gate", data = mapOf("kind" to "gate", "title" to "AC gate")))
        waits("nd-2", mapOf("step" to "ac_gate", "kind" to "gate", "title" to "AC gate", "options" to listOf("approve", "reject")))
        post("/api/threads/nd-2/resume", mapOf("decision" to "approve")).andExpect(status().isOk)
        assertThat(notesOf("nd-2").single()["done"].asBoolean()).isTrue()
        send(ev("gate.waiting", pid, "nd-2", "ac_gate", data = mapOf("kind" to "gate", "title" to "AC gate · AC-2")))
        assertThat(notesOf("nd-2").map { it["done"].asBoolean() }).containsExactly(false, true)
        // a finished flow leaves nothing open
        send(ev("thread.done", pid, "nd-2"))
        assertThat(notesOf("nd-2").filter { it["type"].asText() == "review" }.all { it["done"].asBoolean() }).isTrue()
    }

    @Test
    fun `notifications can be read, deleted one by one and cleared`() {
        val (pid, _) = newProject("inbox-clean")
        send(
            ev("gate.waiting", pid, "cl-1", "g", data = mapOf("title" to "first")),
            ev("gate.waiting", pid, "cl-2", "g", data = mapOf("title" to "second")),
        )
        val ids = get("/api/notifications?limit=500").json().filter { it["project_id"].asText() == pid }.map { it["id"].asLong() }
        assertThat(ids).hasSize(2)
        post("/api/notifications/${ids[0]}/read").andExpect(status().isOk)
        assertThat(get("/api/notifications?limit=500").json().first { it["id"].asLong() == ids[0] }["read"].asBoolean()).isTrue()
        assertThat(post("/api/notifications/read-all").andExpect(status().isOk).json()["count"].asInt()).isGreaterThanOrEqualTo(1)
        delete("/api/notifications/${ids[1]}").andExpect(status().isOk)
        delete("/api/notifications/${ids[1]}").andExpect(status().isNotFound)
        assertThat(get("/api/notifications?limit=500").json().map { it["id"].asLong() }).contains(ids[0]).doesNotContain(ids[1])
        assertThat(delete("/api/notifications").andExpect(status().isOk).json()["count"].asInt()).isGreaterThanOrEqualTo(1)
        assertThat(get("/api/notifications").json().size()).isEqualTo(0)
    }

    @Test
    fun `the run mode comes from settings or the start, reaches the engine, and changes during a run`() {
        val (pid, _) = newProject("inbox-modes")
        assertThat(get("/api/settings/general").json()["run_mode"].asText()).isEqualTo("manual")
        put("/api/settings/general", mapOf("run_mode" to "fast")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/settings", mapOf("run_mode" to "important")).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/settings").json()["effective"]["run_mode"].asText()).isEqualTo("important")

        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Ranks")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["settings"]["run_mode"].asText()).isEqualTo("important")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Ranks again", "run_mode" to "auto", "allow_dirty" to true))
            .andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["settings"]["run_mode"].asText()).isEqualTo("auto")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x", "run_mode" to "yolo")).andExpect(status().isBadRequest)

        val state = post("/api/threads/t-stub-1/mode", mapOf("mode" to "readonly")).andExpect(status().isOk).json()
        assertThat(state["run_mode"].asText()).isEqualTo("readonly")
        assertThat(engine.lastBody("/threads/t-stub-1/mode")!!["mode"].asText()).isEqualTo("readonly")
        post("/api/threads/t-stub-1/mode", mapOf("mode" to "sometimes")).andExpect(status().isBadRequest)
        post("/api/threads/t-stub-1/mode", mapOf<String, Any>()).andExpect(status().isBadRequest)
        put("/api/projects/$pid/settings", mapOf("run_mode" to null)).andExpect(status().isOk)
    }
}
