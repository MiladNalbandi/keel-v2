package keel.api.review

import com.fasterxml.jackson.databind.JsonNode
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/**
 * 0.18.65 the stale-base field report (R3, R4): a branch review compares against merge-base(<canonical remote>/<default
 * branch>, HEAD), says when the local base went its own way, lets the person pick the local base instead, and leaves
 * keel init's setup commits at the branch's bottom out. Two bare remotes that disagree: origin (master: A, O1) and
 * github (main: A, M1, M2, M3); the local master (A, O1, X1) is 3 behind and 2 ahead of github/main.
 */
class ReviewBaseApiTest : ApiTest() {

    private fun commit(root: Path, rel: String, text: String, msg: String) {
        val f = root.resolve(rel)
        Files.createDirectories(f.parent)
        Files.writeString(f, text)
        git(root, "add", rel)
        git(root, "commit", "-q", "-m", msg)
    }

    /** The project folder on feat/x: one file on top of github/main (started there, as 0.18.64's base lets a flow). */
    private fun twoRemotes(name: String, config: String = "base_branch: master\ngit:\n  remote: github\n"): Pair<String, Path> {
        val (pid, root) = newProject(name)
        val remotes = Files.createTempDirectory("keel-two-remotes")
        git(root, "branch", "-m", "main", "master")
        val origin = remotes.resolve("origin.git").toString()
        val github = remotes.resolve("github.git").toString()
        git(remotes, "init", "-q", "--bare", "-b", "master", origin)
        git(remotes, "init", "-q", "--bare", "-b", "main", github)
        git(root, "push", "-q", github, "master:main")
        commit(root, "old/copy.txt", "an older copy\n", "O1")
        git(root, "remote", "add", "origin", origin)
        git(root, "push", "-q", "-u", "origin", "master")
        val up = remotes.resolve("up")
        git(remotes, "clone", "-q", github, up.toString())
        for (i in 1..3) commit(up, "landed/m$i.txt", "$i\n", "M$i")
        git(up, "push", "-q", "origin", "main")
        git(root, "remote", "add", "github", github)
        git(root, "fetch", "-q", "github")
        commit(root, "local.txt", "only here\n", "X1")
        Files.createDirectories(root.resolve(".keel"))
        Files.writeString(root.resolve(".keel/config.yml"), config)
        git(root, "checkout", "-q", "--no-track", "-b", "feat/x", "github/main")
        commit(root, "feature.txt", "the change\n", "feat: the change")
        put("/api/projects/$pid/plugins/review", mapOf("enabled" to true)).andExpect(status().isOk)
        return pid to root
    }

    private fun view(pid: String, key: String): JsonNode = get("/api/projects/$pid/review/view?key=$key").andExpect(status().isOk).json()

    @Test
    fun `a branch review compares against the canonical remote's default branch and warns about the local base`() {
        val (pid, _) = twoRemotes("review-two-remotes")
        val v = view(pid, "branch:feat/x")
        assertThat(v["base"].asText()).isEqualTo("github/main")
        assertThat(v["files"].map { it["path"].asText() }).containsExactly("feature.txt")
        assertThat(v["base_warning"].asText())
            .isEqualTo("Your local master is 3 commits behind and 2 ahead of github/main: this review compares against github/main.")
        assertThat(v["base_options"].map { it["id"].asText() to it["label"].asText() })
            .containsExactly("remote" to "github/main", "local" to "master")
        assertThat(v["base_choice"].asText()).isEqualTo("remote")
        val b = get("/api/projects/$pid/review/branch").andExpect(status().isOk).json()
        assertThat(b["base"].asText()).isEqualTo("github/main")
        assertThat(b["files"].asInt()).isEqualTo(1)
        assertThat(b["ahead"].asInt()).isEqualTo(1)
        assertThat(b["base_warning"].asText()).contains("this review compares against github/main")
    }

    @Test
    fun `the panel can pick the local base, and the file count follows it`() {
        val (pid, _) = twoRemotes("review-pick-base")
        val local = put("/api/projects/$pid/review/base", mapOf("key" to "branch:feat/x", "base" to "local")).andExpect(status().isOk).json()
        assertThat(local["base"].asText()).isEqualTo("master")
        assertThat(local["base_choice"].asText()).isEqualTo("local")
        // the stale local base shows the other change's files as this branch's: the warning says so
        assertThat(local["files"].size()).isEqualTo(4)
        assertThat(local["base_warning"].asText()).contains("compares against your local master, so it may show other changes' files")
        assertThat(get("/api/projects/$pid/review/diff?key=branch:feat/x&path=landed/m1.txt").andExpect(status().isOk).json()["path"].asText())
            .isEqualTo("landed/m1.txt")
        val back = put("/api/projects/$pid/review/base", mapOf("key" to "branch:feat/x", "base" to "")).andExpect(status().isOk).json()
        assertThat(back["base"].asText()).isEqualTo("github/main")
        assertThat(back["files"].size()).isEqualTo(1)
        put("/api/projects/$pid/review/base", mapOf("key" to "branch:feat/x", "base" to "elsewhere")).andExpect(status().isBadRequest)
        put("/api/projects/$pid/review/base", mapOf("key" to "pr:7", "base" to "local")).andExpect(status().isBadRequest)
    }

    @Test
    fun `a base_branch written as remote branch and no remote copy fall back as they should`() {
        val (pid, root) = twoRemotes("review-remote-named", "base_branch: github/main\n")
        val v = view(pid, "branch:feat/x")
        assertThat(v["base"].asText()).isEqualTo("github/main")
        assertThat(v["files"].size()).isEqualTo(1)
        assertThat(v["base_warning"].isNull).isTrue()            // no local main to drift
        // no remote at all: the local base, as before
        git(root, "remote", "remove", "github")
        git(root, "remote", "remove", "origin")
        Files.writeString(root.resolve(".keel/config.yml"), "base_branch: master\n")
        val local = view(pid, "branch:feat/x")
        assertThat(local["base"].asText()).isEqualTo("master")
        assertThat(local["base_options"].map { it["id"].asText() }).containsExactly("local")
    }

    @Test
    fun `keel init's setup commits at the bottom of the branch are not the branch's change`() {
        val (pid, root) = twoRemotes("review-setup-left-out")
        git(root, "checkout", "-q", "--no-track", "-b", "keel/setup", "github/main")
        commit(root, ".keel/rules/x.yaml", "rules: []\n", "chore(setup): keel init")
        commit(root, "docs/RUNNING.md", "# running\n", "chore(setup): keel init")
        git(root, "checkout", "-q", "-b", "feat/y")
        commit(root, "y.txt", "y\n", "feat: y")
        val v = view(pid, "branch:feat/y")
        assertThat(v["files"].map { it["path"].asText() }).containsExactly("y.txt")
        assertThat(v["setup_left_out"].size()).isEqualTo(2)
        assertThat(v["notes"].map { it.asText() }).anyMatch { it.startsWith("keel init's setup commits") && it.contains("left out") }
        // the setup branch itself is reviewed whole: its change is the setup
        val setup = view(pid, "branch:keel/setup")
        assertThat(setup["files"].size()).isEqualTo(2)
        assertThat(setup["setup_left_out"].size()).isEqualTo(0)
    }
}
