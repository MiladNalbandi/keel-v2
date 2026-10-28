package keel.api

import keel.api.jobs.StepRunService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * keel 0.18.11: what each step of one flow really did, for the Flow page's blocks (GET .../flows/{tid}/step-runs): its
 * calls added up per step and criterion, with tries, working time, tokens, cost, the model and effort that ran, and
 * the first line of the last answer. Calls of other flows and other projects never count.
 */
class StepRunsApiTest : ApiTest() {

    private fun ev(pid: String, tid: String, type: String, call: String, step: String, at: String, data: Map<String, Any?>) =
        // call ids are unique in the whole test database (other tests use c1 too)
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "call_id" to "$pid-$call", "at" to at, "data" to data)

    private fun started(agent: String, model: String, effort: String?) =
        mapOf("agent" to agent, "provider" to "claude", "model" to model, "mode" to "subscription", "effort" to effort)

    private fun finished(tin: Int, tout: Int, cost: Double, result: String, st: String = "done") =
        mapOf("status" to st, "tokens_in" to tin, "tokens_out" to tout, "tokens_cached" to 100, "cost_usd" to cost, "result" to result)

    @Test
    fun `each step adds up its calls, counts its tries and keeps the last answer's first line`() {
        val (pid, _) = newProject("step-runs")
        post("/internal/events", listOf(
            ev(pid, "t1", "agent.started", "c1", "spec", "2026-10-17T10:00:00Z", started("explorer", "sonnet", "high")),
            ev(pid, "t1", "agent.finished", "c1", "spec", "2026-10-17T10:02:10Z", finished(20_000, 11_000, 0.18, "## Spec\n\n**Spec with 6 criteria**, 2 questions\nmore")),
            // green ran twice for AC-1 (a send back between)
            ev(pid, "t1", "agent.started", "g1", "green", "2026-10-17T10:10:00Z", started("implementer", "haiku", null) + ("ac" to "AC-1")),
            ev(pid, "t1", "agent.finished", "g1", "green", "2026-10-17T10:11:00Z", finished(1_000, 500, 0.01, "first try")),
            ev(pid, "t1", "agent.started", "g2", "green", "2026-10-17T10:20:00Z", started("implementer", "sonnet", "xhigh") + ("ac" to "AC-1")),
            ev(pid, "t1", "agent.finished", "g2", "green", "2026-10-17T10:23:00Z", finished(2_000, 1_000, 0.05, "app/main.py: POST /health returns 405")),
            // two lanes at the same time are one try
            ev(pid, "t1", "agent.started", "r1", "review", "2026-10-17T10:30:00Z", started("reviewer", "sonnet", null)),
            ev(pid, "t1", "agent.started", "r2", "review", "2026-10-17T10:30:05Z", started("security-auditor", "sonnet", null)),
            ev(pid, "t1", "agent.finished", "r1", "review", "2026-10-17T10:31:00Z", finished(100, 50, 0.0, "fine")),
            ev(pid, "t1", "agent.finished", "r2", "review", "2026-10-17T10:32:00Z", finished(100, 50, 0.0, "no findings")),
            // another flow of this project
            ev(pid, "t2", "agent.started", "x1", "spec", "2026-10-17T11:00:00Z", started("explorer", "opus", null)),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

        val runs = get("/api/projects/$pid/flows/t1/step-runs").andExpect(status().isOk).json().toList()
        assertThat(runs.map { it["step"].asText() }).containsExactly("spec", "green", "review")

        val spec = runs[0]
        assertThat(spec["tries"].asInt()).isEqualTo(1)
        assertThat(spec["agent"].asText()).isEqualTo("explorer")
        assertThat(spec["model"].asText()).isEqualTo("sonnet")
        assertThat(spec["effort"].asText()).isEqualTo("high")
        assertThat(spec["ms"].asLong()).isEqualTo(130_000)
        assertThat(spec["tokens_in"].asLong()).isEqualTo(20_000)
        assertThat(spec["tokens_out"].asLong()).isEqualTo(11_000)
        assertThat(spec["cost_usd"].asDouble()).isEqualTo(0.18)
        assertThat(spec["result"].asText()).isEqualTo("Spec")

        val green = runs[1]
        assertThat(green["ac"].asText()).isEqualTo("AC-1")
        assertThat(green["tries"].asInt()).isEqualTo(2)
        assertThat(green["model"].asText()).isEqualTo("sonnet")                 // the last try's
        assertThat(green["effort"].asText()).isEqualTo("xhigh")
        assertThat(green["ms"].asLong()).isEqualTo(240_000)                      // 1 min + 3 min, not the 13 min between
        assertThat(green["tokens_in"].asLong()).isEqualTo(3_000)
        assertThat(green["result"].asText()).isEqualTo("app/main.py: POST /health returns 405")
        assertThat(green["call_id"].asText()).isEqualTo("$pid-g2")

        assertThat(runs[2]["tries"].asInt()).isEqualTo(1)                       // parallel lanes
        assertThat(get("/api/projects/$pid/flows/t2/step-runs").json().single()["status"].asText()).isEqualTo("running")
        assertThat(get("/api/projects/other/flows/t1/step-runs").json().size()).isEqualTo(0)
    }

    /** 0.18.64 keel_flow_diff: what a step's last run changed (a field report: a retry after a reject lost the note, and
     *  nothing showed that the spec had been written again from scratch). */
    @Test
    fun `a step that ran again shows what its answer or its document changed`() {
        val (pid, _) = newProject("step-diff")
        post("/internal/events", listOf(
            ev(pid, "t1", "agent.started", "a1", "assemble", "2026-10-17T10:00:00Z", started("explorer", "sonnet", null)),
            ev(pid, "t1", "agent.finished", "a1", "assemble", "2026-10-17T10:01:00Z", finished(10, 10, 0.0, "## Spec\nAC-1 save\nAC-2 old rule\n")),
            ev(pid, "t1", "agent.started", "a2", "assemble", "2026-10-17T10:05:00Z", started("explorer", "sonnet", null)),
            ev(pid, "t1", "agent.finished", "a2", "assemble", "2026-10-17T10:06:00Z", finished(10, 10, 0.0, "## Spec\nAC-1 save\nAC-2 the note's rule\n")),
            ev(pid, "t1", "agent.started", "r1", "read", "2026-10-17T09:50:00Z", started("explorer", "sonnet", null)),
            ev(pid, "t1", "agent.finished", "r1", "read", "2026-10-17T09:51:00Z", finished(10, 10, 0.0, "read it")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

        val listed = get("/api/projects/$pid/flows/t1/step-diff").andExpect(status().isOk).json()
        assertThat(listed["kind"].asText()).isEqualTo("list")
        assertThat(listed["steps"].map { it["step"].asText() to it["runs"].asInt() }).containsExactly("assemble" to 2)

        val d = get("/api/projects/$pid/flows/t1/step-diff?step=assemble").andExpect(status().isOk).json()
        assertThat(d["kind"].asText()).isEqualTo("answer")
        assertThat(d["before"]["id"].asText()).isEqualTo("$pid-a1")
        assertThat(d["after"]["id"].asText()).isEqualTo("$pid-a2")
        assertThat(d["diff"].asText()).contains("-AC-2 old rule\n+AC-2 the note's rule\n").contains(" AC-1 save\n")

        val once = get("/api/projects/$pid/flows/t1/step-diff?step=read").andExpect(status().isOk).json()
        assertThat(once["kind"].asText()).isEqualTo("none")
        assertThat(once["why"].asText()).contains("ran once")

        // the document the step writes: the versions keel kept of it in this flow
        val path = ".keel/specs/clarify/2026-10-17-webhook.md"
        fun version(text: String, at: String) = mapOf("type" to "doc.version", "thread_id" to "t1", "project_id" to pid, "step" to "write_doc",
            "at" to at, "data" to mapOf("path" to path, "kind" to "spec", "sha256" to keel.api.knowledge.DocHistoryService.sha256(text.toByteArray()),
                "content" to text, "size" to text.length, "by_kind" to "agent", "by_name" to "explorer", "phase" to "spec"))
        post("/internal/events", listOf(version("# Webhook\nAC-1 old\n", "2026-10-17T10:02:00Z"), version("# Webhook\nAC-1 new\n", "2026-10-17T10:07:00Z")),
            mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val doc = get("/api/projects/$pid/flows/t1/step-diff?step=write_doc").andExpect(status().isOk).json()
        assertThat(doc["kind"].asText()).isEqualTo("document")
        assertThat(doc["path"].asText()).isEqualTo(path)
        assertThat(doc["before"]["version"].asInt()).isEqualTo(1)
        assertThat(doc["after"]["version"].asInt()).isEqualTo(2)
        assertThat(doc["diff"].asText()).contains("-AC-1 old\n+AC-1 new\n")
    }

    @Test
    fun `an agent call keeps the prompt it got, and no screen is sent it`() {
        val (pid, _) = newProject("prompt-kept")
        post("/internal/events", listOf(
            ev(pid, "t1", "agent.started", "p1", "assemble", "2026-10-17T10:00:00Z", started("explorer", "sonnet", null)),
            ev(pid, "t1", "agent.prompt", "p1", "assemble", "2026-10-17T10:00:01Z",
                mapOf("agent" to "explorer", "system" to "You are the explorer.", "prompt" to "Assemble the spec. Notes: keep the rule.",
                    "system_cut" to false, "prompt_cut" to true)),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val p = get("/api/jobs/$pid-p1/prompt").andExpect(status().isOk).json()
        assertThat(p["agent"].asText()).isEqualTo("explorer")
        assertThat(p["step"].asText()).isEqualTo("assemble")
        assertThat(p["system"].asText()).isEqualTo("You are the explorer.")
        assertThat(p["prompt"].asText()).isEqualTo("Assemble the spec. Notes: keep the rule.")
        assertThat(p["prompt_cut"].asBoolean()).isTrue()
        get("/api/jobs/$pid-nope/prompt").andExpect(status().isNotFound)
        assertThat(keel.api.events.RECORD_ONLY).contains("agent.prompt")
    }

    @Test
    fun `the result line skips markdown marks and empty lines and is at most 100 characters`() {
        assertThat(StepRunService.firstLine("\n\n**PASS** — the test proves AC-002.\n- RED 4be12d9")).isEqualTo("PASS — the test proves AC-002.")
        assertThat(StepRunService.firstLine("---\n> `quoted` text")).isEqualTo("quoted text")
        assertThat(StepRunService.firstLine("x".repeat(150))).hasSize(100).endsWith("…")
        assertThat(StepRunService.firstLine("   ")).isNull()
        assertThat(StepRunService.firstLine(null)).isNull()
    }
}
