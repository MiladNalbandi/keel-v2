package keel.api.tasks

import com.fasterxml.jackson.databind.JsonNode
import keel.api.flow.TaskSink
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/**
 * v0.5.0: the Tasks plugin on its own (no Jira plugin on the class path): local tasks and their lifecycle driven by
 * engine events, the Inbox items, a ticket key that keel cannot move (no tracker: the Inbox asks to move it by hand),
 * a task's keel-criteria block, and KeelBot's "Make a task" (core's TaskSink). Jira's tests: plugins/jira/api.
 */
class TasksApiTest : ApiTest() {

    companion object {
        const val PR = "https://github.com/acme/app/pull/7"
    }

    private fun ev(type: String, pid: String, tid: String, step: String? = null, data: Map<String, Any?> = emptyMap()) =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to step, "at" to "2026-10-06T10:00:00Z", "data" to data)

    private fun send(vararg events: Map<String, Any?>) =
        post("/internal/events", events.toList(), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    private fun task(id: String): JsonNode = get("/api/tasks/$id").andExpect(status().isOk).json()
    private fun kinds(t: JsonNode) = t["events"].map { it["kind"].asText() }
    private fun notes(t: JsonNode) = t["events"].map { it["note"]?.asText().orEmpty() }
    private fun inboxOf(pid: String) = get("/api/inbox?project=$pid").json()["items"].filter { it["task"] != null && !it["task"].isNull }

    @Test
    fun `a local task is created, edited, started and walked to done without Jira`() {
        val (pid, root) = newProject("tasks-local")
        post("/api/projects/$pid/tasks", mapOf("title" to " ")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/tasks", mapOf("title" to "x", "type" to "epic")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/tasks", mapOf("title" to "x", "external_key" to "not a key")).andExpect(status().isBadRequest)
        post("/api/projects/nope/tasks", mapOf("title" to "x")).andExpect(status().isNotFound)

        val t = post("/api/projects/$pid/tasks", mapOf("title" to "Rank players", "type" to "story", "description" to "Top ten, weekly.", "reviewers" to listOf("@octocat")))
            .andExpect(status().isOk).json()
        val id = t["id"].asText()
        assertThat(t["status"].asText()).isEqualTo("todo")
        assertThat(t["source"].asText()).isEqualTo("local")
        assertThat(t["reviewers"][0]["login"].asText()).isEqualTo("octocat")
        assertThat(t["events"].single()["actor"].asText()).isEqualTo("user")
        val list = get("/api/projects/$pid/tasks").json()
        assertThat(list["tasks"].map { it["id"].asText() }).containsExactly(id)
        assertThat(list["sync"]["connected"].asBoolean()).isFalse()
        assertThat(get("/api/projects/$pid/tasks?source=jira").json()["tasks"].size()).isEqualTo(0)

        put("/api/tasks/$id", mapOf("title" to "Rank the players", "priority" to "High")).andExpect(status().isOk)
        assertThat(notes(task(id)).last()).isEqualTo("Changed: title, priority.")

        // uncommitted files: the start is refused, the task stays and its history says why
        Files.writeString(root.resolve("notes.txt"), "mine")
        post("/api/tasks/$id/start", emptyMap<String, Any>()).andExpect(status().isConflict)
        assertThat(task(id)["status"].asText()).isEqualTo("todo")
        assertThat(kinds(task(id)).last()).isEqualTo("start_refused")
        assertThat(notes(task(id)).last()).contains("did not start").contains("uncommitted changes")

        // story → the feature flow; title, description go to the engine as the request
        engine.nextThreadIds += "t-local-1"
        val started = post("/api/tasks/$id/start", mapOf("allow_dirty" to true, "run_mode" to "important")).andExpect(status().isOk).json()
        assertThat(started["status"].asText()).isEqualTo("in_progress")
        assertThat(started["thread_id"].asText()).isEqualTo("t-local-1")
        assertThat(started["workflow_id"].asText()).isEqualTo("feature")
        assertThat(started["flow"]["status"].asText()).isEqualTo("running")
        val body = engine.lastBody("/threads")!!
        assertThat(body["title"].asText()).isEqualTo("Rank the players")
        assertThat(body["request"].asText()).isEqualTo("Rank the players\n\nTop ten, weekly.")
        assertThat(body["settings"]["run_mode"].asText()).isEqualTo("important")
        post("/api/tasks/$id/start", mapOf("allow_dirty" to true)).andExpect(status().isConflict)        // a flow runs already

        // the flow opens its PR: In review; no GitHub token, so the reviewers are named but not asked
        send(ev("step.finished", pid, "t-local-1", "open", mapOf("note" to "PR opened: $PR")))
        val review = task(id)
        assertThat(review["status"].asText()).isEqualTo("in_review")
        assertThat(review["pr_url"].asText()).isEqualTo(PR)
        assertThat(notes(review).last()).contains("no GitHub token").contains("octocat")
        send(ev("thread.done", pid, "t-local-1"))
        assertThat(task(id)["status"].asText()).isEqualTo("in_review")

        // approved by hand (no token to read the reviews): Testing (PP) and an Inbox item
        post("/api/tasks/$id/status", mapOf("to" to "testing_pp")).andExpect(status().isOk)
        val pp = inboxOf(pid).single()
        assertThat(pp["kind"].asText()).isEqualTo("task")
        assertThat(pp["title"].asText()).isEqualTo("Confirm PP testing for task ${id.take(8)}")
        assertThat(pp["task"]["actions"].map { it["id"].asText() }).containsExactly("confirm", "send_back")
        assertThat(get("/api/inbox/count").json()["projects"][pid].asInt()).isEqualTo(1)
        assertThat(get("/api/projects/$pid").json()["waiting"].asInt()).isEqualTo(1)
        assertThat(get("/api/inbox?kind=task").json()["kinds"].map { it.asText() }).contains("task")

        post("/api/inbox/tasks/${pp["task"]["item_id"].asLong()}/act", mapOf("action" to "confirm", "note" to "works on PP")).andExpect(status().isOk)
        post("/api/inbox/tasks/${pp["task"]["item_id"].asLong()}/act", mapOf("action" to "confirm")).andExpect(status().isConflict)
        val ship = inboxOf(pid).single()
        assertThat(ship["title"].asText()).startsWith("Ship task ")
        assertThat(task(id)["status"].asText()).isEqualTo("ready_prod")
        post("/api/tasks/$id/confirm", mapOf("stage" to "pp")).andExpect(status().isConflict)
        post("/api/tasks/$id/confirm", mapOf("stage" to "qa")).andExpect(status().isBadRequest)
        val done = post("/api/tasks/$id/confirm", mapOf("stage" to "prod")).andExpect(status().isOk).json()
        assertThat(done["status"].asText()).isEqualTo("done")
        assertThat(done["waiting"].size()).isEqualTo(0)
        assertThat(inboxOf(pid)).isEmpty()
        assertThat(kinds(done)).containsSubsequence("created", "updated", "start_refused", "start", "pr", "github", "flow", "status", "confirm", "confirm")
        assertThat(kinds(done)).doesNotContain("jira", "jira_manual", "jira_error")       // no Jira key: nothing to move

        delete("/api/tasks/$id").andExpect(status().isOk)
        get("/api/tasks/$id").andExpect(status().isNotFound)
    }

    @Test
    fun `a flow that ends without a PR waits for its link, and one whose engine state names the PR moves on`() {
        val (pid, _) = newProject("tasks-nopr")
        val a = post("/api/projects/$pid/tasks", mapOf("title" to "A", "type" to "story")).json()["id"].asText()
        val b = post("/api/projects/$pid/tasks", mapOf("title" to "B", "type" to "story")).json()["id"].asText()
        engine.nextThreadIds += "t-np-a"
        post("/api/tasks/$a/start", mapOf("allow_dirty" to true)).andExpect(status().isOk)
        engine.nextThreadIds += "t-np-b"
        post("/api/tasks/$b/start", mapOf("allow_dirty" to true)).andExpect(status().isOk)
        // the open_pr step said why (B11): the history and an Inbox item say it, and the item takes the PR's link
        send(ev("step.finished", pid, "t-np-a", data = mapOf("name" to "open_pr", "note" to
            "The branch feat/a is not pushed, and keel never pushes. Push it (git push -u origin feat/a), then open the PR. Copy the PR body (shown at the gate) into your PR.")))
        send(ev("thread.done", pid, "t-np-a"))
        assertThat(task(a)["status"].asText()).isEqualTo("in_progress")
        assertThat(notes(task(a)).last()).contains("without a pull request").contains("feat/a is not pushed").doesNotContain("Copy the PR body")
        val ask = inboxOf(pid).single { it["task"]["id"].asText() == a }
        assertThat(ask["title"].asText()).endsWith("the flow finished, open its pull request")
        assertThat(ask["detail"].asText()).contains("git push -u origin feat/a")
        assertThat(ask["task"]["actions"].map { it["id"].asText() }).containsExactly("pr", "later")
        val askId = ask["task"]["item_id"].asLong()
        post("/api/inbox/tasks/$askId/act", mapOf("action" to "pr", "note" to "not a link")).andExpect(status().isBadRequest)
        post("/api/inbox/tasks/$askId/act", mapOf("action" to "pr", "note" to "https://github.com/acme/app/pull/7")).andExpect(status().isOk)
        assertThat(task(a)["status"].asText()).isEqualTo("in_review")
        assertThat(inboxOf(pid).filter { it["task"]["id"].asText() == a }).isEmpty()
        engine.overrides["t-np-b"] = mapOf("status" to "done", "pr_url" to "https://github.com/acme/app/pull/9")
        send(ev("thread.done", pid, "t-np-b"))
        assertThat(task(b)["status"].asText()).isEqualTo("in_review")
        assertThat(task(b)["pr_url"].asText()).isEqualTo("https://github.com/acme/app/pull/9")
        engine.overrides.remove("t-np-b")
    }

    @Test
    fun `without the Jira plugin a ticket key asks for the move by hand, and Sync now reads only the PR reviews`() {
        val (pid, _) = newProject("tasks-alone")
        val list = get("/api/projects/$pid/tasks").json()
        assertThat(list["sync"]["connected"].asBoolean()).isFalse()
        assertThat(list["sync"]["kind"].isNull).isTrue()
        val sync = post("/api/projects/$pid/tasks/sync").andExpect(status().isOk).json()
        assertThat(sync["ok"].asBoolean()).isTrue()
        assertThat(sync["jira"].asBoolean()).isFalse()
        post("/api/projects/nope/tasks/sync").andExpect(status().isNotFound)

        val t = post("/api/projects/$pid/tasks", mapOf("title" to "Fix the queue", "type" to "bug", "external_key" to "ops-7")).json()
        val id = t["id"].asText()
        assertThat(t["external_url"].isNull).isTrue()                                       // no tracker: no link to the ticket
        engine.nextThreadIds += "t-alone-1"
        post("/api/tasks/$id/start", mapOf("workflow_id" to "feature", "allow_dirty" to true)).andExpect(status().isOk)
        val manual = inboxOf(pid).single()
        assertThat(manual["kind"].asText()).isEqualTo("jira-manual")
        assertThat(manual["title"].asText()).isEqualTo("Move OPS-7 to In Progress in Jira (keel could not: no Jira connection for this project)")
        post("/api/inbox/tasks/${manual["task"]["item_id"].asLong()}/act", mapOf("action" to "done")).andExpect(status().isOk)
        assertThat(task(id)["events"].last()["note"].asText()).isEqualTo("You moved OPS-7 to In Progress in Jira by hand.")
        assertThat(inboxOf(pid)).isEmpty()
    }

    @Test
    fun `a task's keel-criteria block goes to its flow as the criteria`() {
        val (pid, _) = newProject("core-criteria")
        val description = """
            Why: EU visitors want euro prices.

            ```keel-criteria
            AC-1 [API] GET /prices returns the currency
            - AC-2 [web] An EU visitor sees the price in euro
            not a criterion
            ```
        """.trimIndent()
        val task = post("/api/projects/$pid/tasks", mapOf("title" to "Euro prices", "description" to description, "type" to "story")).json()
        post("/api/tasks/${task["id"].asText()}/start", mapOf("allow_fake" to true)).andExpect(status().isOk)
        val sent = engine.lastBody("/threads")!!
        assertThat(sent["acs"].map { it["id"].asText() + " " + it["layer"].asText() + " " + it["title"].asText() })
            .containsExactly("AC-1 API GET /prices returns the currency", "AC-2 WEB An EU visitor sees the price in euro")
        assertThat(sent["request"].asText()).contains("Why: EU visitors want euro prices.")
    }

    @Test
    fun `KeelBot hands a side session over as a task through the one TaskSink`(@Autowired sinks: List<TaskSink>) {
        // KeelBot (plugins/keelbot) calls the TaskSink with the session's title and what it did; this is the sink
        val (pid, _) = newProject("helper-side-task")
        val sink = sinks.single()
        val text = "Made in a KeelBot side session on branch `keel/helper/abc` (from 1234567).\n\n- 1234567 fix(helper): price helper"
        val t = mapper.valueToTree<JsonNode>(sink.create(pid, "Add a price helper", text, "story"))
        assertThat(t["title"].asText()).isEqualTo("Add a price helper")
        assertThat(t["type"].asText()).isEqualTo("story")
        assertThat(t["status"].asText()).isEqualTo("todo")
        assertThat(t["description"].asText()).isEqualTo(text)
        assertThat(get("/api/projects/$pid/tasks").json()["tasks"].map { it["id"].asText() }).containsExactly(t["id"].asText())
        org.junit.jupiter.api.assertThrows<keel.api.common.BadRequest> { sink.create(pid, "Add a price helper", text, "epic") }
    }

    @Test
    fun `0_34 a task handed to the Org follows its mission, and each part the lead gives out becomes a task`() {
        val (pid, _) = newProject("tasks-org")
        val id = post("/api/projects/$pid/tasks", mapOf("title" to "Carrier size rules", "description" to "Store a carrier's limits."))
            .andExpect(status().isOk).json()["id"].asText()
        post("/api/tasks/$id/org", mapOf("org" to " ")).andExpect(status().isBadRequest)

        val t = post("/api/tasks/$id/org", mapOf("org" to "small-team", "cap_tokens" to 400000)).andExpect(status().isOk).json()
        val body = engine.lastBody("/org/orgs/small-team/missions")!!
        assertThat(body["task_id"].asText()).isEqualTo(id)
        assertThat(body["project_id"].asText()).isEqualTo(pid)
        assertThat(body["request"].asText()).contains("Store a carrier's limits.").contains("keel task:")
        assertThat(body["cap_tokens"].asInt()).isEqualTo(400000)
        assertThat(body.has("role")).isFalse()           // the org's lead decides
        val mid = t["mission"]["id"].asText()
        assertThat(t["mission"]["org"].asText()).isEqualTo("small-team")
        assertThat(notes(t).last()).contains("Handed to the Org small-team (lead): mission $mid.")
        // a second hand-over while the mission is open is refused
        post("/api/tasks/$id/org", mapOf("org" to "small-team")).andExpect(status().isConflict)

        fun mission(kind: String, m: String, status: String, extra: Map<String, Any?> = emptyMap()) = ev("org.mission.$kind", pid, "", data =
            mapOf("mission_id" to m, "org" to "small-team", "status" to status, "root_task_id" to id, "root_mission_id" to mid) + extra)

        send(mission("started", mid, "running", mapOf("task_id" to id, "parent_id" to null)))
        assertThat(task(id)["status"].asText()).isEqualTo("in_progress")
        send(mission("delegated", mid, "delegated", mapOf("task_id" to id, "parent_id" to null, "children" to listOf(
            mapOf("mission_id" to "m_fe", "role" to "fe", "title" to "The rule store's API", "request" to "PUT /carriers/{id}/rules"),
            mapOf("mission_id" to "m_be", "role" to "be", "title" to "The rule table", "request" to "A migration")))))
        val parts = task(id)["parts"]
        assertThat(parts.map { it["title"].asText() }).containsExactly("The rule store's API", "The rule table")
        assertThat(notes(task(id)).last()).isEqualTo("The Org's lead gave the work out in 2 parts.")
        val fe = parts[0]["id"].asText()
        assertThat(task(fe)["parent_task_id"].asText()).isEqualTo(id)
        assertThat(task(fe)["assignee"].asText()).isEqualTo("fe")
        assertThat(task(fe)["mission"]["id"].asText()).isEqualTo("m_fe")
        // the same delegation told twice makes no second part
        send(mission("delegated", mid, "delegated", mapOf("task_id" to id, "parent_id" to null, "children" to listOf(
            mapOf("mission_id" to "m_fe", "role" to "fe", "title" to "The rule store's API")))))
        assertThat(task(id)["parts"].size()).isEqualTo(2)

        // a part follows its own mission
        send(mission("started", "m_fe", "running", mapOf("parent_id" to mid)))
        assertThat(task(fe)["status"].asText()).isEqualTo("in_progress")
        send(mission("reported", "m_fe", "reported", mapOf("parent_id" to mid, "summary" to "PUT works.")))
        assertThat(task(fe)["status"].asText()).isEqualTo("in_review")
        send(mission("failed", "m_be", "failed", mapOf("parent_id" to mid, "reason" to "the migration did not run")))
        assertThat(task(parts[1]["id"].asText())["status"].asText()).isEqualTo("blocked")
        assertThat(task(parts[1]["id"].asText())["blocked_reason"].asText()).isEqualTo("the migration did not run")

        // the lead's report waits for the person, then it is accepted: the task and its reported parts are done
        send(mission("reported", mid, "reported", mapOf("task_id" to id, "parent_id" to null, "summary" to "Both parts ended.")))
        val inReview = task(id)
        assertThat(inReview["status"].asText()).isEqualTo("in_review")
        assertThat(notes(inReview).last()).contains("its report waits for you").contains("Both parts ended.")
        send(mission("done", mid, "done", mapOf("task_id" to id, "parent_id" to null)), mission("done", "m_fe", "done", mapOf("parent_id" to mid)))
        assertThat(task(id)["status"].asText()).isEqualTo("done")
        assertThat(task(id)["mission"]["status"].asText()).isEqualTo("done")
        assertThat(task(fe)["status"].asText()).isEqualTo("done")
    }

    @Test
    fun `0_34 the Org refusing a task leaves it where it was, with why`() {
        val (pid, _) = newProject("tasks-org-no")
        val id = post("/api/projects/$pid/tasks", mapOf("title" to "x")).andExpect(status().isOk).json()["id"].asText()
        engine.orgMissionAnswer = 400 to mapOf("error" to "The org has no role qa", "hint" to "Its roles: lead, fe.")
        try {
            post("/api/tasks/$id/org", mapOf("org" to "small-team", "role" to "qa")).andExpect(status().isBadRequest)
        } finally {
            engine.orgMissionAnswer = null
        }
        val t = task(id)
        assertThat(t["status"].asText()).isEqualTo("todo")
        assertThat(t["mission"]?.isNull ?: true).isTrue()
        assertThat(notes(t).last()).contains("The Org did not take it: The org has no role qa")
    }
}
