package keel.api.record

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.header
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** The Record plugin joins what keel keeps about a flow, for that flow and project only, and writes nothing. */
class RecordApiTest : ApiTest() {
    private fun ev(pid: String, tid: String, type: String, step: String?, at: String, data: Map<String, Any?>, call: String? = null) =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "call_id" to call, "at" to at, "data" to data)

    private fun seed(pid: String, tid: String = "t_$pid") {
        post("/internal/events", listOf(
            ev(pid, tid, "thread.started", null, "2026-10-01T10:00:00Z", mapOf("workflow" to "feature", "workflow_id" to "feature",
                "title" to "Export invoices", "request" to "Let people export invoices as a file", "via" to "mcp")),
            ev(pid, tid, "agent.started", "spec", "2026-10-01T10:00:01Z", mapOf("agent" to "explorer", "provider" to "fake", "model" to "fake", "phase" to "spec"), "c_$tid"),
            ev(pid, tid, "agent.finished", "spec", "2026-10-01T10:00:09Z", mapOf("status" to "done", "tokens_in" to 100, "tokens_out" to 20, "cost_usd" to 0.5, "result" to "wrote the spec"), "c_$tid"),
            ev(pid, tid, "gate.decided", "spec_gate", "2026-10-01T10:01:00Z", mapOf("gate" to "spec approval", "decision" to "approve", "why" to "looks right")),
            ev(pid, tid, "gate.decided", "ac_gate", "2026-10-01T10:02:00Z", mapOf("gate" to "AC gate", "decision" to "approve", "why" to "auto-approved (mode auto)", "ac" to "AC-1")),
            ev(pid, tid, "gate.decided", "g3", "2026-10-01T10:03:00Z", mapOf("gate" to "third", "decision" to "reject", "why" to "not yet", "by" to "supervisor")),
            ev(pid, tid, "gate.decided", "g4", "2026-10-01T10:04:00Z", mapOf("gate" to "fourth", "decision" to "approve", "why" to "not asked", "by" to "engine")),
            ev(pid, tid, "keel.applied", "commit", "2026-10-01T10:05:00Z", mapOf("kind" to "commit", "sha" to "abc123def456789", "subject" to "feat: export", "files" to listOf("src/a.kt"))),
            ev(pid, tid, "agent.step", "spec", "2026-10-01T10:00:02Z", mapOf("kind" to "text", "text" to "not part of the record"), "c_$tid"),
            ev(pid, tid, "thread.mode", null, "2026-10-01T10:06:00Z", mapOf("mode" to "auto", "by" to "the dashboard")),
            ev(pid, tid, "gate.decided", "g5", "2026-10-01T10:06:30Z", mapOf("gate" to "fifth", "decision" to "approve", "by" to "slack:Ann (U1)")),
            ev(pid, tid, "slack.call", "g5", "2026-10-01T10:06:31Z", mapOf("who" to "Ann (U1)", "what" to "answered approve", "ok" to true)),
            ev(pid, tid, "thread.done", null, "2026-10-01T10:07:00Z", mapOf("status" to "done")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
    }

    @Test
    fun `one flow's record says who asked, what ran, each gate with who answered, the commits and the cost`() {
        val (pid, _) = newProject("rec-all")
        seed(pid)
        val r = get("/api/projects/$pid/record/flows/t_$pid").andExpect(status().isOk).json()
        assertThat(r["asked"]["request"].asText()).contains("export invoices")
        assertThat(r["asked"]["via"].asText()).contains("MCP")
        assertThat(r["asked"]["by"].asText()).contains("one person")
        assertThat(r["ran"].map { it["agent"].asText() }).containsExactly("explorer")
        assertThat(r["ran"][0]["tokens_in"].asLong()).isEqualTo(100)
        assertThat(r["gates"].map { it["who"].asText() }).containsExactly("the person", "keel's run mode", "the Supervisor",
            "keel (not asked: the gate's condition did not hold)", "a person in Slack: Ann (U1)")
        assertThat(r["gates"][1]["ac"].asText()).isEqualTo("AC-1")
        assertThat(r["applied"].filter { it["kind"].asText() == "commit" }.map { it["ref"].asText() }).containsExactly("abc123def456789")
        assertThat(r["applied"][0]["files"].map { it.asText() }).containsExactly("src/a.kt")
        assertThat(r["cost"]["usd"].asDouble()).isEqualTo(0.5)
        assertThat(r["cost"]["tokens_out"].asLong()).isEqualTo(20)
        assertThat(r["other"].map { it["text"].asText() }).anyMatch { it.contains("run mode became auto") }.anyMatch { it.contains("Slack: Ann (U1) answered approve") }
        assertThat(r["limits"].map { it.asText() }).anyMatch { it.contains("not signed") }
    }

    @Test
    fun `0_21_0 a flow started from Slack says so and who, and the Supervisor's diagnosis and fix are listed`() {
        val (pid, _) = newProject("rec-slack")
        val tid = "t_rec_slack"
        post("/internal/events", listOf(
            ev(pid, tid, "thread.started", null, "2026-10-02T10:00:00Z", mapOf("workflow" to "feature", "title" to "Late filter", "via" to "slack", "by" to "slack:Ann (U1)")),
            ev(pid, tid, "supervisor.diagnosis", "g", "2026-10-02T10:01:00Z", mapOf("source" to "failed", "summary" to "The disk is full.")),
            ev(pid, tid, "supervisor.fixed", "g", "2026-10-02T10:02:00Z", mapOf("files" to listOf("src/a.py"), "lines" to 2, "why" to "a check was missing")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val r = get("/api/projects/$pid/record/flows/$tid").andExpect(status().isOk).json()
        assertThat(r["asked"]["via"].asText()).isEqualTo("Slack")
        assertThat(r["asked"]["by"].asText()).isEqualTo("a person in Slack: Ann (U1)")
        val other = r["other"].map { it["text"].asText() }
        assertThat(other).anyMatch { it.contains("diagnosis: The disk is full") }
        assertThat(other).anyMatch { it.contains("small fix in src/a.py") }
    }

    @Test
    fun `only via slack can name a person, as the engine sends it`() {
        val (pid, _) = newProject("rec-claim")
        post("/internal/events", listOf(ev(pid, "t_rec_claim", "thread.started", null, "2026-10-02T10:00:00Z", mapOf("workflow" to "feature", "title" to "Claim", "by" to "slack:Someone (U9)"))),
            mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/record/flows/t_rec_claim").andExpect(status().isOk).json()["asked"]["by"].asText()).contains("one person")
    }

    @Test
    fun `the export is a file, Markdown by default, JSON when asked`() {
        val (pid, _) = newProject("rec-export")
        seed(pid)
        val md = get("/api/projects/$pid/record/flows/t_$pid/export").andExpect(status().isOk)
            .andExpect(header().string("Content-Disposition", org.hamcrest.Matchers.containsString(".md")))
            .andReturn().response.getContentAsString(Charsets.UTF_8)
        assertThat(md).contains("# Record of a flow: Export invoices")
            .contains("> Let people export invoices as a file")
            .contains("**spec approval**: approve, by the person. looks right")
            .contains("`abc123def456`").contains("src/a.kt")
            .contains("## What this record is not").contains("not signed")
        val json = get("/api/projects/$pid/record/flows/t_$pid/export?format=json").andExpect(status().isOk)
            .andExpect(header().string("Content-Disposition", org.hamcrest.Matchers.containsString(".json"))).json()
        assertThat(json["thread_id"].asText()).isEqualTo("t_$pid")
    }

    @Test
    fun `a pipe or a new line in a value does not break the Markdown table`() {
        val (pid, _) = newProject("rec-pipe")
        post("/internal/events", listOf(
            ev(pid, "t_pipe$pid", "thread.started", null, "2026-10-01T10:00:00Z", mapOf("title" to "A\nB")),
            ev(pid, "t_pipe$pid", "agent.started", "a|b", "2026-10-01T10:00:01Z", mapOf("agent" to "explorer"), "cp$pid"),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val md = get("/api/projects/$pid/record/flows/t_pipe$pid/export").andReturn().response.getContentAsString(Charsets.UTF_8)
        assertThat(md).contains("a\\|b").contains("# Record of a flow: A B")
        assertThat(md).contains("keel kept no request text")
    }

    @Test
    fun `the list has the project's flows, newest first, with counts`() {
        val (pid, _) = newProject("rec-list")
        seed(pid, "t_old$pid")
        post("/internal/events", listOf(ev(pid, "t_new$pid", "thread.started", null, "2026-10-02T10:00:00Z", mapOf("title" to "Later"))),
            mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val rows = get("/api/projects/$pid/record/flows").andExpect(status().isOk).json()
        assertThat(rows.map { it["thread_id"].asText() }).containsExactly("t_new$pid", "t_old$pid")
        val old = rows.first { it["thread_id"].asText() == "t_old$pid" }
        assertThat(old["gates"].asInt()).isEqualTo(5)
        assertThat(old["commits"].asInt()).isEqualTo(1)
    }

    @Test
    fun `another project's flow is a 404, and a project that does not exist too`() {
        val (pid, _) = newProject("rec-mine")
        val (other, _) = newProject("rec-other")
        seed(other, "t_theirs$other")
        get("/api/projects/$pid/record/flows/t_theirs$other").andExpect(status().isNotFound)
        get("/api/projects/$pid/record/flows/t_theirs$other/export").andExpect(status().isNotFound)
        assertThat(get("/api/projects/$pid/record/flows").json().size()).isEqualTo(0)
        get("/api/projects/no-such/record/flows").andExpect(status().isNotFound)
    }

    @Test
    fun `reading a record changes nothing`() {
        val (pid, _) = newProject("rec-read")
        seed(pid)
        val before = get("/api/projects/$pid/runs").json().toString()
        repeat(2) { get("/api/projects/$pid/record/flows/t_$pid").andExpect(status().isOk) }
        assertThat(get("/api/projects/$pid/runs").json().toString()).isEqualTo(before)
    }
}
