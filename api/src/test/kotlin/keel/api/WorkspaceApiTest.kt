package keel.api

import keel.api.common.ProcResult
import keel.api.doctor.WorkspaceDoctor
import keel.api.flow.FlowService
import keel.api.knowledge.KnowledgeService
import keel.api.projects.ProjectService
import keel.api.repo.KeelRules
import keel.api.repo.RepoService
import keel.api.support.ApiTest
import keel.api.workspace.GitWorkspace
import keel.api.workspace.Workspace
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import java.nio.file.Files
import java.nio.file.Path

/**
 * Step 2 (docs/plugins/09-step2-contract.md §4): the git helper is the core Workspace. RepoService (the Code page)
 * delegates to it, and flows, the Doctor, the knowledge base and KeelBot use it directly.
 */
class WorkspaceApiTest : ApiTest() {
    @Autowired lateinit var workspace: Workspace
    @Autowired lateinit var projects: ProjectService
    @Autowired lateinit var rules: KeelRules

    /** Answers the same every time and records each call. */
    private class Recording : Workspace {
        val calls = mutableListOf<String>()
        override fun git(root: Path, vararg args: String, timeout: Long): ProcResult {
            calls += "git ${args.joinToString(" ")} (${timeout}s) in $root"
            return ProcResult(0, "from the workspace", "")
        }
        override fun gitOut(root: Path, vararg args: String): String? = null.also { calls += "gitOut ${args.joinToString(" ")}" }
        override fun base(root: Path): String? = "trunk".also { calls += "base in $root" }
        override fun lastCodeCommit(root: Path): Long? = null
        override fun lastCommitTime(root: Path, rel: String): Long? = null
    }

    private fun repo(branch: String): Path {
        val root = Files.createTempDirectory("keel-ws")
        git(root, "init", "-q", "-b", branch)
        Files.writeString(root.resolve("README.md"), "# ws\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "first")
        return root
    }

    private fun commitAt(root: Path, rel: String, at: String) {
        val f = root.resolve(rel)
        Files.createDirectories(f.parent)
        Files.writeString(f, "changed at $at\n")
        git(root, "add", "-A")
        gitEnv(root, mapOf("GIT_COMMITTER_DATE" to at, "GIT_AUTHOR_DATE" to at), "commit", "-q", "-m", "change $rel")
    }

    @Test
    fun `keel's workspace is plain git in the project folder`() {
        assertThat(workspace).isInstanceOf(GitWorkspace::class.java)
        val root = repo("main")
        assertThat(workspace.git(root, "rev-parse", "--abbrev-ref", "HEAD").out.trim()).isEqualTo("main")
        assertThat(workspace.gitOut(root, "rev-parse", "--abbrev-ref", "HEAD")).isEqualTo("main")
        assertThat(workspace.git(root, "rev-parse", "--verify", "--quiet", "refs/heads/nope").ok).isFalse()
        assertThat(workspace.gitOut(root, "rev-parse", "--verify", "--quiet", "refs/heads/nope")).isNull()
    }

    @Test
    fun `the base is main, else master, else none`() {
        assertThat(workspace.base(repo("main"))).isEqualTo("main")
        assertThat(workspace.base(repo("master"))).isEqualTo("master")
        assertThat(workspace.base(repo("trunk"))).isNull()
    }

    @Test
    fun `the last code commit leaves docs and keel's own files out`() {
        val root = repo("main")
        commitAt(root, "src/App.kt", "2026-01-01T10:00:00Z")
        commitAt(root, "docs/knowledge/domain.md", "2026-02-01T10:00:00Z")
        commitAt(root, ".keel/notes.md", "2026-03-01T10:00:00Z")
        assertThat(workspace.lastCodeCommit(root)).isEqualTo(1767261600L)            // 2026-01-01T10:00:00Z
        assertThat(workspace.lastCommitTime(root, "docs/knowledge/domain.md")).isEqualTo(1769940000L)   // 2026-02-01T10:00:00Z
        assertThat(workspace.lastCommitTime(root, "docs/knowledge/none.md")).isNull()
    }

    @Test
    fun `RepoService runs its git through the Workspace`() {
        val rec = Recording()
        val repo = RepoService(projects, rules, rec)
        val root = Path.of("/somewhere")
        assertThat(repo.base(root)).isEqualTo("trunk")
        assertThat(repo.git(root, "status", "--porcelain", timeout = 5).out).isEqualTo("from the workspace")
        assertThat(repo.git(root, "log").ok).isTrue()
        assertThat(rec.calls).containsExactly(
            "base in /somewhere",
            "git status --porcelain (5s) in /somewhere",
            "git log (10s) in /somewhere",
        )
    }

    @Test
    fun `flows, the Doctor and the knowledge base use the Workspace, not the Code page's RepoService`() {
        // KeelBot too (plugins/keelbot/api tests its HelperService)
        for (c in listOf(FlowService::class.java, WorkspaceDoctor::class.java, KnowledgeService::class.java)) {
            val needs = c.constructors.flatMap { it.parameterTypes.toList() }
            assertThat(needs).describedAs(c.simpleName).contains(Workspace::class.java).doesNotContain(RepoService::class.java)
        }
    }
}
