package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** keel's files and Memory: the knowledge base, keel's core (the Code page's keel view shows them; its own endpoints
 *  are the Code plugin's, plugins/code/api RepoApiTest). */
class KeelDocsApiTest : ApiTest() {
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
