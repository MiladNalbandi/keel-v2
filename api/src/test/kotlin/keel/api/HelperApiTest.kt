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

    private fun waitingFlow(pid: String, tid: String) {
        engine.nextThreadIds.add(tid)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Discount codes", "allow_fake" to true)).andExpect(status().isOk)
        engine.overrides[tid] = mapOf("status" to "waiting", "title" to "Discount codes", "ac" to "AC-001", "phase" to "gate",
            "waiting" to mapOf("step" to "ac_gate", "kind" to "gate", "title" to "AC gate", "detail" to "tests 1/2 pass", "options" to listOf("approve", "reject")))
    }

    @Test
    fun `fix mode needs a flow that waits, and sends its phase, criterion, unlocks and run mode`() {
        val (pid, _) = newProject("helper-fix")
        post("/api/projects/$pid/helper/sessions", mapOf("mode" to "fix")).andExpect(status().isConflict)
        waitingFlow(pid, "t-fix-1")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("mode" to "fix")).andExpect(status().isOk).json()["id"].asText()
        assertThat(engine.lastBody("/helper/sessions")!!["thread_id"].asText()).isEqualTo("t-fix-1")
        assertThat(engine.lastBody("/helper/sessions")!!["mode"].asText()).isEqualTo("fix")
        // the AC gate's own phase lets only notes change: the engine gets the phases of the work before it, nearest first
        assertThat(engine.lastBody("/helper/sessions")!!["flow"]["phases_before"].map { it.asText() }.take(3)).containsExactly("green", "red", "spec")

        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "Make AC-001 pass")).andExpect(status().isOk)
        val flow = engine.lastBody("/helper/sessions/$sid/turn")!!["flow"]
        assertThat(flow["thread_id"].asText()).isEqualTo("t-fix-1")
        assertThat(flow["phase"].asText()).isEqualTo("gate")
        assertThat(flow["phases_before"][0].asText()).isEqualTo("green")
        assertThat(flow["ac"]["id"].asText()).isEqualTo("AC-001")
        assertThat(flow["workflow"].asText()).isEqualTo("feature")
        assertThat(flow["run_mode"].asText()).isEqualTo("manual")
        assertThat(flow["unlocks"].isArray).isTrue()

        assertThat(get("/api/projects/$pid/helper/sessions/$sid/changes").json()[0]["path"].asText()).isEqualTo("src/a.kt")
        post("/api/projects/$pid/helper/sessions/$sid/undo", mapOf("path" to "src/a.kt")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid/undo")!!["path"].asText()).isEqualTo("src/a.kt")
        val done = post("/api/projects/$pid/helper/sessions/$sid/done", mapOf("message" to " Limit is 100 ")).andExpect(status().isOk).json()
        assertThat(engine.lastBody("/helper/sessions/$sid/done")!!["message"].asText()).isEqualTo("Limit is 100")
        assertThat(done["sha"].asText()).isEqualTo("abc1234")
        assertThat(engine.lastBody("/helper/sessions/$sid/done")!!["flow"]["phases_before"][0].asText()).isEqualTo("green")

        // the flow moves on: the Fix chat can no longer edit or commit
        engine.overrides["t-fix-1"] = mapOf("status" to "running")
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "more")).andExpect(status().isConflict)
        post("/api/projects/$pid/helper/sessions/$sid/done").andExpect(status().isConflict)
        // an Ask chat cannot press Done
        val ask = post("/api/projects/$pid/helper/sessions").json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$ask/done").andExpect(status().isBadRequest)
    }

    @Test
    fun `a waiting command is a card in the Inbox, and the answer goes to the engine`() {
        val (pid, _) = newProject("helper-ask")
        engine.helperQuestions += mapOf("id" to "p_1", "session" to "h_x", "project" to pid, "kind" to "command",
            "command" to "npm install left-pad", "path" to "", "title" to "Fix the totals", "at" to "2026-10-06T10:00:00Z")
        val inbox = get("/api/inbox?project=$pid").json()
        val item = inbox["items"].first { it["kind"].asText() == "permission" }
        assertThat(item["detail"].asText()).isEqualTo("npm install left-pad")
        assertThat(item["permission"]["id"].asText()).isEqualTo("p_1")
        assertThat(item["flow"].asText()).isEqualTo("Fix the totals")
        assertThat(get("/api/inbox/count").json()["projects"][pid].asInt()).isEqualTo(1)

        post("/api/projects/$pid/helper/permissions/p_1", mapOf("decision" to "maybe")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/helper/permissions/p_9", mapOf("decision" to "once")).andExpect(status().isNotFound)
        post("/api/projects/$pid/helper/permissions/p_1", mapOf("decision" to "deny", "why" to "not now")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/permissions/p_1")!!["why"].asText()).isEqualTo("not now")
        assertThat(get("/api/inbox?project=$pid").json()["items"].none { it["kind"].asText() == "permission" }).isTrue()
    }

    @Test
    fun `a permission question notifies the person, and its answer clears the notification`() {
        val (pid, _) = newProject("helper-notify")
        fun ev(type: String, data: Map<String, Any?>) = mapOf("type" to type, "thread_id" to "h_n", "project_id" to pid, "step" to "helper",
            "at" to java.time.Instant.now().toString(), "data" to data)
        post("/internal/events", listOf(ev("helper.permission", mapOf("id" to "p_2", "command" to "rm -rf build"))), mapOf("X-Keel-Token" to TOKEN))
            .andExpect(status().isOk)
        val note = get("/api/notifications").json().first { it["title"].asText() == "The Helper asks to run a command" }
        assertThat(note["body"].asText()).isEqualTo("rm -rf build")
        post("/internal/events", listOf(ev("helper.permission.answered", mapOf("id" to "p_2", "decision" to "deny"))), mapOf("X-Keel-Token" to TOKEN))
        assertThat(get("/api/notifications").json().first { it["title"].asText() == "The Helper asks to run a command" }["done"].asBoolean()).isTrue()
    }

    @Test
    fun `a side session keeps on its own branch and is handed over as a task or as a flow on that branch`() {
        val (pid, root) = newProject("helper-side")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("mode" to "side")).andExpect(status().isOk).json()["id"].asText()
        assertThat(engine.lastBody("/helper/sessions")!!["mode"].asText()).isEqualTo("side")
        // Keep needs no waiting flow: the engine gets no flow and commits on the side branch
        post("/api/projects/$pid/helper/sessions/$sid/done", mapOf("message" to "price helper")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid/done")!!["flow"].size()).isEqualTo(0)

        val sha = git(root, "rev-parse", "HEAD").trim()
        git(root, "branch", "keel/helper/abc")
        engine.helperHandover = engine.helperHandover + mapOf("base" to sha,
            "commits" to listOf(mapOf("sha" to "1234567abc", "subject" to "fix(helper): price helper")))
        val task = post("/api/projects/$pid/helper/sessions/$sid/task", mapOf("type" to "story")).andExpect(status().isOk).json()
        assertThat(task["title"].asText()).isEqualTo("Add a price helper")
        assertThat(task["description"].asText()).contains("branch `keel/helper/abc`").contains("1234567 fix(helper): price helper")
            .contains("- Add a price helper").contains("Added it.")

        // a flow: not while something is not kept, not with a dirty folder; then the folder checks the branch out
        engine.helperHandover = engine.helperHandover + mapOf("uncommitted" to listOf("src/a.js"))
        post("/api/projects/$pid/helper/sessions/$sid/flow", mapOf("workflow_id" to "feature")).andExpect(status().isConflict)
        engine.helperHandover = engine.helperHandover + mapOf("uncommitted" to emptyList<Any>())
        java.nio.file.Files.writeString(root.resolve("mine.txt"), "my own work")
        post("/api/projects/$pid/helper/sessions/$sid/flow", mapOf("workflow_id" to "feature")).andExpect(status().isConflict)
        java.nio.file.Files.delete(root.resolve("mine.txt"))
        engine.nextThreadIds.add("t-side-1")
        post("/api/projects/$pid/helper/sessions/$sid/flow", mapOf("workflow_id" to "feature")).andExpect(status().isOk)
        assertThat(engine.calls.any { it.path == "/helper/sessions/$sid/release" }).isTrue()
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("keel/helper/abc")
        assertThat(engine.lastBody("/threads")!!["request"].asText()).contains("branch `keel/helper/abc`")
        // and not twice: a flow now runs in the folder
        post("/api/projects/$pid/helper/sessions/$sid/flow", mapOf("workflow_id" to "feature")).andExpect(status().isConflict)
    }
}
