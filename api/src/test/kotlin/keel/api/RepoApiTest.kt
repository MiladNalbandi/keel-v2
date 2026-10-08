package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

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
    fun `keel docs and memory`() {
        val (pid, _) = newProject("repo-knowledge", files)

        val docs = get("/api/projects/$pid/keel-docs").andExpect(status().isOk).json()
        val byPath = docs.associateBy { it["path"].asText() }
        assertThat(byPath["docs/specs/scores.md"]!!["what"].asText()).isEqualTo("Spec — 2 ACs")
        assertThat(byPath[".keel/config.yml"]!!["what"].asText()).isEqualTo("Project config")
        assertThat(byPath.keys).contains("docs/knowledge/", "docs/adr/ADR-001-graph.md")

        val memory = get("/api/projects/$pid/memory").json()
        val arch = memory["knowledge"].first { it["id"].asText() == "architecture" }
        assertThat(arch["status"].asText()).isEqualTo("written")
        assertThat(arch["cites"].asInt()).isEqualTo(2)
        assertThat(memory["knowledge"].first { it["id"].asText() == "domain" }["status"].asText()).isEqualTo("missing")

        val fact = post("/api/projects/$pid/memory", mapOf("title" to "Test needs Docker", "text" to "Start Docker first.", "kind" to "fact"))
            .andExpect(status().isOk).json()
        assertThat(fact["source"].asText()).isEqualTo("you")
        val fid = fact["id"].asText()
        put("/api/projects/$pid/memory/$fid", mapOf("kind" to "rule")).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/memory").json()["facts"][0]["kind"].asText()).isEqualTo("rule")
        post("/api/projects/$pid/memory", mapOf("title" to "x", "text" to "y", "kind" to "gossip")).andExpect(status().isBadRequest)
        delete("/api/projects/$pid/memory/$fid").andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/memory").json()["facts"].size()).isEqualTo(0)

        // the wiki is the Wiki plugin's (plugins/wiki/api: WikiApiTest), the map the Map plugin's (plugins/map/api: MapApiTest)
    }
}
