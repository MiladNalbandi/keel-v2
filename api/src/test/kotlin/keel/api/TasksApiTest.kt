package keel.api

import com.fasterxml.jackson.databind.JsonNode
import keel.api.support.ApiTest
import keel.api.support.StubJira
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** v0.5.0: tasks, the Jira connection and sync, the lifecycle driven by engine events and PR reviews, the Inbox items. */
class TasksApiTest : ApiTest() {

    companion object {
        val cloud: StubJira = StubJira.start("cloud")
        val server: StubJira = StubJira.start("server")
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

    private fun connect(pid: String, s: StubJira, extra: Map<String, Any?> = emptyMap()): JsonNode =
        put("/api/projects/$pid/jira", mapOf("kind" to s.kind, "base_url" to s.url, "email" to if (s.cloud) s.email else null, "token" to s.token) + extra)
            .andExpect(status().isOk).json()

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
    fun `a Jira Cloud ticket goes from To do to production, keel moving the real ticket and asking reviewers`() {
        cloud.reset()
        github.reset()
        cloud.add("ABC-1", "Rank players", "To Do", "Story", "Players want ranks.")
        val (pid, _) = newProject("tasks-cloud")
        val conn = connect(pid, cloud, mapOf("project_key" to "abc", "reviewer_field" to "customfield_10010",
            "jira_reviewers" to listOf("rev@example.com"), "github_reviewers" to listOf("ana", "bo")))
        assertThat(conn["connected"].asBoolean()).isTrue()
        assertThat(conn["settings"]["project_key"].asText()).isEqualTo("ABC")
        assertThat(conn.toString()).doesNotContain(cloud.token)
        put("/api/secrets/GITHUB_TOKEN", mapOf("value" to github.token)).andExpect(status().isOk)
        try {
            val sync = post("/api/projects/$pid/tasks/sync").andExpect(status().isOk).json()
            assertThat(sync["ok"].asBoolean()).isTrue()
            assertThat(sync["created"].asInt()).isEqualTo(1)
            assertThat(cloud.calls.first { it.path == "/rest/api/3/search/jql" }.query).contains("project%20%3D%20ABC%20AND%20assignee%20%3D%20currentUser%28%29")
            val t0 = get("/api/projects/$pid/tasks").json()["tasks"].single()
            val id = t0["id"].asText()
            assertThat(t0["external_key"].asText()).isEqualTo("ABC-1")
            assertThat(t0["source"].asText()).isEqualTo("jira")
            assertThat(t0["external_url"].asText()).isEqualTo("${cloud.url}/browse/ABC-1")
            assertThat(t0["description"].asText()).isEqualTo("Players want ranks.")
            assertThat(get("/api/projects/$pid/tasks").json()["sync"]["me"].asText()).isEqualTo("Dev One")
            assertThat(post("/api/projects/$pid/tasks/sync").json()["created"].asInt()).isEqualTo(0)      // upsert, not a copy

            engine.nextThreadIds += "t-cloud-1"
            post("/api/tasks/$id/start", mapOf("workflow_id" to "feature", "allow_dirty" to true)).andExpect(status().isOk)
            val issue = cloud.issues.getValue("ABC-1")
            assertThat(issue.status).isEqualTo("In Progress")
            assertThat(issue.comments.last()).isEqualTo("keel started the feature flow for this ticket. Follow it in keel: http://keel.test/#/tasks/$id")
            assertThat(engine.lastBody("/threads")!!["title"].asText()).isEqualTo("ABC-1: Rank players")
            assertThat(engine.lastBody("/threads")!!["request"].asText()).endsWith("Jira ticket: ABC-1 (${cloud.url}/browse/ABC-1)")
            assertThat(notes(task(id))).contains("Jira: moved ABC-1 from To Do to In Progress.")

            send(ev("step.finished", pid, "t-cloud-1", "open", mapOf("note" to "PR opened: $PR")))
            val review = task(id)
            assertThat(review["status"].asText()).isEqualTo("in_review")
            assertThat(issue.status).isEqualTo("In Review")
            assertThat(issue.comments.last()).isEqualTo("Pull request: $PR")
            assertThat(github.calls.single { it.method == "POST" }.let { it.path to it.body!!["reviewers"].map { r -> r.asText() } })
                .isEqualTo("/repos/acme/app/pulls/7/requested_reviewers" to listOf("ana", "bo"))
            assertThat(issue.fields["customfield_10010"]).isEqualTo(listOf(mapOf("accountId" to "acc-rev-1")))
            assertThat(review["reviewers"].map { "${it["login"].asText()}:${it["on"].asText()}:${it["state"].asText()}" })
                .containsExactlyInAnyOrder("ana:github:requested", "bo:github:requested", "rev@example.com:jira:set")
            send(ev("thread.done", pid, "t-cloud-1"))

            // reviews: changes asked first (no move), then approved
            github.reviews["acme/app#7"] = listOf(github.review("ana", "CHANGES_REQUESTED"))
            val s1 = post("/api/projects/$pid/tasks/sync").json()
            assertThat(s1["reviews_checked"].asInt()).isEqualTo(1)
            assertThat(s1["reviews_moved"].asInt()).isEqualTo(0)
            assertThat(notes(task(id)).last()).isEqualTo("Changes requested by ana.")
            github.reviews["acme/app#7"] = listOf(github.review("ana", "CHANGES_REQUESTED"), github.review("ana", "APPROVED"), github.review("bo", "COMMENTED"))
            assertThat(post("/api/projects/$pid/tasks/sync").json()["reviews_moved"].asInt()).isEqualTo(1)
            assertThat(task(id)["status"].asText()).isEqualTo("testing_pp")
            assertThat(issue.status).isEqualTo("Testing in PP")
            val pp = inboxOf(pid).single()
            assertThat(pp["title"].asText()).isEqualTo("Confirm PP testing for ABC-1")
            assertThat(pp["task"]["key"].asText()).isEqualTo("ABC-1")
            assertThat(pp["task"]["pr_url"].asText()).isEqualTo(PR)

            // sent back from PP (a reason is needed), the PR link pasted again, approved again
            val item = pp["task"]["item_id"].asLong()
            post("/api/inbox/tasks/$item/act", mapOf("action" to "send_back")).andExpect(status().isBadRequest)
            post("/api/inbox/tasks/$item/act", mapOf("action" to "send_back", "note" to "the totals are off")).andExpect(status().isOk)
            assertThat(task(id)["status"].asText()).isEqualTo("in_progress")
            assertThat(issue.status).isEqualTo("In Progress")
            assertThat(issue.comments.last()).isEqualTo("Sent back from Testing (PP): the totals are off")
            assertThat(inboxOf(pid)).isEmpty()
            post("/api/tasks/$id/pr", mapOf("url" to "not a link")).andExpect(status().isBadRequest)
            post("/api/tasks/$id/pr", mapOf("url" to PR)).andExpect(status().isOk)
            assertThat(issue.status).isEqualTo("In Review")
            post("/api/projects/$pid/tasks/sync").andExpect(status().isOk)
            assertThat(task(id)["status"].asText()).isEqualTo("testing_pp")

            post("/api/tasks/$id/confirm", mapOf("stage" to "pp", "note" to "checked on PP")).andExpect(status().isOk)
            assertThat(issue.status).isEqualTo("Ready for Production")
            assertThat(issue.comments.last()).isEqualTo("Testing in PP passed. — checked on PP")
            val ship = inboxOf(pid).single()
            assertThat(ship["title"].asText()).isEqualTo("Ship ABC-1 to production")
            post("/api/inbox/tasks/${ship["task"]["item_id"].asLong()}/act", mapOf("action" to "confirm")).andExpect(status().isOk)
            val done = task(id)
            assertThat(done["status"].asText()).isEqualTo("done")
            assertThat(issue.status).isEqualTo("Done")
            assertThat(issue.comments.last()).isEqualTo("Shipped to production.")
            assertThat(done["events"].first()["actor"].asText()).isEqualTo("jira")
            assertThat(kinds(done)).containsSubsequence("created", "start", "jira", "pr", "jira", "reviewers", "reviewers", "flow", "review",
                "approved", "jira", "send_back", "jira", "pr", "approved", "confirm", "jira", "confirm", "jira")
            // keel's own moves are not seen as changes made in Jira
            post("/api/projects/$pid/tasks/sync").andExpect(status().isOk)
            assertThat(kinds(task(id))).doesNotContain("jira_status")
        } finally {
            delete("/api/secrets/GITHUB_TOKEN")
        }
    }

    @Test
    fun `Jira Server sync upserts tickets from a board and records what changed in Jira`() {
        server.reset()
        server.add("ABC-1", "Rank players", "To Do", "Story")
        server.add("ABC-2", "Fix the tally", "In Progress", "Bug")
        server.add("ABC-3", "Old", "Done", "Task")
        val (pid, _) = newProject("tasks-server")
        connect(pid, server, mapOf("board_id" to "7", "status_map" to mapOf("in_review" to "In Review")))
        val s = post("/api/projects/$pid/tasks/sync").andExpect(status().isOk).json()
        assertThat(s["created"].asInt()).isEqualTo(2)
        val board = server.calls.first { it.path == "/rest/agile/1.0/board/7/issue" }
        assertThat(board.auth).isEqualTo("Bearer ${server.token}")
        assertThat(board.query).contains("jql=assignee%20%3D%20currentUser%28%29").doesNotContain("project%20%3D")
        val byKey = { k: String -> get("/api/projects/$pid/tasks").json()["tasks"].first { it["external_key"].asText() == k } }
        assertThat(byKey("ABC-2")["type"].asText()).isEqualTo("bug")
        assertThat(byKey("ABC-2")["status"].asText()).isEqualTo("in_progress")
        assertThat(byKey("ABC-1")["status"].asText()).isEqualTo("todo")
        assertThat(get("/api/projects/$pid/jira").json()["last_sync_at"].isNull).isFalse()

        // changed in Jira: the title and assignee follow, the status maps to In review, both recorded as Jira's
        server.issues.getValue("ABC-1").apply { summary = "Rank players weekly"; status = "In Review"; assignee = "Dev Two" }
        server.issues.getValue("ABC-2").status = "Done"                 // leaves the query: read again by key
        val s2 = post("/api/projects/$pid/tasks/sync").json()
        assertThat(s2["moved"].asInt()).isEqualTo(2)
        val one = task(byKey("ABC-1")["id"].asText())
        assertThat(one["title"].asText()).isEqualTo("Rank players weekly")
        assertThat(one["assignee"].asText()).isEqualTo("Dev Two")
        assertThat(one["status"].asText()).isEqualTo("in_review")
        assertThat(one["external_status"].asText()).isEqualTo("In Review")
        val jiraEvents = one["events"].filter { it["actor"].asText() == "jira" }
        assertThat(jiraEvents.map { it["kind"].asText() }).containsExactly("created", "jira_update", "jira_status")
        assertThat(jiraEvents.last()["note"].asText()).isEqualTo("Jira: To Do → In Review (keel: In review)")
        assertThat(jiraEvents.last()["from_status"].asText()).isEqualTo("todo")
        assertThat(server.calls.any { it.path == "/rest/api/2/search" && it.query.contains("key%20in%20%28ABC-2%29") }).isTrue()
        assertThat(task(byKey("ABC-2")["id"].asText())["status"].asText()).isEqualTo("done")
        assertThat(server.calls.none { it.method == "POST" }).isTrue()         // a sync never writes to Jira

        // errors are kept and shown, and the token never shows
        connect(pid, server, mapOf("board_id" to "99"))
        val bad = post("/api/projects/$pid/tasks/sync").json()
        assertThat(bad["ok"].asBoolean()).isFalse()
        assertThat(bad["error"].asText()).contains("board 99").contains("404")
        assertThat(get("/api/projects/$pid/jira").json()["last_sync_error"].asText()).contains("board 99")
        put("/api/projects/$pid/jira", mapOf("token" to "bad-token-zzz111")).andExpect(status().isOk)
        val t = post("/api/projects/$pid/jira/test").json()
        assertThat(t["ok"].asBoolean()).isFalse()
        assertThat(t["error"].asText()).isEqualTo("Jira refused the login (401).")
        assertThat(t["kind"].asText()).isEqualTo("auth")
        val failed = post("/api/projects/$pid/tasks/sync").json()
        assertThat(failed["error"].asText()).isEqualTo("Jira refused the login (401).")
        assertThat(failed.toString() + get("/api/projects/$pid/jira").json().toString()).doesNotContain("bad-token-zzz111")
    }

    @Test
    fun `without a connection, or when Jira refuses, the Inbox asks to move the real ticket by hand`() {
        val (pid, _) = newProject("tasks-manual")
        val id = post("/api/projects/$pid/tasks", mapOf("title" to "Fix the queue", "type" to "bug", "external_key" to "ops-7")).json()["id"].asText()
        assertThat(task(id)["external_key"].asText()).isEqualTo("OPS-7")
        assertThat(task(id)["source"].asText()).isEqualTo("jira")
        post("/api/projects/$pid/tasks", mapOf("title" to "dup", "external_key" to "OPS-7")).andExpect(status().isConflict)

        // a bug starts the fix flow by default (the stub engine has no fix template: refused, and said on the task)
        post("/api/tasks/$id/start", mapOf("allow_dirty" to true)).andExpect(status().isNotFound)
        assertThat(notes(task(id)).last()).contains("The fix flow did not start").contains("No workflow called \"fix\"")

        engine.nextThreadIds += "t-man-1"
        post("/api/tasks/$id/start", mapOf("workflow_id" to "feature", "allow_dirty" to true)).andExpect(status().isOk)
        val manual = inboxOf(pid).single()
        assertThat(manual["kind"].asText()).isEqualTo("jira-manual")
        assertThat(manual["title"].asText()).isEqualTo("Move OPS-7 to In Progress in Jira (keel could not: no Jira connection for this project)")
        assertThat(manual["task"]["actions"].map { it["label"].asText() }).containsExactly("Done")
        assertThat(get("/api/inbox?kind=jira-manual").json()["items"].map { it["title"].asText() }).contains(manual["title"].asText())
        post("/api/inbox/tasks/${manual["task"]["item_id"].asLong()}/act", mapOf("action" to "confirm")).andExpect(status().isBadRequest)
        post("/api/inbox/tasks/${manual["task"]["item_id"].asLong()}/act", mapOf("action" to "done")).andExpect(status().isOk)
        val after = task(id)
        assertThat(after["external_status"].asText()).isEqualTo("In Progress")
        assertThat(after["events"].last()["actor"].asText()).isEqualTo("user")
        assertThat(after["events"].last()["note"].asText()).isEqualTo("You moved OPS-7 to In Progress in Jira by hand.")
        assertThat(inboxOf(pid)).isEmpty()

        // the flow hands over to another one; the task follows it, and its failure blocks the task (no Blocked in Jira: no item)
        send(ev("thread.started", pid, "t-man-2", data = mapOf("workflow" to "fix", "title" to "Fix the queue", "parent" to mapOf("thread_id" to "t-man-1", "workflow" to "feature"))))
        assertThat(task(id)["thread_id"].asText()).isEqualTo("t-man-2")
        assertThat(notes(task(id)).last()).isEqualTo("The feature flow handed the work to the fix flow.")
        send(ev("thread.failed", pid, "t-man-1", data = mapOf("error" to "old thread")))
        assertThat(task(id)["status"].asText()).isEqualTo("in_progress")                   // not its flow any more
        send(ev("thread.failed", pid, "t-man-2", data = mapOf("error" to "verify_green failed 3 times", "status" to "failed")))
        val blocked = task(id)
        assertThat(blocked["status"].asText()).isEqualTo("blocked")
        assertThat(blocked["blocked_reason"].asText()).isEqualTo("verify_green failed 3 times")
        assertThat(inboxOf(pid)).isEmpty()

        // now connected, but Jira refuses the move: keel says why, and asks for the move by hand (never with the token)
        cloud.reset()
        cloud.add("OPS-7", "Fix the queue", "In Progress", "Bug")
        connect(pid, cloud)
        post("/api/tasks/$id/status", mapOf("to" to "in_progress")).andExpect(status().isOk)
        assertThat(notes(task(id))).contains("Jira: OPS-7 is already in In Progress.")
        cloud.failures["/rest/api/2/issue/OPS-7/transitions"] = 403 to "You cannot do this. ({auth})"
        post("/api/tasks/$id/pr", mapOf("url" to PR)).andExpect(status().isOk)
        val refused = inboxOf(pid).single()
        assertThat(refused["title"].asText()).startsWith("Move OPS-7 to In Review in Jira (keel could not: Jira says this account may not use ticket OPS-7 (403)")
        assertThat(refused.toString() + task(id).toString()).doesNotContain(cloud.token)
        assertThat(kinds(task(id))).contains("jira_error", "jira_manual")

        // a stopped flow after a new start: blocked again
        cloud.failures.clear()
        post("/api/tasks/$id/status", mapOf("to" to "in_progress")).andExpect(status().isBadRequest)    // from review a reason is needed
        post("/api/tasks/$id/status", mapOf("to" to "in_progress", "note" to "redo")).andExpect(status().isOk)
        assertThat(inboxOf(pid).map { it["kind"].asText() }).doesNotContain("task")
        assertThat(inboxOf(pid)).isEmpty()                                                   // the move by hand was replaced, then done by keel
        engine.nextThreadIds += "t-man-3"
        post("/api/tasks/$id/start", mapOf("workflow_id" to "feature", "allow_dirty" to true)).andExpect(status().isOk)
        send(ev("thread.done", pid, "t-man-3", data = mapOf("status" to "stopped")))
        assertThat(task(id)["status"].asText()).isEqualTo("blocked")
        assertThat(task(id)["blocked_reason"].asText()).isEqualTo("stopped")
        post("/api/tasks/$id/status", mapOf("to" to "cancelled", "note" to "not needed")).andExpect(status().isOk)
        assertThat(cloud.issues.getValue("OPS-7").status).isEqualTo("In Progress")          // Cancelled is not mapped: the ticket stays
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
        send(ev("thread.done", pid, "t-np-a"))
        assertThat(task(a)["status"].asText()).isEqualTo("in_progress")
        assertThat(notes(task(a)).last()).contains("without a pull request")
        engine.overrides["t-np-b"] = mapOf("status" to "done", "pr_url" to "https://github.com/acme/app/pull/9")
        send(ev("thread.done", pid, "t-np-b"))
        assertThat(task(b)["status"].asText()).isEqualTo("in_review")
        assertThat(task(b)["pr_url"].asText()).isEqualTo("https://github.com/acme/app/pull/9")
        engine.overrides.remove("t-np-b")
    }

    @Test
    fun `the Jira connection is checked, keeps its token secret, discovers the mapping and offers the MCP server`() {
        cloud.reset()
        cloud.add("ABC-1", "Rank players")
        val (pid, _) = newProject("tasks-conn")
        val base = mapOf("kind" to "cloud", "base_url" to cloud.url, "email" to cloud.email, "token" to cloud.token)
        put("/api/projects/$pid/jira", base + ("kind" to "onprem")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base + ("base_url" to "jira.example.com")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base + ("email" to "")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base - "token").andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base + ("project_key" to "a b")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base + ("github_reviewers" to listOf("not a login!"))).andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base + ("poll_minutes" to 5000)).andExpect(status().isBadRequest)
        put("/api/projects/$pid/jira", base + ("status_map" to mapOf("shipped" to "Done"))).andExpect(status().isBadRequest)
        assertThat(get("/api/projects/$pid/jira").json()["connected"].asBoolean()).isFalse()
        assertThat(get("/api/projects/$pid/mcp-catalog").json()[0]["ready"].asBoolean()).isFalse()
        post("/api/projects/$pid/mcp-catalog/jira").andExpect(status().isConflict)

        val v = put("/api/projects/$pid/jira", base + mapOf("project_key" to "ABC", "poll_minutes" to 0)).andExpect(status().isOk).json()
        assertThat(v["token_hint"].asText()).isEqualTo("…XYZ")
        assertThat(v["default_jql"].asText()).isEqualTo("assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC")
        assertThat(v["jql"].asText()).startsWith("project = ABC AND ")
        assertThat(get("/api/projects/$pid/jira").andReturn().response.contentAsString).doesNotContain(cloud.token)
        // a later save without the token keeps it
        put("/api/projects/$pid/jira", mapOf("jql" to "project = ABC")).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/jira").json()["jql"].asText()).isEqualTo("project = ABC")

        val ok = post("/api/projects/$pid/jira/test").andExpect(status().isOk).json()
        assertThat(ok["ok"].asBoolean()).isTrue()
        assertThat(ok["user"]["name"].asText()).isEqualTo("Dev One")
        assertThat(get("/api/projects/$pid/jira").json()["me"]["account_id"].asText()).isEqualTo("acc-dev-1")
        val unsaved = post("/api/projects/$pid/jira/test", mapOf("token" to "typo-token-777")).json()
        assertThat(unsaved["ok"].asBoolean()).isFalse()
        assertThat(unsaved.toString()).doesNotContain("typo-token-777")
        val elsewhere = post("/api/projects/$pid/jira/test", mapOf("base_url" to "https://elsewhere.example.com")).json()
        assertThat(elsewhere["ok"].asBoolean()).isFalse()
        assertThat(elsewhere["hint"].asText()).contains("another Jira URL")

        val d = get("/api/projects/$pid/jira/discover?key=ABC-1").andExpect(status().isOk).json()
        assertThat(d["statuses"].map { it["name"].asText() }).containsExactly("To Do", "In Progress", "In Review", "Testing in PP", "Ready for Production", "Done")
        assertThat(d["fields"].map { it["id"].asText() }).containsExactly("customfield_10010", "customfield_10020")
        assertThat(d["suggested"]["in_review"].asText()).isEqualTo("In Review")
        assertThat(d["suggested"]["testing_pp"].asText()).isEqualTo("Testing in PP")
        assertThat(d["suggested"]["ready_prod"].asText()).isEqualTo("Ready for Production")
        assertThat(d["transitions"].map { it["to"].asText() }).contains("In Progress")
        assertThat(d["keel_statuses"].size()).isEqualTo(8)
        get("/api/projects/$pid/jira/discover?key=ABC-404").andExpect(status().isBadGateway)

        // the optional MCP server: off, the token only as a reference, resolved when a flow starts it
        val entry = get("/api/projects/$pid/mcp-catalog").json()[0]
        assertThat(entry["name"].asText()).isEqualTo("Jira (mcp-atlassian)")
        assertThat(entry["license"].asText()).isEqualTo("MIT")
        assertThat(entry["ready"].asBoolean()).isTrue()
        val srv = post("/api/projects/$pid/mcp-catalog/jira").andExpect(status().isOk).json()
        val name = srv["name"].asText()
        assertThat(name).isEqualTo("jira-$pid")
        assertThat(srv["enabled"].asBoolean()).isFalse()
        assertThat(srv["command"].asText()).isEqualTo("uvx")
        assertThat(srv["args"].map { it.asText() }).containsExactly("mcp-atlassian")
        assertThat(srv["env"]["JIRA_URL"].asText()).isEqualTo(cloud.url)
        assertThat(srv["env"]["JIRA_USERNAME"].asText()).isEqualTo(cloud.email)
        assertThat(srv["env"]["JIRA_API_TOKEN"].asText()).isEqualTo("secret:jira.$pid")
        assertThat(srv["env"]["READ_ONLY_MODE"].asText()).isEqualTo("true")
        assertThat(srv["env"]["JIRA_PROJECTS_FILTER"].asText()).isEqualTo("ABC")
        assertThat(srv["label"].asText()).contains("mcp-atlassian")
        assertThat(get("/api/mcp-servers").andReturn().response.contentAsString).doesNotContain(cloud.token)
        assertThat(get("/api/projects/$pid/jira").json()["mcp_server"].asText()).isEqualTo(name)
        put("/api/mcp-servers/$name", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/settings", mapOf("mcp" to listOf("keel", name))).andExpect(status().isOk)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "read the ticket", "allow_dirty" to true)).andExpect(status().isOk)
        val spec = engine.lastBody("/threads")!!["mcp"].first { it["name"].asText() == name }
        assertThat(spec["env"]["JIRA_API_TOKEN"].asText()).isEqualTo(cloud.token)

        delete("/api/projects/$pid/jira").andExpect(status().isOk)
        delete("/api/projects/$pid/jira").andExpect(status().isNotFound)
        val gone = get("/api/projects/$pid/jira").json()
        assertThat(gone["connected"].asBoolean()).isFalse()
        assertThat(gone["token_set"].asBoolean()).isFalse()
        assertThat(get("/api/mcp-servers").json().none { it["name"].asText() == name }).isTrue()
    }
}
