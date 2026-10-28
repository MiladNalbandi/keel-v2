package keel.api

import keel.api.support.ApiTest
import keel.api.workspace.Workspace
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/**
 * 0.18.65 the stale-base field report (R2, R4, R5): the project's canonical remote (git.remote in .keel/config.yml, else
 * the base's upstream, else a remote that has the base, else origin, else the only remote), a new branch from its copy
 * of the base, a start that asks first when base_branch names no local branch, and a flow on init's keel/setup branch.
 * Two bare remotes that disagree: origin (master: A, O1) and github (main: A, M1, M2, M3); the local master (A, O1, X1)
 * is 1 ahead of origin/master, and 2 ahead and 3 behind github/main.
 */
class FlowCanonicalBaseApiTest : ApiTest() {
    @Autowired lateinit var workspace: Workspace

    private fun commit(root: Path, rel: String, text: String, msg: String) {
        val f = root.resolve(rel)
        Files.createDirectories(f.parent)
        Files.writeString(f, text)
        git(root, "add", rel)
        git(root, "commit", "-q", "-m", msg)
    }

    private fun config(root: Path, text: String) {
        Files.createDirectories(root.resolve(".keel"))
        Files.writeString(root.resolve(".keel/config.yml"), text)
        // keel's config is not the person's work: an untracked .keel/ never makes the folder dirty for a start
    }

    private fun twoRemotes(name: String): Pair<String, Path> {
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
        return pid to root
    }

    @Test
    fun `the canonical remote is the base's upstream unless the config names one`() {
        val (_, root) = twoRemotes("canonical-default")
        config(root, "base_branch: master\n")
        val byUpstream = workspace.baseInfo(root)
        assertThat(byUpstream.canonical).isEqualTo("origin")
        assertThat(byUpstream.remote).isEqualTo("origin/master")
        assertThat(byUpstream.ahead to byUpstream.behind).isEqualTo(1 to 0)
        config(root, "base_branch: master\ngit:\n  remote: github\n")
        val named = workspace.baseInfo(root)
        assertThat(named.canonical).isEqualTo("github")
        assertThat(named.ref).isEqualTo("github/main")
        assertThat(named.ahead to named.behind).isEqualTo(2 to 3)
        assertThat(named.warning).isEqualTo("the local master is 3 commits behind and 2 ahead of github/main; keel compares with github/main")
        config(root, "base_branch: github/main\n")
        val written = workspace.baseInfo(root)
        assertThat(written.name to written.local).isEqualTo("main" to null)
        assertThat(written.ref).isEqualTo("github/main")
        assertThat(written.explicit).isTrue()
        assertThat(workspace.base(root)).isEqualTo("master")      // the local base branch for the folder's own checks
    }

    @Test
    fun `a start asks first when base_branch names no local branch that a remote has`() {
        val (pid, root) = twoRemotes("missing-base")
        config(root, "base_branch: main\n")
        val err = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores", "allow_fake" to true))
            .andExpect(status().isConflict).json()
        assertThat(err["error"].asText()).isEqualTo("base_branch main in .keel/config.yml names no local branch here; github/main has it")
        assertThat(err["hint"].asText()).contains("use github/main", "create a local main that tracks github/main", "pick another branch")
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("master")      // nothing was made
        // a start that names its base goes on: from the remote's copy
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores", "allow_fake" to true, "base" to "github/main"))
            .andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "HEAD^").trim()).isNotEqualTo(git(root, "rev-parse", "master").trim())
        assertThat(Files.exists(root.resolve("landed/m3.txt"))).isTrue()
    }

    @Test
    fun `base_branch written as remote branch starts a new branch from that remote's copy`() {
        val (pid, root) = twoRemotes("remote-written-base")
        config(root, "base_branch: github/main\n")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Ranks", "allow_fake" to true)).andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/ranks")
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "github/main").trim())
    }

    @Test
    fun `a local base only behind the canonical remote's copy starts the branch from that copy`() {
        val (pid, root) = twoRemotes("behind-canonical")
        git(root, "checkout", "-q", "-b", "main", "github/main~2")
        config(root, "base_branch: main\ngit:\n  remote: github\n")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Totals", "allow_fake" to true)).andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "github/main").trim())
    }

    @Test
    fun `a flow started on init's keel setup branch builds on it`() {
        val (pid, root) = twoRemotes("on-setup-branch")
        git(root, "checkout", "-q", "-b", "keel/setup", "github/main")
        config(root, "base_branch: main\ngit:\n  remote: github\n")
        git(root, "add", ".keel/config.yml")
        git(root, "commit", "-q", "-m", "chore(setup): keel init")
        git(root, "branch", "main", "github/main")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Coupons", "allow_fake" to true)).andExpect(status().isOk)
        assertThat(git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()).isEqualTo("feat/coupons")
        assertThat(git(root, "rev-parse", "HEAD").trim()).isEqualTo(git(root, "rev-parse", "keel/setup").trim())
        assertThat(Files.exists(root.resolve(".keel/config.yml"))).isTrue()
    }

    @Test
    fun `the base's view and the person's choice go to the engine`() {
        val (pid, _) = twoRemotes("base-endpoints")
        get("/api/projects/$pid/base").andExpect(status().isOk)
        assertThat(engine.calls.any { it.path.startsWith("/base") }).isTrue()
        put("/api/projects/$pid/base", mapOf("choice" to "track")).andExpect(status().isOk)
        assertThat(engine.lastBody("/base")!!["choice"].asText()).isEqualTo("track")
        put("/api/projects/$pid/base", mapOf("choice" to "guess")).andExpect(status().isBadRequest)
    }
}
