package keel.api.goals

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID

/** A goal: a plan becomes real only with a person's approval, a person starts each step, and its limits are kept. */
class GoalsApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private val plan = """The plan.

```json
[{"title": "Add the export button", "request": "Put a button at src/a.kt:3", "workflow": "small"},
 {"title": "Write the file", "request": "Write it", "workflow": "made-up"}]
```
"""

    @BeforeEach
    fun table() {
        Files.readString(Path.of("../plugins/goals/migrations/V1__goals.sql")).replace(Regex("--.*"), "").split(";")
            .map { it.trim() }.filter { it.isNotEmpty() }.forEach { jdbc.execute(it) }
        engine.extraTemplates += listOf("goals-plan", "small", "change").map { id ->
            mapOf("id" to id, "name" to id, "keel_rules" to false, "version" to 1, "own_branch" to (id != "goals-plan"), "yaml" to "",
                "steps" to listOf(mapOf("id" to "a", "kind" to "agent", "name" to "a", "agent" to "explorer", "phase" to "review")))
        }
    }

    @AfterEach
    fun tidy() {
        engine.extraTemplates.removeIf { it["id"] in setOf("goals-plan", "small", "change") }
    }

    private fun goal(pid: String, vararg more: Pair<String, Any?>) =
        post("/api/projects/$pid/goals", mapOf("title" to "Export invoices", "done_when" to "People can export") + more).andExpect(status().isOk).json()

    private fun event(pid: String, tid: String, type: String, step: String?, data: Map<String, Any?>) =
        post("/internal/events", listOf(mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "at" to java.time.Instant.now().toString(), "data" to data)),
            mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    /** A goal whose plan a person approved; returns its id. */
    private fun planned(pid: String, vararg more: Pair<String, Any?>): String {
        val id = goal(pid, *more)["id"].asText()
        val tid = "plan_" + UUID.randomUUID().toString().take(8)
        jdbc.update("UPDATE goals_goal SET status = 'planning', plan_thread = ? WHERE id = ?", tid, id)
        event(pid, tid, "gate.waiting", "plan_gate", mapOf("detail" to plan))
        event(pid, tid, "gate.decided", "plan_gate", mapOf("decision" to "approve"))
        return id
    }

    private fun spend(pid: String, tid: String, tokens: Int) {
        event(pid, tid, "thread.started", null, mapOf("workflow_id" to "small", "title" to "x"))
        event(pid, tid, "agent.started", "a", mapOf("agent" to "explorer"))
        post("/internal/events", listOf(mapOf("type" to "agent.finished", "thread_id" to tid, "project_id" to pid, "step" to "a", "call_id" to "c_$tid", "at" to java.time.Instant.now().toString(),
            "data" to mapOf("status" to "done", "tokens_in" to tokens, "tokens_out" to 0))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
    }

    @Test
    fun `a goal needs a title and what done means, and its limits must make sense`() {
        val (pid, _) = newProject("goal-new")
        post("/api/projects/$pid/goals", mapOf("title" to "x", "done_when" to " ")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/goals", mapOf("title" to " ", "done_when" to "y")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/goals", mapOf("title" to "x", "done_when" to "y", "cap_tokens" to 0)).andExpect(status().isBadRequest)
        post("/api/projects/$pid/goals", mapOf("title" to "x", "done_when" to "y", "max_flows" to -1)).andExpect(status().isBadRequest)
        val g = goal(pid, "cap_tokens" to 5000, "max_flows" to 3)
        assertThat(g["status"].asText()).isEqualTo("new")
        assertThat(g["cap_tokens"].asLong()).isEqualTo(5000)
    }

    @Test
    fun `plan starts the planning flow with the goal's words, on no branch, and nothing is real yet`() {
        val (pid, root) = newProject("goal-plan")
        val id = goal(pid, "why" to "Customers ask", "cap_tokens" to 9000)["id"].asText()
        val before = git(root, "branch", "--show-current")
        val g = post("/api/projects/$pid/goals/$id/plan", mapOf("allow_fake" to true)).andExpect(status().isOk).json()
        assertThat(g["status"].asText()).isEqualTo("planning")
        val sent = engine.lastBody("/threads")!!["request"].asText()
        assertThat(sent).contains("Export invoices").contains("People can export").contains("Customers ask").contains("9000 tokens")
        assertThat(git(root, "branch", "--show-current")).isEqualTo(before)
        assertThat(get("/api/projects/$pid/goals/$id").json()["steps"].size()).isEqualTo(0)
    }

    @Test
    fun `the plan waits at the gate, and only an approval makes its steps real`() {
        val (pid, _) = newProject("goal-gate")
        val id = goal(pid)["id"].asText()
        jdbc.update("UPDATE goals_goal SET status = 'planning', plan_thread = 'pg1' WHERE id = ?", id)
        event(pid, "pg1", "gate.waiting", "plan_gate", mapOf("detail" to plan))
        var v = get("/api/projects/$pid/goals/$id").json()
        assertThat(v["steps"].size()).isEqualTo(0)
        assertThat(v["draft"].map { it["title"].asText() }).containsExactly("Add the export button", "Write the file")
        event(pid, "pg1", "gate.decided", "plan_gate", mapOf("decision" to "reject", "why" to "too big"))
        v = get("/api/projects/$pid/goals/$id").json()
        assertThat(v["steps"].size()).isEqualTo(0)
        assertThat(v["goal"]["status"].asText()).isEqualTo("planning")
        event(pid, "pg1", "gate.waiting", "plan_gate", mapOf("detail" to plan))
        event(pid, "pg1", "gate.decided", "plan_gate", mapOf("decision" to "approve"))
        v = get("/api/projects/$pid/goals/$id").json()
        assertThat(v["goal"]["status"].asText()).isEqualTo("active")
        assertThat(v["steps"].map { it["workflow"].asText() }).containsExactly("small", "change")   // a made-up flow becomes change
        assertThat(v["steps"].map { it["state"].asText() }).containsExactly("todo", "todo")
        assertThat(v["limits"].map { it.asText() }).anyMatch { it.contains("no named owner") }
    }

    @Test
    fun `an approved plan starts nothing, the next step waits in the Inbox and a person starts it`() {
        val (pid, _) = newProject("goal-start")
        val id = planned(pid)
        val calls = engine.calls.count { it.path == "/threads" && it.method == "POST" }
        assertThat(engine.calls.count { it.path == "/threads" && it.method == "POST" }).isEqualTo(calls)
        val inbox = get("/api/inbox?project=$pid").json()["items"].filter { it["kind"].asText() == "goal-step" }
        assertThat(inbox).hasSize(1)
        assertThat(inbox[0]["title"].asText()).contains("Add the export button")
        val sid = get("/api/projects/$pid/goals/$id").json()["steps"][0]["id"].asText()
        val v = post("/api/projects/$pid/goals/$id/steps/$sid/start", mapOf("allow_fake" to true)).andExpect(status().isOk).json()
        assertThat(engine.lastBody("/threads")!!["request"].asText()).contains("Put a button at src/a.kt:3").contains("step 1 of the goal")
        assertThat(v["steps"][0]["thread_id"].asText()).isNotBlank()
        assertThat(v["progress"]["flows"].asInt()).isEqualTo(1)
        post("/api/projects/$pid/goals/$id/steps/$sid/start", mapOf("allow_fake" to true)).andExpect(status().isConflict)
    }

    @Test
    fun `the flow limit stops the next start`() {
        val (pid, _) = newProject("goal-flows")
        val id = planned(pid, "max_flows" to 1)
        val steps = get("/api/projects/$pid/goals/$id").json()["steps"]
        jdbc.update("UPDATE goals_step SET thread_id = 'done_flow' WHERE id = ?", steps[0]["id"].asText())
        event(pid, "done_flow", "thread.started", null, mapOf("title" to "x"))
        event(pid, "done_flow", "thread.done", null, mapOf("status" to "done"))
        val res = post("/api/projects/$pid/goals/$id/steps/${steps[1]["id"].asText()}/start", mapOf("allow_fake" to true)).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("limit of 1 flows")
        assertThat(get("/api/inbox?project=$pid").json()["items"].filter { it["kind"].asText() == "goal-step" }).isEmpty()
        post("/api/projects/$pid/goals/$id/limits", mapOf("max_flows" to 5)).andExpect(status().isOk)
        post("/api/projects/$pid/goals/$id/steps/${steps[1]["id"].asText()}/start", mapOf("allow_fake" to true)).andExpect(status().isOk)
    }

    @Test
    fun `the token limit stops the next start, the plan's tokens count, and a step's flow gets only what is left`() {
        val (pid, _) = newProject("goal-tokens")
        val id = planned(pid, "cap_tokens" to 1000)
        val g = get("/api/projects/$pid/goals/$id").json()
        val planTid = jdbc.queryForObject("SELECT plan_thread FROM goals_goal WHERE id = ?", String::class.java, id)!!
        spend(pid, planTid, 400)
        assertThat(get("/api/projects/$pid/goals/$id").json()["progress"]["tokens"].asLong()).isEqualTo(400)
        val first = g["steps"][0]["id"].asText()
        post("/api/projects/$pid/goals/$id/steps/$first/start", mapOf("allow_fake" to true)).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["settings"]["cap_tokens"].asInt()).isEqualTo(600)
        val tid = get("/api/projects/$pid/goals/$id").json()["steps"][0]["thread_id"].asText()
        spend(pid, tid, 700)
        event(pid, tid, "thread.done", null, mapOf("status" to "done"))
        val second = g["steps"][1]["id"].asText()
        val res = post("/api/projects/$pid/goals/$id/steps/$second/start", mapOf("allow_fake" to true)).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("limit of 1000 tokens")
    }

    @Test
    fun `a step that runs blocks the next, a skipped one is left out, and the progress counts`() {
        val (pid, _) = newProject("goal-prog")
        val id = planned(pid)
        val s = get("/api/projects/$pid/goals/$id").json()["steps"]
        val a = s[0]["id"].asText()
        val b = s[1]["id"].asText()
        jdbc.update("UPDATE goals_step SET thread_id = 'running_flow' WHERE id = ?", a)
        event(pid, "running_flow", "thread.started", null, mapOf("title" to "x"))
        val res = post("/api/projects/$pid/goals/$id/steps/$b/start", mapOf("allow_fake" to true)).andExpect(status().isConflict).json()
        assertThat(res["error"].asText()).contains("runs or waits")
        event(pid, "running_flow", "thread.done", null, mapOf("status" to "done"))
        val v = post("/api/projects/$pid/goals/$id/steps/$b/skip").andExpect(status().isOk).json()
        assertThat(v["progress"]["done"].asInt()).isEqualTo(1)
        assertThat(v["progress"]["total"].asInt()).isEqualTo(1)
        post("/api/projects/$pid/goals/$id/steps/$a/skip").andExpect(status().isConflict)
    }

    @Test
    fun `a person says when it is done or stopped, then nothing starts`() {
        val (pid, _) = newProject("goal-done")
        val id = planned(pid)
        val sid = get("/api/projects/$pid/goals/$id").json()["steps"][0]["id"].asText()
        post("/api/projects/$pid/goals/$id/status/nonsense").andExpect(status().isBadRequest)
        assertThat(post("/api/projects/$pid/goals/$id/status/stopped").json()["status"].asText()).isEqualTo("stopped")
        post("/api/projects/$pid/goals/$id/steps/$sid/start", mapOf("allow_fake" to true)).andExpect(status().isConflict)
        post("/api/projects/$pid/goals/$id/plan", mapOf("allow_fake" to true)).andExpect(status().isConflict)
    }

    @Test
    fun `a planning flow that fails leaves the goal ready to plan again, and another project sees nothing`() {
        val (pid, _) = newProject("goal-fail")
        val (other, _) = newProject("goal-other")
        val id = goal(pid)["id"].asText()
        jdbc.update("UPDATE goals_goal SET status = 'planning', plan_thread = 'pf1' WHERE id = ?", id)
        event(pid, "pf1", "thread.failed", null, mapOf("error" to "down"))
        assertThat(get("/api/projects/$pid/goals/$id").json()["goal"]["status"].asText()).isEqualTo("new")
        jdbc.update("UPDATE goals_goal SET status = 'planning', plan_thread = 'pf2' WHERE id = ?", id)
        event(pid, "pf2", "thread.done", null, mapOf("status" to "stopped"))
        assertThat(get("/api/projects/$pid/goals/$id").json()["goal"]["status"].asText()).isEqualTo("new")
        get("/api/projects/$other/goals/$id").andExpect(status().isNotFound)
        post("/api/projects/$other/goals/$id/plan").andExpect(status().isNotFound)
        assertThat(get("/api/projects/$other/goals").json().size()).isEqualTo(0)
    }

    @Autowired lateinit var tokens: keel.api.mcp.McpTokens

    @Test
    fun `an MCP write token may do what the Goals tools do, a read token none of it, and limits stay admin`() {
        val (pid, _) = newProject("goal-mcp")
        val via = mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "t")
        val read = via + mapOf("X-Keel-Mcp-Token" to tokens.tokenFor("read"))
        val write = via + mapOf("X-Keel-Mcp-Token" to tokens.tokenFor("write"))
        val body = mapOf("title" to "Export invoices", "done_when" to "People can export")
        post("/api/projects/$pid/goals", body, read).andExpect(status().isForbidden)
        val id = post("/api/projects/$pid/goals", body, write).andExpect(status().isOk).json()["id"].asText()
        post("/api/projects/$pid/goals/$id/limits", mapOf("max_flows" to 2), write).andExpect(status().isForbidden)
        post("/api/projects/$pid/goals/$id/plan", mapOf("allow_fake" to true), read).andExpect(status().isForbidden)
        post("/api/projects/$pid/goals/$id/plan", mapOf("allow_fake" to true), write).andExpect(status().isOk)
        val ready = planned(pid)
        val step = get("/api/projects/$pid/goals/$ready").json()["steps"][1]["id"].asText()
        post("/api/projects/$pid/goals/$ready/steps/$step/skip", null, write).andExpect(status().isOk)
        post("/api/projects/$pid/goals/$ready/status/done", null, read).andExpect(status().isForbidden)
        post("/api/projects/$pid/goals/$ready/status/done", null, write).andExpect(status().isOk)
    }
}
