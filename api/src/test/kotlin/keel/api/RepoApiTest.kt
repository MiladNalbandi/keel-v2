package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

class RepoApiTest : ApiTest() {

    private val files = mapOf(
        "README.md" to "# demo\n",
        "apps/api/src/main/kotlin/app/Score.kt" to (1..200).joinToString("\n") { "// line $it" },
        "apps/api/src/test/kotlin/app/ScoreTest.kt" to "class ScoreTest\n",
        "docs/specs/scores.md" to "# Scores\n\nAC-001 a\nAC-002 b\n",
        "docs/knowledge/architecture.md" to "# Architecture\n\nLayers live in `apps/api/src/main/kotlin/app/Score.kt:1` and `README.md:1`.\n",
        "docs/adr/ADR-001-graph.md" to "# The flow is a graph\n",
        ".keel/state.json" to """{"flow":"feature","phase":"red","acs":{"AC-001":{"status":"red"},"AC-002":{"status":"done"}}}""",
        ".env" to "SECRET=1\n",
        "node_modules/x/index.js" to "x\n",
    )

    @Test
    fun `repo info, tree, file and commits on a real git repo`() {
        val (pid, root) = newProject("repo-demo", files)
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
        assertThat(byPath[".keel/state.json"]!!["keel"].asBoolean()).isTrue()
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
    fun `keel docs, memory, wiki and map`() {
        val (pid, _) = newProject("repo-knowledge", files)

        val docs = get("/api/projects/$pid/keel-docs").andExpect(status().isOk).json()
        val byPath = docs.associateBy { it["path"].asText() }
        assertThat(byPath["docs/specs/scores.md"]!!["what"].asText()).isEqualTo("Spec — 2 ACs")
        assertThat(byPath[".keel/state.json"]!!["status"].asText()).isEqualTo("live")
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

        val wiki = get("/api/projects/$pid/wiki").json()
        assertThat(wiki["sections"].map { it["id"].asText() }).containsExactly("knowledge", "workflows", "runbook", "decisions")
        val page = get("/api/projects/$pid/wiki/page?id=kb:architecture").json()
        assertThat(page["markdown"].asText()).contains("Layers live in")
        val wf = get("/api/projects/$pid/wiki/page?id=wf:feature").json()
        assertThat(wf["markdown"].asText()).contains("| 2 | spec approval | gate | you |")
        assertThat(get("/api/projects/$pid/wiki/page?id=adr:ADR-001-graph.md").json()["title"].asText()).isEqualTo("The flow is a graph")
        assertThat(get("/api/projects/$pid/wiki/page?id=runbook").json()["markdown"].asText()).contains("How to run")
        get("/api/projects/$pid/wiki/page?id=adr:../../etc").andExpect(status().isBadRequest)

        assertThat(get("/api/projects/$pid/map").json()["missing"].asText()).contains("No map yet")
        // the fixture KEEL_HOME has no bin/keel
        assertThat(post("/api/projects/$pid/map/rebuild").json()["missing"].asText()).contains("not installed")
    }
}
