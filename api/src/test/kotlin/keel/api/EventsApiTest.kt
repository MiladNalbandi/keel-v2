package keel.api

import keel.api.support.ApiTest
import keel.api.workflows.WorkflowDoc
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

class EventsApiTest : ApiTest() {

    private fun ev(type: String, pid: String, tid: String, callId: String? = null, step: String? = null, data: Map<String, Any?> = emptyMap()) =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "at" to java.time.Instant.now().toString(), "call_id" to callId, "data" to data)

    private fun send(events: List<Map<String, Any?>>) =
        post("/internal/events", events, mapOf("X-Keel-Token" to TOKEN))

    @Test
    fun `live text (helper delta, agent delta) goes to the screens by its own name and is never stored`(
        @org.springframework.beans.factory.annotation.Autowired jdbc: org.springframework.jdbc.core.JdbcTemplate,
    ) {
        val (pid, _) = newProject("events-delta")
        send(listOf(
            ev("helper.delta", pid, "h_1", callId = "c1", step = "helper", data = mapOf("n" to 1, "block" to 0, "text" to "The entry")),
            ev("agent.delta", pid, "t-delta", callId = "c2", step = "spec", data = mapOf("n" to 1, "block" to 0, "text" to "Spec")),
            ev("helper.step", pid, "h_1", callId = "c1", step = "helper", data = mapOf("n" to 1, "kind" to "text", "text" to "The entry is main.py")),
            ev("helper.tool", pid, "h_1", callId = "c1", step = "helper", data = mapOf("n" to 2, "name" to "keel_status", "status" to "ok", "output" to "ok")),
        )).andExpect(status().isOk)
        val types = jdbc.queryForList("SELECT type FROM events WHERE project_id = ?", String::class.java, pid)
        assertThat(types).containsExactly("helper.step")
        assertThat(keel.api.events.EventHub.channelOf("helper.tool")).isEqualTo("helper.tool")
        assertThat(keel.api.events.EventHub.channelOf("helper.delta")).isEqualTo("helper.delta")
        assertThat(keel.api.events.EventHub.channelOf("agent.delta")).isEqualTo("agent.delta")
    }

    @Test
    fun `agent usage books a running call's tokens so far, and never overwrites the finished total`(
        @org.springframework.beans.factory.annotation.Autowired jdbc: org.springframework.jdbc.core.JdbcTemplate,
    ) {
        val (pid, _) = newProject("events-usage")
        fun tokens() = jdbc.queryForMap("SELECT tokens_in, tokens_out, tokens_cached, status FROM agent_calls WHERE id = 'u1'")
        send(listOf(
            ev("agent.started", pid, "t-usage", callId = "u1", step = "impl", data = mapOf("agent" to "implementer", "provider" to "claude", "mode" to "subscription")),
            ev("agent.usage", pid, "t-usage", callId = "u1", step = "impl", data = mapOf("tokens_in" to 1200, "tokens_out" to 300, "tokens_cached" to 50_000)),
        )).andExpect(status().isOk)
        assertThat(tokens()).containsEntry("tokens_in", 1200).containsEntry("tokens_out", 300).containsEntry("tokens_cached", 50_000)
        send(listOf(ev("agent.finished", pid, "t-usage", callId = "u1", step = "impl",
            data = mapOf("status" to "done", "tokens_in" to 2000, "tokens_out" to 500, "tokens_cached" to 90_000)))).andExpect(status().isOk)
        send(listOf(ev("agent.usage", pid, "t-usage", callId = "u1", step = "impl", data = mapOf("tokens_in" to 1500, "tokens_out" to 400))))
            .andExpect(status().isOk)
        assertThat(tokens()).containsEntry("tokens_in", 2000).containsEntry("tokens_out", 500).containsEntry("status", "done")
    }

    @Test
    fun `a thread the engine started by itself (start_flow) keeps its workflow id`(@org.springframework.beans.factory.annotation.Autowired jdbc: org.springframework.jdbc.core.JdbcTemplate) {
        val (pid, _) = newProject("events-child")
        send(listOf(ev("thread.started", pid, "t-child", data = mapOf("workflow" to "fix", "title" to "Fix the rank", "parent" to mapOf("thread_id" to "t-parent")))))
            .andExpect(status().isOk)
        assertThat(jdbc.queryForObject("SELECT workflow_id FROM threads WHERE id = 't-child'", String::class.java)).isEqualTo("fix")
    }

    @Test
    fun `the engine's fan-out, loop, marker and hand-off keys survive the api's workflow model`() {
        val doc = WorkflowDoc.parse("""
            name: hand-off
            steps:
              - { id: hyp, kind: parallel, name: investigators, agent: investigator, from: hypotheses, cap: 4, batch: 2, markers: [ROOT-CAUSE] }
              - { id: each, kind: agent, name: per group, agent: implementer, for_each: coverage_groups, per_item: true, collect: notes }
              - { id: repro, kind: branch, name: reproduced, when: { marker: REPRO, equals: confirmed }, no: hand }
              - { id: hand, kind: code, name: hand to fix, action: start_flow, flow: fix, seed: { title: "${'$'}title" }, then: end }
        """.trimIndent())
        val yaml = doc.toYaml()
        listOf("from: hypotheses", "cap: 4", "batch: 2", "for_each: coverage_groups", "per_item: true", "marker: REPRO",
               "equals: confirmed", "flow: fix", "title: \$title", "then: end", "- ROOT-CAUSE").forEach { assertThat(yaml).contains(it) }
    }

    @Test
    fun `the ship and cover keys and the include step survive the api's workflow model`() {
        val doc = WorkflowDoc.parse("""
            name: tail
            steps:
              - { id: plan, kind: gate, name: which steps, skip_menu: true }
              - { id: fix, kind: agent, name: fix, agent: implementer, retry_only: true }
              - { id: verify, kind: code, name: verify, action: verify_fast, rounds: 2, back: fix }
              - { id: cov, kind: code, name: measure, action: verify_coverage, soft: true, skippable: deferred, group: coverage }
              - { id: decide, kind: gate, name: decide, for_each: coverage_groups, choices: [test, accept], on_skip: { choice: accept, record: coverage_accepted } }
              - { id: review, kind: agent, name: review, agent: reviewer, redo: verify }
              - { id: final, kind: gate, name: final, report: verdicts }
              - { id: ship, kind: include, name: ship, flow: ship }
        """.trimIndent())
        val yaml = doc.toYaml()
        listOf("skip_menu: true", "retry_only: true", "rounds: 2", "back: fix", "soft: true", "skippable: deferred", "group: coverage",
               "- accept", "choice: accept", "record: coverage_accepted", "redo: verify", "report: verdicts", "kind: include", "flow: ship")
            .forEach { assertThat(yaml).contains(it) }
    }

    @Test
    fun `events need the internal token`() {
        bare.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post("/internal/events")
            .contentType("application/json").content("[]")).andExpect(status().isUnauthorized)
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
            ev("agent.step", pid, tid, "c1", "red", mapOf("n" to 2, "kind" to "tool", "text" to "keel_next", "tool" to "keel_next", "server" to "keel", "ms" to 12, "ok" to true, "output" to "phase: red\nnext: write the test")),
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
        assertThat(detail["steps"][1]["output"].asText()).isEqualTo("phase: red\nnext: write the test")

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
        // an older engine sends only the step's title: it gets the project's name and the mark
        assertThat(review["title"].asText()).isEqualTo("◆ events-notes: AC gate waits for you")
        assertThat(review["body"].asText()).contains("AC-002")
        assertThat(review["summary"].map { it.asText() }).containsExactly("AC-002 Refuse a negative score")
        assertThat(review["read"].asBoolean()).isFalse()
        assertThat(mine.first { it["type"].asText() == "budget" }["body"].asText()).isEqualTo("400k of 500k tokens used.")

        val project = get("/api/projects/$pid").json()
        assertThat(project["waiting"].asInt()).isEqualTo(0) // thread.done after the gate

        post("/api/notifications/${review["id"].asLong()}/read").andExpect(status().isOk)
        assertThat(get("/api/notifications").json().first { it["id"].asLong() == review["id"].asLong() }["read"].asBoolean()).isTrue()
        post("/api/notifications/999999/read").andExpect(status().isNotFound)
    }

    @Test
    fun `notifications have a title a person reads and at most three short lines, the full text stays in the body`() {
        val (pid, _) = newProject("notes-shop")
        val handOver = listOf(
            "Setup ladder: 3 of 12 rungs passed, 9 skipped. The commands that passed are in .keel/RUNNING.md.",
            "Knowledge: wrote architecture, domain, conventions (in .keel/knowledge/).",
            "Layout: keel — keel's files stay in .keel/, out of git.",
        ).joinToString("\n")
        send(listOf(
            ev("thread.started", pid, "t-init", data = mapOf("workflow" to "init", "title" to "")),
            ev("gate.waiting", pid, "t-init", step = "hand_over", data = mapOf("kind" to "gate", "title" to "hand over", "detail" to handOver,
                "flow" to "init", "step_name" to "hand over", "phase" to "setup",
                "summary" to listOf("✓ 3 setup checks passed · 9 skipped", "✓ Knowledge: architecture, domain, conventions"))),
            ev("thread.started", pid, "t-feat", data = mapOf("workflow" to "feature", "title" to "Add CSV export")),
            ev("gate.waiting", pid, "t-feat", step = "spec_gate", data = mapOf("kind" to "gate", "title" to "spec approval",
                "detail" to "AC-001 [API] Export orders as CSV\nAC-002 [WEB] A button downloads the file", "flow" to "feature", "phase" to "spec",
                "step_name" to "spec approval")),
            ev("gate.waiting", pid, "t-feat", step = "round_gate", data = mapOf("kind" to "clarify", "title" to "The explorer has 2 questions before the spec",
                "detail" to "1. Which columns?\n2. Which date format?", "flow" to "feature", "phase" to "spec", "step_name" to "your answers",
                "summary" to listOf("◆ Which columns?", "◆ Which date format?"))),
            ev("gate.waiting", pid, "t-feat", step = "ac_gate", data = mapOf("kind" to "gate", "title" to "AC gate · AC-002", "detail" to "long evidence",
                "flow" to "feature", "phase" to "gate", "step_name" to "AC gate", "ac" to "AC-002", "summary" to listOf("◆ AC-002: A button downloads the file"))),
            ev("agent.started", pid, "t-feat", "c-notes", "green", mapOf("agent" to "implementer", "provider" to "fake")),
            ev("agent.finished", pid, "t-feat", "c-notes", "green", mapOf("status" to "failed", "result" to "tests still red")),
            ev("thread.done", pid, "t-feat"),
            ev("thread.started", pid, "t-fix", data = mapOf("workflow" to "fix", "title" to "Fix the totals")),
            ev("thread.failed", pid, "t-fix", data = mapOf("error" to
                "RuntimeError: the build broke while compiling the order module because a dependency is missing\nTraceback (most recent call last): ...")),
        )).andExpect(status().isOk)

        val mine = get("/api/notifications?limit=50").json().filter { it["project_id"].asText() == pid }
        fun note(title: String) = mine.firstOrNull { it["title"].asText() == title }
            ?: throw AssertionError("no notification titled '$title' in ${mine.map { it["title"].asText() }}")
        fun lines(title: String) = note(title)["summary"].map { it.asText() }

        // the init hand-over: a title a person understands, two facts; the long text (paths, layout) stays in the body
        assertThat(lines("◆ notes-shop is set up — check it"))
            .containsExactly("✓ 3 setup checks passed · 9 skipped", "✓ Knowledge: architecture, domain, conventions")
        assertThat(note("◆ notes-shop is set up — check it")["body"].asText()).contains("Layout: keel", ".keel/RUNNING.md")
        // the spec gate: no summary from the engine, lines from the detail
        assertThat(lines("◆ notes-shop: approve the spec?")).containsExactly("AC-001 [API] Export orders as CSV", "AC-002 [WEB] A button downloads the file")
        assertThat(lines("◆ notes-shop: your answers are needed")).containsExactly("◆ Which columns?", "◆ Which date format?")
        assertThat(lines("◆ notes-shop: check AC-002")).containsExactly("◆ AC-002: A button downloads the file")
        assertThat(lines("✗ notes-shop: implementer failed")).containsExactly("✗ tests still red")
        assertThat(lines("✓ Add CSV export is done")).containsExactly("✓ All steps finished")

        val failed = mine.first { it["title"].asText().startsWith("✗ Fix the totals stopped: ") }["title"].asText()
        val reason = failed.removePrefix("✗ Fix the totals stopped: ")
        assertThat(reason).doesNotContain("RuntimeError").endsWith("…")
        // cut after a whole word: the words before "…" are the start of the error's own words
        assertThat("the build broke while compiling the order module because a dependency is missing ").startsWith(reason.removeSuffix("…") + " ")

        // never a step id or snake_case, never more than three lines
        assertThat(mine.map { it["title"].asText() }).noneMatch { Regex("[a-z]+_[a-z]+").containsMatchIn(it) }
        assertThat(mine).allMatch { it["summary"].size() <= 3 }
    }

    @Test
    fun `a failed step's notification is done once the next gate is decided (B3)`(@org.springframework.beans.factory.annotation.Autowired jdbc: org.springframework.jdbc.core.JdbcTemplate) {
        val (pid, _) = newProject("events-b3")
        val tid = "t-b3"
        send(listOf(
            ev("thread.started", pid, tid, data = mapOf("title" to "B3")),
            ev("code.started", pid, tid, "k1", "suite", mapOf("agent" to "keel")),
            ev("code.finished", pid, tid, "k1", "suite", mapOf("status" to "failed", "result" to "2 tests failed")),
            ev("agent.started", pid, tid, "a1", "green", mapOf("agent" to "implementer", "provider" to "fake")),
            ev("agent.finished", pid, tid, "a1", "green", mapOf("status" to "failed", "result" to "tests still red")),
        )).andExpect(status().isOk)
        // one from 0.18.54, made without its thread: its link is the job
        jdbc.update("INSERT INTO agent_calls(id, project_id, thread_id, agent, status, started_at) VALUES ('k0', ?, ?, 'keel', 'failed', '2026-10-24T10:00:00Z')", pid, tid)
        jdbc.update("INSERT INTO notifications(type, project_id, title, body, link, at, read, done) VALUES ('failed', ?, 'A code step failed', '', '/jobs/k0', '2026-10-24T10:00:00Z', 0, 0)", pid)
        val mine = { get("/api/notifications?limit=100").json().filter { it["project_id"].asText() == pid } }
        assertThat(mine().filter { it["type"].asText() == "failed" }).hasSize(3).allMatch { !it["read"].asBoolean() && !it["done"].asBoolean() }
        assertThat(mine().filter { it["link"].asText() == "/jobs/k1" }.single()["thread_id"].asText()).isEqualTo(tid)

        send(listOf(
            ev("gate.waiting", pid, tid, step = "g1", data = mapOf("kind" to "gate", "title" to "AC gate waits for you")),
            ev("gate.decided", pid, tid, step = "g1", data = mapOf("gate" to "ac", "decision" to "approve")),
        )).andExpect(status().isOk)
        assertThat(mine()).hasSize(4).allMatch { it["read"].asBoolean() && it["done"].asBoolean() }
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
