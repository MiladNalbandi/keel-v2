package keel.api.wiki

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** The Wiki plugin's api part: the wiki's pages and the knowledge refresh (moved from RepoApiTest and V02RepoApiTest). */
class WikiApiTest : ApiTest() {

    @Test
    fun `the wiki lists the knowledge base, the workflows, the runbook and the decisions, and reads each page`() {
        val (pid, _) = newProject(
            "repo-knowledge",
            mapOf(
                "README.md" to "# demo\n",
                "apps/api/src/main/kotlin/app/Score.kt" to (1..200).joinToString("\n") { "// line $it" },
                "docs/knowledge/architecture.md" to "# Architecture\n\nLayers live in `apps/api/src/main/kotlin/app/Score.kt:1` and `README.md:1`.\n",
                "docs/adr/ADR-001-graph.md" to "# The flow is a graph\n",
                ".keel/config.yml" to "commands: {}\n",
            ),
        )
        val wiki = get("/api/projects/$pid/wiki").json()
        assertThat(wiki["sections"].map { it["id"].asText() }).containsExactly("knowledge", "workflows", "runbook", "decisions")
        val knowledge = wiki["sections"][0]["items"]
        assertThat(knowledge.first { it["id"].asText() == "kb:architecture" }["status"].asText()).isEqualTo("written")
        assertThat(knowledge.first { it["id"].asText() == "kb:domain" }["status"].asText()).isEqualTo("missing")
        val page = get("/api/projects/$pid/wiki/page?id=kb:architecture").json()
        assertThat(page["markdown"].asText()).contains("Layers live in")
        assertThat(page["meta"]["cites"].asInt()).isEqualTo(2)
        val wf = get("/api/projects/$pid/wiki/page?id=wf:feature").json()
        assertThat(wf["markdown"].asText()).contains("| 2 | spec approval | gate | you |")
        assertThat(get("/api/projects/$pid/wiki/page?id=adr:ADR-001-graph.md").json()["title"].asText()).isEqualTo("The flow is a graph")
        assertThat(get("/api/projects/$pid/wiki/page?id=runbook").json()["markdown"].asText()).contains("How to run")
        get("/api/projects/$pid/wiki/page?id=adr:../../etc").andExpect(status().isBadRequest)
        get("/api/projects/$pid/wiki/page?id=gossip").andExpect(status().isNotFound)
    }

    @Test
    fun `wiki refresh starts the knowledge-refresh template with one AC per stale section`() {
        val (pid, root) = newProject("v2-wiki", mapOf("docs/knowledge/architecture.md" to "# A\n", "docs/knowledge/domain.md" to "# D\n", "src/A.kt" to "class A"))
        post("/api/projects/$pid/wiki/refresh", emptyMap<String, Any>()).andExpect(status().isBadRequest)

        Files.writeString(root.resolve("src/A.kt"), "class A2")
        gitEnv(root, mapOf("GIT_COMMITTER_DATE" to "2099-01-01T00:00:00Z", "GIT_AUTHOR_DATE" to "2099-01-01T00:00:00Z"), "commit", "-q", "-am", "code moved on")
        assertThat(get("/api/projects/$pid/memory").json()["knowledge"].first { it["id"].asText() == "architecture" }["status"].asText()).isEqualTo("stale")

        val state = post("/api/projects/$pid/wiki/refresh").andExpect(status().isOk).json()
        assertThat(state["thread_id"].asText()).isEqualTo("t-stub-1")
        val body = engine.lastBody("/threads")!!
        assertThat(body["workflow"]["id"].asText()).isEqualTo("knowledge-refresh")
        assertThat(body["acs"].map { it["id"].asText() }).containsExactly("architecture", "domain")
        assertThat(body["acs"][0]["layer"].asText()).isEqualTo("API")
        assertThat(body["title"].asText()).contains("architecture")

        post("/api/projects/$pid/wiki/refresh", mapOf("sections" to listOf("domain"))).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads")!!["acs"].map { it["title"].asText() }).containsExactly("domain")
        post("/api/projects/$pid/wiki/refresh", mapOf("sections" to listOf("gossip"))).andExpect(status().isBadRequest)
    }

    @Test
    fun `an unknown project is not found`() {
        get("/api/projects/nope/wiki").andExpect(status().isNotFound)
        get("/api/projects/nope/wiki/page?id=runbook").andExpect(status().isNotFound)
        post("/api/projects/nope/wiki/refresh").andExpect(status().isNotFound)
    }
}
