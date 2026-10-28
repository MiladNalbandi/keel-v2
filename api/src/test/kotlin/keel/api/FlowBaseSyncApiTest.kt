package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/** 0.18.64 field report bugs 4 and 6: a start fetches every remote before it makes the flow's branch and says how far
 *  the base is behind; the start can name its base; keel syncs a flow's branch onto a base (engine /threads/{id}/sync). */
class FlowBaseSyncApiTest : ApiTest() {

    /** A bare `origin` one commit ahead of the project's main (another person pushed), and a `mirror` that is gone. */
    private fun withRemotes(name: String): Pair<String, Path> {
        val (pid, root) = newProject(name)
        val bare = Files.createTempDirectory("keel-remote").resolve("origin.git")
        git(root.parent, "init", "-q", "--bare", "-b", "main", bare.toString())
        git(root, "remote", "add", "origin", bare.toString())
        git(root, "push", "-q", "origin", "main")
        val other = Files.createTempDirectory("keel-other").resolve("other")
        git(root.parent, "clone", "-q", bare.toString(), other.toString())
        Files.writeString(other.resolve("team.txt"), "the team's latest\n")
        git(other, "add", "team.txt"); git(other, "commit", "-q", "-m", "feat: the team's latest")
        git(other, "push", "-q", "origin", "main")
        git(root, "remote", "add", "mirror", "file://${root.parent}/no-such-repo.git")
        return pid to root
    }

    @Test
    fun `a start fetches every remote first, so the new branch starts from the newer code, and a dead remote never stops it`() {
        val (pid, root) = withRemotes("fetch-first")
        // the last fetch (the push) left origin/main where main is: without a fetch the branch would start from stale code
        assertThat(git(root, "rev-parse", "origin/main").trim()).isEqualTo(git(root, "rev-parse", "main").trim())
        engine.baseNote = "fetch from mirror failed: fetch failed; the base may be stale; the flow's branch starts from origin/main"
        try {
            val res = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Basket totals", "allow_fake" to true))
                .andExpect(status().isOk).json()
            assertThat(res["base_note"].asText()).contains("fetch from mirror failed").contains("starts from origin/main")
        } finally {
            engine.baseNote = null
        }
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/basket-totals")
        assertThat(Files.exists(root.resolve("team.txt"))).isTrue()               // fetched, then branched from origin/main
        val fetched = engine.lastBody("/threads")!!["data"]["remotes_fetched"]
        assertThat(fetched.map { it["remote"].asText() to it["ok"].asBoolean() }).containsExactlyInAnyOrder("origin" to true, "mirror" to false)
    }

    @Test
    fun `a project without a remote fetches nothing and sends the engine nothing about it`() {
        val (pid, _) = newProject("no-remote")
        val before = engine.calls.count { it.path == "/remotes/fetch" }
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores", "allow_fake" to true)).andExpect(status().isOk)
        assertThat(engine.calls.count { it.path == "/remotes/fetch" }).isEqualTo(before)
        assertThat(engine.lastBody("/threads")!!.path("data").path("remotes_fetched").isMissingNode).isTrue()
    }

    @Test
    fun `base names the branch the flow's new branch starts from`() {
        val (pid, root) = withRemotes("start-from-base")
        git(root, "checkout", "-q", "-b", "release")
        Files.writeString(root.resolve("release.txt"), "release\n")
        git(root, "add", "release.txt"); git(root, "commit", "-q", "-m", "chore: release")
        git(root, "checkout", "-q", "main")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Hot fix", "allow_fake" to true, "base" to "release"))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/hot-fix")
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "release").trim())
        // next to that flow, in a worktree of its own: the worktree starts from the base named (a remote branch here)
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "From origin", "allow_fake" to true,
            "base" to "origin/main", "where" to "worktree")).andExpect(status().isOk)
        assertThat(engine.lastBody("/worktrees")!!["start"].asText()).isEqualTo("origin/main")
    }

    @Test
    fun `a base that does not exist or goes with continue is refused before anything changes`() {
        val (pid, root) = withRemotes("bad-base")
        val res = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "X", "allow_fake" to true, "base" to "upstream/main"))
            .andExpect(status().isBadRequest).json()
        assertThat(res["error"].asText()).isEqualTo("There is no branch upstream/main to start from")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "X", "allow_fake" to true, "base" to "origin/main",
            "branch" to "continue")).andExpect(status().isBadRequest)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("main")
        assertThat(git(root, "branch", "--list", "feat/*").trim()).isEmpty()
    }

    @Test
    fun `the remotes fetch endpoint answers one result per remote`() {
        val (pid, _) = withRemotes("fetch-endpoint")
        val res = post("/api/projects/$pid/remotes/fetch").andExpect(status().isOk).json()
        assertThat(res["remotes"].map { it["remote"].asText() to it["ok"].asBoolean() }).containsExactlyInAnyOrder("origin" to true, "mirror" to false)
        assertThat(engine.lastBody("/remotes/fetch")!!["root"].asText()).endsWith("fetch-endpoint")
    }

    @Test
    fun `a sync goes to the engine with the flow's folder and is checked first`() {
        val (pid, root) = newProject("sync")
        engine.nextThreadIds.add("t-sync")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores", "allow_fake" to true)).andExpect(status().isOk)
        val res = post("/api/threads/t-sync/sync", mapOf("onto" to "origin/main", "mode" to "rebase")).andExpect(status().isOk).json()
        assertThat(res["ok"].asBoolean()).isTrue()
        val sent = engine.lastBody("/threads/t-sync/sync")!!
        assertThat(sent["onto"].asText()).isEqualTo("origin/main")
        assertThat(sent["mode"].asText()).isEqualTo("rebase")
        assertThat(sent["action"].asText()).isEqualTo("start")
        assertThat(sent["root"].asText()).isEqualTo(root.toString())
        post("/api/threads/t-sync/sync").andExpect(status().isOk)                                     // every field has a default
        assertThat(engine.lastBody("/threads/t-sync/sync")!!["mode"].asText()).isEqualTo("merge")
        post("/api/threads/t-sync/sync", mapOf("mode" to "squash")).andExpect(status().isBadRequest)
        post("/api/threads/t-sync/sync", mapOf("action" to "redo")).andExpect(status().isBadRequest)
        post("/api/threads/t-nope/sync").andExpect(status().isNotFound)
    }
}
