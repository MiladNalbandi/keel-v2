package keel.api.helper

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * v0.6.0 KeelBot: the api's sessions proxy the engine, a turn carries what the engine needs (the project's workflows and
 * flows too, v0.9.0), its events are agent calls, and its old permission routes answer through core approvals. Moved
 * with the plugin (plugins/keelbot); it runs with keel's test support (ApiTest, StubEngine).
 */
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
    fun `compact and export reach the engine, for this project's chats only`() {
        // P1-2 / P1-3: keel TUI's /compact and /export went to routes the api did not have (404)
        val (pid, _) = newProject("helper-compact")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("title" to "Scores")).json()["id"].asText()
        val c = post("/api/projects/$pid/helper/sessions/$sid/compact").andExpect(status().isOk).json()
        assertThat(c["before"].asInt()).isEqualTo(48000)
        assertThat(c["after"].asInt()).isEqualTo(6000)
        val x = post("/api/projects/$pid/helper/sessions/$sid/export").andExpect(status().isOk).json()
        assertThat(x["path"].asText()).isEqualTo(".keel/chats/$sid.md")
        val (other, _) = newProject("helper-compact-other")
        post("/api/projects/$other/helper/sessions/$sid/compact").andExpect(status().isNotFound)
        post("/api/projects/$other/helper/sessions/$sid/export").andExpect(status().isNotFound)
    }

    @Test
    fun `undo-turn and redo-turn reach the engine, for this project's chats only`() {
        // 0.18.19 keel TUI's /undo and /redo with no argument: a whole KeelBot answer
        val (pid, _) = newProject("helper-undo-turn")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("title" to "Scores")).json()["id"].asText()
        val u = post("/api/projects/$pid/helper/sessions/$sid/undo-turn").andExpect(status().isOk).json()
        assertThat(u["text"].asText()).isEqualTo("add b")
        assertThat(u["files"].map { it.asText() }).containsExactly("src/a.kt", "src/b.kt")
        val r = post("/api/projects/$pid/helper/sessions/$sid/redo-turn").andExpect(status().isOk).json()
        assertThat(r["n"].asInt()).isEqualTo(3)
        val (other, _) = newProject("helper-undo-turn-other")
        post("/api/projects/$other/helper/sessions/$sid/undo-turn").andExpect(status().isNotFound)
        post("/api/projects/$other/helper/sessions/$sid/redo-turn").andExpect(status().isNotFound)
    }

    @Test
    fun `a bang command reaches the engine's guarded run, and its output goes with the next turn`() {
        // 0.18.18 keel TUI's `!command`: the engine runs it through keel's guard; its output goes with the next question
        val (pid, _) = newProject("helper-run")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("title" to "Shell")).json()["id"].asText()
        val r = post("/api/projects/$pid/helper/sessions/$sid/run", mapOf("command" to " ls ")).andExpect(status().isOk).json()
        assertThat(r["exit"].asInt()).isEqualTo(0)
        assertThat(engine.lastBody("/helper/sessions/$sid/run")!!["command"].asText()).isEqualTo("ls")
        post("/api/projects/$pid/helper/sessions/$sid/run", mapOf("command" to "  ")).andExpect(status().isBadRequest)
        val (other, _) = newProject("helper-run-other")
        post("/api/projects/$other/helper/sessions/$sid/run", mapOf("command" to "ls")).andExpect(status().isNotFound)
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "why?",
            "shell" to mapOf("command" to "npm test", "exit" to 1, "output" to "1 failed"))).andExpect(status().isOk)
        val shell = engine.lastBody("/helper/sessions/$sid/turn")!!["shell"]
        assertThat(shell["command"].asText()).isEqualTo("npm test")
        assertThat(shell["exit"].asInt()).isEqualTo(1)
        assertThat(shell["output"].asText()).isEqualTo("1 failed")
    }

    @Test
    fun `the images the person names go on to the engine with the turn`() {
        // 0.18.18 keel TUI sends the image files a prompt names; the engine sends those the model can see
        val (pid, _) = newProject("helper-images")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("title" to "Look")).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "what is wrong here?",
            "images" to listOf(" docs/shot.png ", "docs/shot.png", ""))).andExpect(status().isOk)
        val sent = engine.lastBody("/helper/sessions/$sid/turn")!!
        assertThat(sent["images"].map { it.asText() }).containsExactly("docs/shot.png")
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "and now?")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid/turn")!!.has("images")).isFalse()
    }

    @Test
    fun `the prompt history is the engine's, for this project only`() {
        // 0.18.18 keel TUI's ↑ / ↓ walk the person's earlier prompts of the project, newest first, from keel
        val (pid, _) = newProject("helper-prompts")
        val r = get("/api/projects/$pid/helper/prompts?limit=50").andExpect(status().isOk).json()
        assertThat(r.map { it.asText() }).containsExactly("And the tests?", "Where does it start?")
        val q = engine.calls.last { it.path == "/helper/prompts" }.query!!
        assertThat(q).contains("project=$pid").contains("limit=50")
        get("/api/projects/nope-no-such/helper/prompts").andExpect(status().isNotFound)
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
    fun `a turn carries the waiting question's ids and choices for KeelBot's answer button`() {
        val (pid, _) = newProject("helper-asked")
        val sid = post("/api/projects/$pid/helper/sessions").json()["id"].asText()
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Bug hunt", "allow_fake" to true)).andExpect(status().isOk)
        engine.overrides["t-stub-1"] = mapOf("status" to "waiting", "title" to "Bug hunt",
            "waiting" to mapOf("step" to "triage", "kind" to "gate", "title" to "triage", "detail" to "1 open", "options" to listOf("approve"),
                "choices" to listOf("take", "close"),
                "choice_info" to listOf(
                    mapOf("name" to "take", "label" to "Take the top group", "description" to "d", "why" to "none", "default" to true),
                    mapOf("name" to "close", "label" to "Close a finding", "description" to "d", "why" to "required", "default" to false)),
                "questions" to listOf(mapOf("id" to "as", "question" to "How does it close?", "multiple" to false,
                    "options" to listOf(mapOf("label" to "fixed", "recommended" to false), mapOf("label" to "wontfix", "recommended" to true))))))
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "close it")).andExpect(status().isOk)
        val w = engine.lastBody("/helper/sessions/$sid/turn")!!["flow"]["waiting"]
        assertThat(w["questions"][0]["id"].asText()).isEqualTo("as")
        assertThat(w["questions"][0]["options"].map { it.asText() }).containsExactly("fixed", "wontfix")
        assertThat(w["questions"][0]["recommended"].map { it.asText() }).containsExactly("wontfix")
        assertThat(w["choices"].map { it["name"].asText() }).containsExactly("take", "close")
        assertThat(w["choices"][1]["why"].asText()).isEqualTo("required")
        assertThat(w["choices"][0]["default"].asBoolean()).isTrue()
    }

    @Test
    fun `0_18_28 a turn carries each choice's real effect and what a plain reject does`() {
        val (pid, _) = newProject("helper-effects")
        val sid = post("/api/projects/$pid/helper/sessions").json()["id"].asText()
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "ETA", "allow_fake" to true)).andExpect(status().isOk)
        engine.overrides["t-stub-1"] = mapOf("status" to "waiting", "title" to "ETA",
            "waiting" to mapOf("step" to "integration_commit", "name" to "verify the suite + commit", "kind" to "fix",
                "title" to "verify the suite + commit: the suite fails", "detail" to "2 fail", "options" to listOf("approve", "reject"),
                "choices" to listOf("send_back_ac", "stop"),
                "choice_info" to listOf(
                    mapOf("name" to "send_back_ac", "label" to "Send back to AC-008", "description" to "its test is the likely cause", "why" to "optional", "default" to true),
                    mapOf("name" to "stop", "label" to "Stop the flow", "description" to "stop here", "why" to "optional", "default" to false)),
                "plain" to mapOf("reject" to mapOf("does" to "stops the flow", "reason" to "not used"))))
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "what now?")).andExpect(status().isOk)
        val w = engine.lastBody("/helper/sessions/$sid/turn")!!["flow"]["waiting"]
        assertThat(w["name"].asText()).isEqualTo("verify the suite + commit")
        assertThat(w["choices"][0]["description"].asText()).isEqualTo("its test is the likely cause")
        assertThat(w["plain"]["reject"]["does"].asText()).isEqualTo("stops the flow")
        assertThat(w["plain"]["reject"]["reason"].asText()).isEqualTo("not used")
    }

    @Test
    fun `KeelBot's effort comes from the helper agent, a chat can change it, and default clears it`() {
        val (pid, _) = newProject("helper-effort")
        put("/api/projects/$pid/agents/helper", mapOf("model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet", "effort" to "high")))
            .andExpect(status().isOk)
        val sid = post("/api/projects/$pid/helper/sessions").andExpect(status().isOk).json()["id"].asText()
        assertThat(engine.lastBody("/helper/sessions")!!["model"]["effort"].asText()).isEqualTo("high")
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "opus", "effort" to "MAX")))
            .andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!["model"]["effort"].asText()).isEqualTo("max")
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "opus", "effort" to "default")))
            .andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!["model"].has("effort")).isFalse()
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "opus", "effort" to "very high")))
            .andExpect(status().isBadRequest)
    }

    @Test
    fun `its events are agent calls with their steps, counted in the budget, and never a flow`() {
        val (pid, _) = newProject("helper-events")
        fun ev(type: String, data: Map<String, Any?>) = mapOf("type" to type, "thread_id" to "h_abc", "project_id" to pid, "step" to "helper",
            "call_id" to "c-helper-1", "at" to java.time.Instant.now().toString(), "data" to data)
        post("/internal/events", listOf(
            ev("helper.started", mapOf("agent" to "helper", "provider" to "claude", "model" to "sonnet", "mode" to "api", "phase" to "helper-ask")),
            ev("helper.step", mapOf("n" to 1, "kind" to "read", "text" to "line 1", "path" to "src/a.kt", "ok" to true)),
            ev("helper.finished", mapOf("agent" to "helper", "status" to "done", "tokens_in" to 1200, "tokens_out" to 300, "cost_usd" to 0.01, "result" to "It is in src/a.kt:1")),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val row = jdbc.queryForMap("SELECT agent, thread_id, status, tokens_in, mode, phase FROM agent_calls WHERE id = 'c-helper-1'")
        assertThat(row["agent"]).isEqualTo("helper")
        assertThat(row["thread_id"]).isEqualTo("h_abc")
        assertThat(row["status"]).isEqualTo("done")
        assertThat((row["tokens_in"] as Number).toInt()).isEqualTo(1200)
        assertThat(row["mode"]).isEqualTo("api")
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
    fun `a chat changes its mode in place, and Fix takes the flow that waits now`() {
        val (pid, _) = newProject("helper-mode")
        val sid = post("/api/projects/$pid/helper/sessions").json()["id"].asText()
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("mode" to "edit")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!["mode"].asText()).isEqualTo("edit")
        assertThat(engine.lastBody("/helper/sessions/$sid")!!.has("thread_id")).isFalse()
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("mode" to "loud")).andExpect(status().isBadRequest)
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("mode" to "fix")).andExpect(status().isConflict)
        waitingFlow(pid, "t-mode-1")
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("mode" to "fix")).andExpect(status().isOk)
        val body = engine.lastBody("/helper/sessions/$sid")!!
        assertThat(body["mode"].asText()).isEqualTo("fix")
        assertThat(body["thread_id"].asText()).isEqualTo("t-mode-1")
        assertThat(body["flow"]["phases_before"][0].asText()).isEqualTo("green")
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
        // 0.18.20 one answer's files (keel TUI's block after it): the turn goes on to the engine
        get("/api/projects/$pid/helper/sessions/$sid/changes?turn=3").andExpect(status().isOk)
        assertThat(engine.calls.last { it.path == "/helper/sessions/$sid/changes" }.query).isEqualTo("turn=3")
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
        // step 2: the answer goes through core approvals, to the engine's POST /approvals/{id}
        assertThat(engine.lastBody("/approvals/p_1")!!["why"].asText()).isEqualTo("not now")
        assertThat(get("/api/inbox?project=$pid").json()["items"].none { it["kind"].asText() == "permission" }).isTrue()
    }

    @Test
    fun `a permission question notifies the person, and its answer clears the notification`() {
        val (pid, _) = newProject("helper-notify")
        fun ev(type: String, data: Map<String, Any?>) = mapOf("type" to type, "thread_id" to "h_n", "project_id" to pid, "step" to "helper",
            "at" to java.time.Instant.now().toString(), "data" to data)
        post("/internal/events", listOf(ev("helper.permission", mapOf("id" to "p_2", "command" to "rm -rf build"))), mapOf("X-Keel-Token" to TOKEN))
            .andExpect(status().isOk)
        val note = get("/api/notifications").json().first { it["title"].asText() == "KeelBot asks to run a command" }
        assertThat(note["body"].asText()).isEqualTo("rm -rf build")
        post("/internal/events", listOf(ev("helper.permission.answered", mapOf("id" to "p_2", "decision" to "deny"))), mapOf("X-Keel-Token" to TOKEN))
        assertThat(get("/api/notifications").json().first { it["title"].asText() == "KeelBot asks to run a command" }["done"].asBoolean()).isTrue()
    }

    @Test
    fun `a side session keeps on its own branch and is handed over as a task or as a flow on that branch`() {
        val (pid, root) = newProject("helper-side")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("mode" to "side")).andExpect(status().isOk).json()["id"].asText()
        assertThat(engine.lastBody("/helper/sessions")!!["mode"].asText()).isEqualTo("side")
        // Keep needs no waiting flow: the engine gets no flow and commits on the side branch
        put("/api/projects/$pid/settings", mapOf("commit_author" to "Ada Lovelace <ada@example.com>")).andExpect(status().isOk)
        post("/api/projects/$pid/helper/sessions/$sid/done", mapOf("message" to "price helper")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid/done")!!["flow"].size()).isEqualTo(0)
        // keel's commit is by the person the settings name, with KeelBot as co-author (on by default)
        val commit = engine.lastBody("/helper/sessions/$sid/done")!!["commit"]
        assertThat(commit["commit_author"].asText()).isEqualTo("Ada Lovelace <ada@example.com>")
        assertThat(commit["commit_coauthor"].asBoolean()).isTrue()

        val sha = git(root, "rev-parse", "HEAD").trim()
        git(root, "branch", "keel/helper/abc")
        engine.helperHandover = engine.helperHandover + mapOf("base" to sha,
            "commits" to listOf(mapOf("sha" to "1234567abc", "subject" to "fix(helper): price helper")))
        // a task: the Tasks plugin makes it (plugins/tasks tests that); keel's core alone has no tasks, and says so
        val noTasks = post("/api/projects/$pid/helper/sessions/$sid/task", mapOf("type" to "story")).andExpect(status().isConflict).json()
        assertThat(noTasks["error"].asText()).isEqualTo("Tasks are not installed in this keel")
        post("/api/projects/$pid/helper/sessions/nope/task", mapOf("type" to "story")).andExpect(status().isNotFound)

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

    @Test
    fun `a branch that cannot be checked out leaves the side session as it was, its worktree not released`() {
        val (pid, root) = newProject("helper-side-checkout")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("mode" to "side")).andExpect(status().isOk).json()["id"].asText()
        val sha = git(root, "rev-parse", "HEAD").trim()
        val saved = engine.helperHandover            // the stub engine is shared by the other tests: put its handover back
        try {
            engine.helperHandover = engine.helperHandover + mapOf("base" to sha, "branch" to "keel/helper/missing",
                "uncommitted" to emptyList<Any>(), "commits" to listOf(mapOf("sha" to "1234567abc", "subject" to "fix(helper): x")))
            post("/api/projects/$pid/helper/sessions/$sid/flow", mapOf("workflow_id" to "feature")).andExpect(status().isConflict)
            assertThat(engine.calls.any { it.path == "/helper/sessions/$sid/release" }).isFalse()
        } finally {
            engine.helperHandover = saved
        }
    }

    private fun startFlow(pid: String, title: String) =
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to title, "allow_fake" to true, "allow_dirty" to true,
            "where" to "worktree")).andExpect(status().isOk)

    @Test
    fun `every KeelBot message carries the workflows it can suggest and the project's flows`() {
        val (pid, _) = newProject("keelbot-context")
        engine.nextThreadIds.addAll(listOf("t-kb-1", "t-kb-2"))
        startFlow(pid, "Euro prices")
        startFlow(pid, "Score ranks")
        val sid = post("/api/projects/$pid/helper/sessions", emptyMap<String, Any>()).andExpect(status().isOk).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "Which workflow for a new report page?")).andExpect(status().isOk)
        val keel = engine.lastBody("/helper/sessions/$sid/turn")!!["keel"]
        val feature = keel["workflows"].first { it["id"].asText() == "feature" }
        assertThat(feature["source"].asText()).isEqualTo("keel")
        assertThat(feature["steps"].size()).isGreaterThan(0)
        assertThat(feature["last_run"]["title"].asText()).isEqualTo("Score ranks")
        assertThat(keel["flows"].map { it["title"].asText() }).containsExactly("Score ranks", "Euro prices")
        assertThat(keel["flows"][0]["workflow"].asText()).isEqualTo("feature")
        assertThat(keel["flows"][0]["thread_id"].asText()).isEqualTo("t-kb-2")
    }

    @Test
    fun `KeelBot's old answer route delegates to approvals, for this project's questions only`() {
        val (pid, _) = newProject("appr-old")
        val (other, _) = newProject("appr-other")
        val q = mapOf("id" to "p_d00000000001", "kind" to "command", "project" to pid, "title" to "Fix the totals",
            "command" to "npm install left-pad", "path" to "", "source" to "keelbot", "thread_id" to null, "session" to "h_1",
            "at" to java.time.Instant.now().toString())
        engine.helperQuestions += q
        post("/internal/events", listOf(mapOf("type" to "approval.asked", "thread_id" to "h_1", "project_id" to pid, "step" to "approval",
            "at" to java.time.Instant.now().toString(), "data" to q)), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/helper/permissions").json().map { it["id"].asText() }).containsExactly("p_d00000000001")
        // another project cannot answer it
        post("/api/projects/$other/helper/permissions/p_d00000000001", mapOf("decision" to "once")).andExpect(status().isNotFound)
        val r = post("/api/projects/$pid/helper/permissions/p_d00000000001", mapOf("decision" to "once")).andExpect(status().isOk).json()
        assertThat(r["decision"].asText()).isEqualTo("once")
        assertThat(engine.lastBody("/approvals/p_d00000000001")!!["decision"].asText()).isEqualTo("once")
        assertThat(get("/api/projects/$pid/helper/permissions").json().size()).isZero()
    }

    @Test
    fun `its turns are agent calls through its event handler, never a flow`() {
        val (pid, _) = newProject("appr-handlers")
        fun ev(type: String, at: String, data: Map<String, Any?>) = mapOf("type" to type, "thread_id" to "h_z", "project_id" to pid,
            "step" to "helper", "call_id" to "c-appr-1", "at" to at, "data" to data)
        post("/internal/events", listOf(
            ev("helper.started", "2026-10-08T10:00:00Z", mapOf("provider" to "fake", "mode" to "api", "phase" to "helper-ask")),
            ev("helper.step", "2026-10-08T10:00:01Z", mapOf("n" to 1, "kind" to "tool", "server" to "keel-db", "text" to "db_query")),
            ev("helper.finished", "2026-10-08T10:00:02Z", mapOf("status" to "done", "tokens_in" to 10, "tokens_out" to 2)),
        ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        val row = jdbc.queryForMap("SELECT agent, status, steps_count, mcp_calls, tokens_in, mode FROM agent_calls WHERE id = 'c-appr-1'")
        assertThat(row["agent"]).isEqualTo("helper")
        assertThat(row["status"]).isEqualTo("done")
        assertThat((row["steps_count"] as Number).toInt()).isEqualTo(1)
        assertThat((row["mcp_calls"] as Number).toInt()).isEqualTo(1)
        assertThat((row["tokens_in"] as Number).toInt()).isEqualTo(10)
        assertThat(row["mode"]).isEqualTo("fake")
        assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE id = 'h_z'", Int::class.java)).isZero()   // never a flow
    }

    @Test
    fun `a turn carries the project's plugins and their secrets, and the commands come with its plugins`() {
        val (pid, _) = newProject("plug-flow")
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to " ghp_example token ")).andExpect(status().isOk)
        val sid = post("/api/projects/$pid/helper/sessions", emptyMap<String, Any>()).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "How many scores?")).andExpect(status().isOk)
        val turn = engine.lastBody("/helper/sessions/$sid/turn")!!
        assertThat(turn["plugins"].map { it.asText() }).containsExactly("db", "git")
        assertThat(turn["keys"]["github"].asText()).isEqualTo("ghp_exampletoken")
        get("/api/projects/$pid/helper/commands").andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/commands")!!["plugins"].map { it.asText() }).containsExactly("db", "git")
        mvc.perform(MockMvcRequestBuilders.delete("/api/secrets/GITHUB_REPO_TOKEN")).andExpect(status().isOk)
    }

    @Test
    fun `KeelBot uses the Workspace, not the Code page's RepoService`() {
        val needs = HelperService::class.java.constructors.flatMap { it.parameterTypes.toList() }
        assertThat(needs).contains(keel.api.workspace.Workspace::class.java).doesNotContain(keel.api.repo.RepoService::class.java)
    }
}
