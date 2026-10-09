package keel.api.repo

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** The Code page's endpoints (plugins/code/api, keel.api.repo.RepoController): the repo's info, tree, files, commits,
 *  update from base, a file's history and a branch's diffs, on a real git repo. Moved from keel's api tests with the
 *  plugin; keel's files and Memory stay core (KeelDocsApiTest). */
class RepoApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    /** An active (waiting) flow of the project, as the api keeps it (the engine's events write these rows). */
    private fun activeFlow(pid: String, phase: String, acs: String) = jdbc.update(
        "INSERT INTO threads(id, project_id, workflow_id, title, status, phase, state_json, created_at, updated_at) VALUES (?, ?, 'feature', 'x', 'waiting', ?, ?, ?, ?)",
        "t-$pid", pid, phase, """{"acs": $acs}""", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
    )

    private val files = mapOf(
        "README.md" to "# demo\n",
        "apps/api/src/main/kotlin/app/Score.kt" to (1..200).joinToString("\n") { "// line $it" },
        "apps/api/src/test/kotlin/app/ScoreTest.kt" to "class ScoreTest\n",
        "docs/specs/scores.md" to "# Scores\n\nAC-001 a\nAC-002 b\n",
        "docs/knowledge/architecture.md" to "# Architecture\n\nLayers live in `apps/api/src/main/kotlin/app/Score.kt:1` and `README.md:1`.\n",
        "docs/adr/ADR-001-graph.md" to "# The flow is a graph\n",
        ".keel/config.yml" to "commands: {}\n",
        ".env" to "SECRET=1\n",
        "node_modules/x/index.js" to "x\n",
    )

    @Test
    fun `repo info, tree, file and commits on a real git repo`() {
        val (pid, root) = newProject("repo-demo", files)
        // no flow yet: nothing is frozen and the project has no phase (keel v1's state file is not read since 0.4.1)
        val v1State = root.resolve(".keel").resolve("state.json")
        Files.writeString(v1State, """{"flow":"feature","phase":"red"}""")
        assertThat(get("/api/projects/$pid").json()["phase"].asText()).isEqualTo("none")
        assertThat(get("/api/projects/$pid/repo/file?path=apps/api/src/main/kotlin/app/Score.kt").json()["frozen"].asBoolean()).isFalse()
        Files.delete(v1State)
        activeFlow(pid, "red", """[{"id": "AC-001", "status": "red"}, {"id": "AC-002", "status": "done"}]""")
        git(root, "checkout", "-q", "-b", "feat/scores")
        Files.writeString(root.resolve("apps/api/src/main/kotlin/app/New.kt"), "class New\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "add New")
        Files.writeString(root.resolve("README.md"), "# changed\n")
        Files.writeString(root.resolve("untracked.txt"), "u\n")

        val info = get("/api/projects/$pid/repo").andExpect(status().isOk).json()
        assertThat(info["branch"].asText()).isEqualTo("feat/scores")
        assertThat(info["base"].asText()).isEqualTo("main")
        assertThat(info["ahead"].asInt()).isEqualTo(1)
        assertThat(info["behind"].asInt()).isEqualTo(0)
        assertThat(info["remote"].isNull).isTrue()
        assertThat(info["branches"].map { it["name"].asText() }).contains("main", "feat/scores")
        assertThat(info["worktrees"].size()).isEqualTo(1)

        val tree = get("/api/projects/$pid/repo/tree?depth=8").andExpect(status().isOk).json()
        val byPath = tree.associateBy { it["path"].asText() }
        assertThat(byPath.keys).doesNotContain("node_modules", ".git")
        assertThat(byPath["README.md"]!!["mark"].asText()).isEqualTo("M")
        assertThat(byPath["untracked.txt"]!!["mark"].asText()).isEqualTo("A")
        assertThat(byPath["docs/specs/scores.md"]!!["keel"].asBoolean()).isTrue()
        assertThat(byPath[".keel/config.yml"]!!["keel"].asBoolean()).isTrue()
        assertThat(byPath["README.md"]!!["keel"].asBoolean()).isFalse()
        // phase red: production code is frozen, tests are not
        assertThat(byPath["apps/api/src/main/kotlin/app/Score.kt"]!!["frozen"].asBoolean()).isTrue()
        assertThat(byPath["apps/api/src/test/kotlin/app/ScoreTest.kt"]!!["frozen"].asBoolean()).isFalse()
        assertThat(byPath["apps"]!!["kind"].asText()).isEqualTo("dir")
        assertThat(byPath["apps"]!!["depth"].asInt()).isEqualTo(1)

        val shallow = get("/api/projects/$pid/repo/tree?depth=1").json()
        assertThat(shallow.all { it["depth"].asInt() == 1 }).isTrue()

        val file = get("/api/projects/$pid/repo/file?path=apps/api/src/main/kotlin/app/Score.kt").andExpect(status().isOk).json()
        assertThat(file["head"].asText().lines()).hasSize(120)
        assertThat(file["frozen"].asBoolean()).isTrue()
        assertThat(file["last_commit"]["message"].asText()).isEqualTo("first commit")

        val commits = get("/api/projects/$pid/repo/commits?limit=10").json()
        assertThat(commits.map { it["message"].asText() }).containsExactly("add New", "first commit")
        assertThat(commits[0]["author"].asText()).isEqualTo("Test")

        val project = get("/api/projects/$pid").json()
        assertThat(project["branch"].asText()).isEqualTo("feat/scores")
        assertThat(project["phase"].asText()).isEqualTo("red")
        assertThat(project["flow"].asText()).isEqualTo("feature")
        assertThat(project["acs"].map { it.asInt() }).containsExactly(1, 2)
    }

    @Test
    fun `paths outside the root and secret files are refused`() {
        val (pid, root) = newProject("repo-safe", files)
        val outside = root.parent.resolve("outside.txt")
        Files.writeString(outside, "nope")
        get("/api/projects/$pid/repo/file?path=../outside.txt").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/file?path=apps/../../outside.txt").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/file?path=$outside").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/file?path=/etc/passwd").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/file?path=.env").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/file?path=.git/config").andExpect(status().isForbidden)
        val e = get("/api/projects/$pid/repo/file?path=../outside.txt").json()
        assertThat(e["error"].asText()).isEqualTo("That path is outside the project")

        // a symlink that points outside
        Files.createSymbolicLink(root.resolve("link.txt"), outside)
        get("/api/projects/$pid/repo/file?path=link.txt").andExpect(status().isForbidden)

        get("/api/projects/$pid/repo/file?path=nope.txt").andExpect(status().isNotFound)
        get("/api/projects/nope/repo").andExpect(status().isNotFound)
    }

    @Test
    fun `update from base merges main into the branch`() {
        val (pid, root) = newProject("v2-merge", mapOf("README.md" to "# demo\n", "a.txt" to "a\n"))
        git(root, "checkout", "-q", "-b", "feat/x")
        Files.writeString(root.resolve("b.txt"), "b\n")
        git(root, "add", "-A"); git(root, "commit", "-q", "-m", "b on feat")
        git(root, "checkout", "-q", "main")
        Files.writeString(root.resolve("c.txt"), "c\n")
        git(root, "add", "-A"); git(root, "commit", "-q", "-m", "c on main")
        git(root, "checkout", "-q", "feat/x")

        val r = post("/api/projects/$pid/repo/update-from-base").andExpect(status().isOk).json()
        assertThat(r["ok"].asBoolean()).isTrue()
        assertThat(r["merged"].asBoolean()).isTrue()
        assertThat(r["conflicts"].size()).isEqualTo(0)
        assertThat(Files.exists(root.resolve("c.txt"))).isTrue()
        assertThat(get("/api/projects/$pid/repo").json()["behind"].asInt()).isEqualTo(0)

        val again = post("/api/projects/$pid/repo/update-from-base").json()
        assertThat(again["ok"].asBoolean()).isTrue()
        assertThat(again["merged"].asBoolean()).isFalse()

        git(root, "checkout", "-q", "main")
        post("/api/projects/$pid/repo/update-from-base").andExpect(status().isBadRequest)
    }

    @Test
    fun `a conflict aborts the merge and names the files`() {
        val (pid, root) = newProject("v2-conflict", mapOf("README.md" to "# demo\n"))
        git(root, "checkout", "-q", "-b", "feat/y")
        Files.writeString(root.resolve("README.md"), "# feature\n")
        git(root, "commit", "-q", "-am", "feature readme")
        git(root, "checkout", "-q", "main")
        Files.writeString(root.resolve("README.md"), "# main\n")
        git(root, "commit", "-q", "-am", "main readme")
        git(root, "checkout", "-q", "feat/y")

        val r = post("/api/projects/$pid/repo/update-from-base").andExpect(status().isOk).json()
        assertThat(r["ok"].asBoolean()).isFalse()
        assertThat(r["merged"].asBoolean()).isFalse()
        assertThat(r["conflicts"].map { it.asText() }).containsExactly("README.md")
        assertThat(r["output"].asText()).contains("CONFLICT")
        assertThat(Files.exists(root.resolve(".git/MERGE_HEAD"))).isFalse()
        assertThat(Files.readString(root.resolve("README.md"))).isEqualTo("# feature\n")
        assertThat(git(root, "status", "--porcelain")).isBlank()
    }

    @Test
    fun `file history follows renames and refuses outside paths`() {
        val (pid, root) = newProject("v2-history", mapOf("old.txt" to "one\n"))
        git(root, "mv", "old.txt", "new.txt")
        git(root, "commit", "-q", "-m", "rename")
        Files.writeString(root.resolve("new.txt"), "two\n")
        git(root, "commit", "-q", "-am", "edit")

        val h = get("/api/projects/$pid/repo/history?path=new.txt").andExpect(status().isOk).json()
        assertThat(h.map { it["message"].asText() }).containsExactly("edit", "rename", "first commit")
        assertThat(h[0]["author"].asText()).isEqualTo("Test")
        assertThat(h[0]["sha"].asText()).hasSize(40)
        get("/api/projects/$pid/repo/history?path=../x").andExpect(status().isForbidden)
        assertThat(get("/api/projects/$pid/repo/history?path=never.txt").json().size()).isEqualTo(0)
    }

    @Test
    fun `a branch's file diff is against its base, never another ref`() {
        val (pid, root) = newProject("repo-branch", mapOf("README.md" to "# demo\n", "Score.kt" to "class Score(val v: Int)\n"))
        git(root, "checkout", "-q", "-b", "feat/test")
        Files.writeString(root.resolve("Score.kt"), "class Score(val v: Long)\n")
        Files.writeString(root.resolve("new.txt"), "hello\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "feat: scores are Long")
        git(root, "checkout", "-q", "main")
        Files.writeString(root.resolve("README.md"), "# demo 2\n")
        git(root, "commit", "-q", "-am", "docs: readme")      // on main only: not part of the branch's changes

        val d = get("/api/projects/$pid/repo/diff?path=Score.kt&branch=feat/test").andExpect(status().isOk).json()
        assertThat(d["against"].asText()).isEqualTo("branch")
        assertThat(d["ref"].asText()).isEqualTo("main…feat/test")
        assertThat(d["diff"].asText()).contains("-class Score(val v: Int)", "+class Score(val v: Long)")
        // a file only the branch has, and a file the branch did not touch
        assertThat(get("/api/projects/$pid/repo/diff?path=new.txt&branch=feat/test").json()["diff"].asText()).contains("+hello")
        assertThat(get("/api/projects/$pid/repo/diff?path=README.md&branch=feat/test").json()["diff"].asText()).isEmpty()
        get("/api/projects/$pid/repo/diff?path=Score.kt&branch=HEAD~1").andExpect(status().isNotFound)
    }
}
