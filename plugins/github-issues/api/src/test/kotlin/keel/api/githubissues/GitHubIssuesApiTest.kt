package keel.api.githubissues

import com.fasterxml.jackson.databind.JsonNode
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * 0.22.0 (issue #64) the GitHub Issues plugin with the Tasks plugin: a project's connection (the repo from its origin,
 * the token of Connections › GitHub), the sync (the issues it follows become tasks), and a task's moves on the real
 * issue: comments, a status label, closed when done, "Closes #N" in the PR, and GitHub's own moves coming back.
 */
class GitHubIssuesApiTest : ApiTest() {

    private fun task(id: String): JsonNode = get("/api/tasks/$id").andExpect(status().isOk).json()
    private fun tasks(pid: String) = get("/api/projects/$pid/tasks").json()["tasks"]
    private fun notes(t: JsonNode) = t["events"].map { it["note"]?.asText().orEmpty() }
    private fun patches() = github.calls.filter { it.method == "PATCH" }

    /** A project whose origin is acme/app on github.com. */
    private fun project(name: String): String {
        val (pid, root) = newProject(name)
        git(root, "remote", "add", "origin", "git@github.com:acme/app.git")
        return pid
    }

    @BeforeEach
    fun token() {
        github.reset()
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to github.token)).andExpect(status().isOk)
    }

    @AfterEach
    fun noToken() {
        delete("/api/secrets/GITHUB_REPO_TOKEN")
    }

    @Test
    fun `a project connects with the repo of its origin and keel's GitHub token, which is never shown`() {
        val pid = project("gh-connect")
        val v0 = get("/api/projects/$pid/github-issues").andExpect(status().isOk).json()
        assertThat(v0["connected"].asBoolean()).isFalse()
        assertThat(v0["origin"].asText()).isEqualTo("acme/app")
        assertThat(v0["token_set"].asBoolean()).isTrue()
        assertThat(v0["status_map"]["done"].asText()).isEqualTo("closed")
        assertThat(v0["status_map"]["cancelled"].asText()).isEqualTo("closed:not planned")
        assertThat(v0["status_map"]["in_progress"].asText()).isEqualTo("-")

        val t = post("/api/projects/$pid/github-issues/test").andExpect(status().isOk).json()
        assertThat(t["ok"].asBoolean()).isTrue()
        assertThat(t["user"].asText()).isEqualTo("mili-dev")
        assertThat(t["repo"].asText()).isEqualTo("acme/app")

        val v = put("/api/projects/$pid/github-issues", mapOf("poll_minutes" to 0)).andExpect(status().isOk).json()
        assertThat(v["connected"].asBoolean()).isTrue()
        assertThat(v["repo"].asText()).isEqualTo("acme/app")
        assertThat(v["repo_from"].asText()).isEqualTo("origin")
        assertThat(v.toString()).doesNotContain(github.token)
        assertThat(get("/api/connections/kinds").json().map { it["kind"].asText() }).contains("github-issues")

        // a repo that is not GitHub's, a follow mode and a status target keel does not know are refused
        put("/api/projects/$pid/github-issues", mapOf("repo" to "https://gitlab.com/a/b")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/github-issues", mapOf("follow" to "mine")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/github-issues", mapOf("follow" to "label")).andExpect(status().isBadRequest)     // which label?
        put("/api/projects/$pid/github-issues", mapOf("status_map" to mapOf("done" to "deleted"))).andExpect(status().isBadRequest)

        delete("/api/projects/$pid/github-issues").andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/github-issues").json()["connected"].asBoolean()).isFalse()
    }

    @Test
    fun `without a token or a GitHub origin keel says what is missing`() {
        val (pid, _) = newProject("gh-no-origin")
        delete("/api/secrets/GITHUB_REPO_TOKEN")
        assertThat(post("/api/projects/$pid/github-issues/test").json()["error"].asText()).contains("no GitHub token")
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to github.token)).andExpect(status().isOk)
        assertThat(post("/api/projects/$pid/github-issues/test").json()["hint"].asText()).contains("owner/repo")
        put("/api/projects/$pid/github-issues", emptyMap<String, Any>()).andExpect(status().isBadRequest)
        // named by hand it works, also as the repo's web address
        val v = put("/api/projects/$pid/github-issues", mapOf("repo" to "https://github.com/acme/app")).andExpect(status().isOk).json()
        assertThat(v["repo"].asText()).isEqualTo("acme/app")
        assertThat(v["repo_from"].asText()).isEqualTo("settings")
        github.hasIssues = false
        assertThat(post("/api/projects/$pid/github-issues/test").json()["error"].asText()).contains("Issues are turned off")
    }

    @Test
    fun `the issues assigned to you become tasks, pull requests and others' issues do not, and a sync upserts`() {
        val pid = project("gh-sync")
        github.issue(1, "Rank players", labels = listOf("bug"), body = "Players want ranks.")
        github.issue(2, "Someone else's", assignees = listOf("ana"))
        github.issues[3] = keel.api.support.StubGitHub.Issue(3, "A pull request", assignees = listOf("mili-dev"), pr = true)
        put("/api/projects/$pid/github-issues", mapOf("poll_minutes" to 0)).andExpect(status().isOk)

        val sync = post("/api/projects/$pid/tasks/sync").andExpect(status().isOk).json()
        assertThat(sync["ok"].asBoolean()).isTrue()
        assertThat(sync["created"].asInt()).isEqualTo(1)
        val t = tasks(pid).single()
        assertThat(t["external_key"].asText()).isEqualTo("#1")
        assertThat(t["source"].asText()).isEqualTo("github")
        assertThat(t["type"].asText()).isEqualTo("bug")
        assertThat(t["status"].asText()).isEqualTo("todo")
        assertThat(t["external_url"].asText()).isEqualTo("https://github.com/acme/app/issues/1")
        assertThat(t["description"].asText()).isEqualTo("Players want ranks.")
        val info = get("/api/projects/$pid/tasks").json()["sync"]
        assertThat(info["tracker"].asText()).isEqualTo("github")
        assertThat(info["where"].asText()).isEqualTo("acme/app")
        assertThat(info["me"].asText()).isEqualTo("mili-dev")
        assertThat(github.calls.first { it.path == "/repos/acme/app/issues" }.method).isEqualTo("GET")

        github.issues.getValue(1).title = "Rank the players"
        val again = post("/api/projects/$pid/tasks/sync").json()
        assertThat(again["created"].asInt()).isEqualTo(0)
        assertThat(again["updated"].asInt()).isEqualTo(1)
        assertThat(tasks(pid).single()["title"].asText()).isEqualTo("Rank the players")

        // follow a label instead: issue 2 has it, so it comes in too
        github.issues.getValue(2).labels += "keel"
        put("/api/projects/$pid/github-issues", mapOf("follow" to "label", "label" to "keel")).andExpect(status().isOk)
        assertThat(post("/api/projects/$pid/tasks/sync").json()["created"].asInt()).isEqualTo(1)
        assertThat(tasks(pid).map { it["external_key"].asText() }).containsExactlyInAnyOrder("#1", "#2")
    }

    @Test
    fun `a task's start comments on the issue and its PR closes it, done closes it, a label follows a status, GitHub's moves come back`() {
        val pid = project("gh-life")
        github.issue(7, "Coupons apply once")
        put("/api/projects/$pid/github-issues", mapOf("poll_minutes" to 0, "status_map" to mapOf("in_review" to "label:in review")))
            .andExpect(status().isOk)
        post("/api/projects/$pid/tasks/sync").andExpect(status().isOk)
        val id = tasks(pid).single()["id"].asText()

        engine.nextThreadIds += "t-gh-1"
        post("/api/tasks/$id/start", mapOf("workflow_id" to "feature", "allow_dirty" to true)).andExpect(status().isOk)
        val body = engine.lastBody("/threads")!!
        assertThat(body["title"].asText()).isEqualTo("#7: Coupons apply once")
        assertThat(body["request"].asText()).endsWith("GitHub ticket: #7 (https://github.com/acme/app/issues/7)")
        assertThat(body["data"]["ticket"].asText()).isEqualTo("#7")
        assertThat(body["data"]["pr_line"].asText()).isEqualTo("Closes #7")
        val issue = github.issues.getValue(7)
        assertThat(issue.comments.last().second).startsWith("keel started the feature flow for this ticket.")
        assertThat(issue.state).isEqualTo("open")
        assertThat(patches()).isEmpty()                         // in progress: "-", the issue stays as it is

        // in review: the status label
        post("/api/tasks/$id/pr", mapOf("url" to "https://github.com/acme/app/pull/9")).andExpect(status().isOk)
        assertThat(issue.labels).contains("in review")
        assertThat(issue.comments.last().second).isEqualTo("Pull request: https://github.com/acme/app/pull/9")
        assertThat(task(id)["external_status"].asText()).isEqualTo("label:in review")
        assertThat(notes(task(id))).contains("GitHub: moved #7 from open to label:in review.")

        // keel's own move is no move on the next sync
        val calls = github.calls.size
        assertThat(post("/api/projects/$pid/tasks/sync").json()["moved"].asInt()).isEqualTo(0)
        assertThat(github.calls.drop(calls).none { it.method != "GET" }).isTrue()

        // closed on GitHub (the PR was merged): the task is done, and keel sends nothing back
        issue.state = "closed"
        issue.reason = "completed"
        val before = github.calls.size
        assertThat(post("/api/projects/$pid/tasks/sync").json()["moved"].asInt()).isEqualTo(1)
        val done = task(id)
        assertThat(done["status"].asText()).isEqualTo("done")
        assertThat(done["events"].last()["actor"].asText()).isEqualTo("github")
        assertThat(github.calls.drop(before).none { it.method != "GET" }).isTrue()
    }

    @Test
    fun `cancelled closes the issue as not planned, and a token that may only read asks you to move it by hand`() {
        val pid = project("gh-cancel")
        github.issue(4, "Old idea")
        github.issue(5, "Read only")
        put("/api/projects/$pid/github-issues", mapOf("poll_minutes" to 0, "comment" to false)).andExpect(status().isOk)
        post("/api/projects/$pid/tasks/sync").andExpect(status().isOk)
        val byKey = tasks(pid).associate { it["external_key"].asText() to it["id"].asText() }

        post("/api/tasks/${byKey["#4"]}/status", mapOf("to" to "cancelled", "note" to "not needed")).andExpect(status().isOk)
        assertThat(github.issues.getValue(4).state).isEqualTo("closed")
        assertThat(github.issues.getValue(4).reason).isEqualTo("not_planned")
        assertThat(github.issues.getValue(4).comments).isEmpty()          // comments are off for this connection

        github.canPush = false
        post("/api/tasks/${byKey["#5"]}/status", mapOf("to" to "cancelled", "note" to "no")).andExpect(status().isOk)
        assertThat(github.issues.getValue(5).state).isEqualTo("open")
        val inbox = get("/api/inbox?project=$pid").json()["items"].filter { it["task"] != null && !it["task"].isNull }
        assertThat(inbox.map { it["title"].asText() }).anyMatch { it.startsWith("Move #5 to closed:not planned in GitHub") }
    }

    @Test
    fun `a ticket key is a Jira key or a GitHub issue, and a project follows one tracker`() {
        val pid = project("gh-keys")
        put("/api/projects/$pid/github-issues", mapOf("poll_minutes" to 0)).andExpect(status().isOk)
        val t = post("/api/projects/$pid/tasks", mapOf("title" to "By hand", "external_key" to "#12")).andExpect(status().isOk).json()
        assertThat(t["source"].asText()).isEqualTo("github")
        assertThat(t["external_url"].asText()).isEqualTo("https://github.com/acme/app/issues/12")
        val j = post("/api/projects/$pid/tasks", mapOf("title" to "A Jira one", "external_key" to "abc-3")).andExpect(status().isOk).json()
        assertThat(j["source"].asText()).isEqualTo("jira")
        assertThat(j["external_key"].asText()).isEqualTo("ABC-3")
        post("/api/projects/$pid/tasks", mapOf("title" to "No", "external_key" to "twelve")).andExpect(status().isBadRequest)

        post("/api/projects/$pid/tasks", mapOf("title" to "No", "external_key" to "#")).andExpect(status().isBadRequest)
    }
}
