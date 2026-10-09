package keel.api.plugins

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.10.0 the Git plugin (plugins/git, moved from keel's api tests PluginsApiTest): the person's own git calls go to the
 *  engine with the token and the commit settings, a branch reads against its base, and nothing works while it is off.
 *  The GitHub token itself is keel's core (Connections › GitHub; ship opens the pull request with it). */
class GitPluginApiTest : ApiTest() {
    @Test
    fun `the person's git actions go to the engine with the token and the commit settings`() {
        val (pid, _) = newProject("plug-git")
        get("/api/projects/$pid/git/status").andExpect(status().isConflict)
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/settings", mapOf("commit_author" to "Ada Lovelace <ada@example.com>")).andExpect(status().isOk)
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to "ghp_gittoken")).andExpect(status().isOk)
        post("/api/projects/$pid/git/commit", mapOf("message" to "fix: x")).andExpect(status().isOk)
        val sent = engine.lastBody("/plugins/git/commit")!!
        assertThat(sent["message"].asText()).isEqualTo("fix: x")
        assertThat(sent["settings"]["commit_author"].asText()).isEqualTo("Ada Lovelace <ada@example.com>")
        assertThat(sent["settings"]["commit_coauthor"].asBoolean()).isTrue()
        assertThat(sent["keys"]["github"].asText()).isEqualTo("ghp_gittoken")       // core's token, memory only
        post("/api/projects/$pid/git/switch", mapOf("branch" to "feat/x", "create" to true)).andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/git/switch")!!["create"].asBoolean()).isTrue()
        post("/api/projects/$pid/git/pr", mapOf("title" to "X", "body" to "why")).andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/git/pr")!!["title"].asText()).isEqualTo("X")
        delete("/api/secrets/GITHUB_REPO_TOKEN").andExpect(status().isOk)
    }

    @Test
    fun `a branch shows its own commits and files against the base, with the Git plugin on`() {
        val (pid, root) = newProject("plug-branch", mapOf("README.md" to "# demo\n", "Score.kt" to "class Score(val v: Int)\n"))
        git(root, "checkout", "-q", "-b", "feat/test")
        java.nio.file.Files.writeString(root.resolve("Score.kt"), "class Score(val v: Long)\n")
        java.nio.file.Files.writeString(root.resolve("new.txt"), "hello\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feat: scores are Long")
        git(root, "checkout", "-q", "main")
        java.nio.file.Files.writeString(root.resolve("README.md"), "# demo 2\n")
        git(root, "commit", "-q", "-am", "docs: readme")      // on main only: not part of the branch's changes

        get("/api/projects/$pid/git/branch?name=feat/test").andExpect(status().isConflict)   // the plugin is off
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        val b = get("/api/projects/$pid/git/branch?name=feat/test").andExpect(status().isOk).json()
        assertThat(b["base"].asText()).isEqualTo("main")
        assertThat(b["current"].asBoolean()).isFalse()
        assertThat(b["ahead"].asInt() to b["behind"].asInt()).isEqualTo(1 to 1)
        assertThat(b["commits"].map { it["message"].asText() }).containsExactly("feat: scores are Long")
        assertThat(b["files"].map { it["path"].asText() + " " + it["status"].asText() }).containsExactly("Score.kt M", "new.txt A")

        // a file's diff on the branch (GET /repo/diff?branch=) is the Code page's: plugins/code/api, RepoApiTest

        val main = get("/api/projects/$pid/git/branch?name=main").json()
        assertThat(main["current"].asBoolean()).isTrue()
        assertThat(main["files"].size()).isZero()
        // a name is a local branch, never an option, a range or another ref
        get("/api/projects/$pid/git/branch?name=nope").andExpect(status().isNotFound)
        get("/api/projects/$pid/git/branch?name=-p").andExpect(status().isBadRequest)
        get("/api/projects/$pid/git/branch?name=main..feat/test").andExpect(status().isBadRequest)
    }
}
