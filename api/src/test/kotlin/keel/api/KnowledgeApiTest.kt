package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** Plan 5c: each agent chooses the project knowledge it uses (defaults from front matter, project override). */
class KnowledgeApiTest : ApiTest() {

    private val files = mapOf(
        "README.md" to "# demo\n",
        "docs/knowledge/architecture.md" to "a".repeat(40),
        "docs/knowledge/domain.md" to "d".repeat(400),
        "docs/knowledge/conventions.md" to "c".repeat(800),
    )

    private fun agent(pid: String, id: String) = get("/api/projects/$pid/agents").json().first { it["id"].asText() == id }

    @Test
    fun `defaults come from front matter, with the token cost of the sections that exist`() {
        val (pid, _) = newProject("knowledge-defaults", files)
        val ta = agent(pid, "test-author")
        assertThat(ta["knowledge"]["sections"].map { it.asText() }).containsExactly("domain", "conventions")
        assertThat(ta["knowledge"]["code_graph"].asBoolean()).isTrue()
        assertThat(ta["knowledge"]["strict"].asBoolean()).isFalse()
        assertThat(ta["knowledge_tokens"].asInt()).isEqualTo(300)
        assertThat(ta["knowledge_files"].fieldNames().asSequence().toList()).containsExactly("architecture", "domain", "conventions")
        assertThat(ta["knowledge_files"]["domain"].asInt()).isEqualTo(100)
        // no knowledge block: the fallback (architecture + graph)
        val ex = agent(pid, "explorer")
        assertThat(ex["knowledge"]["sections"].map { it.asText() }).containsExactly("architecture")
        assertThat(ex["knowledge_tokens"].asInt()).isEqualTo(10)
    }

    @Test
    fun `a project override round trips, merges over the default and can be cleared`() {
        val (pid, _) = newProject("knowledge-override", files)
        val o = put("/api/projects/$pid/agents/test-author", mapOf("knowledge" to mapOf("sections" to listOf("architecture"), "strict" to true)))
            .andExpect(status().isOk).json()
        assertThat(o["overridden"].map { it.asText() }).contains("knowledge")
        assertThat(o["knowledge"]["sections"].map { it.asText() }).containsExactly("architecture")
        assertThat(o["knowledge"]["strict"].asBoolean()).isTrue()
        assertThat(o["knowledge"]["code_graph"].asBoolean()).isTrue()   // not sent: the default stays
        assertThat(o["knowledge_tokens"].asInt()).isEqualTo(10)
        assertThat(agent(pid, "test-author")["knowledge"]["strict"].asBoolean()).isTrue()

        // the flow start carries it to the engine, and the estimate counts its tokens
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isOk)
        val k = engine.lastBody("/threads")!!["agents"]["test-author"]["knowledge"]
        assertThat(k["sections"].map { it.asText() }).containsExactly("architecture")
        assertThat(k["strict"].asBoolean()).isTrue()
        assertThat(k["code_graph"].asBoolean()).isTrue()
        assertThat(k["hints"].asBoolean()).isFalse()                       // keel's "where to look" lookups: off by default
        assertThat(engine.lastBody("/threads")!!["agents"]["implementer"]["knowledge"]).isNotNull()
        get("/api/projects/$pid/estimate?workflow_id=feature&acs=2").andExpect(status().isOk)
        assertThat(engine.lastBody("/workflows/estimate")!!["knowledge_tokens"]["test-author"].asInt()).isEqualTo(10)
        put("/api/projects/$pid/agents/test-author", mapOf("knowledge" to mapOf("hints" to true))).andExpect(status().isOk)
        assertThat(agent(pid, "test-author")["knowledge"]["hints"].asBoolean()).isTrue()

        val cleared = put("/api/projects/$pid/agents/test-author", mapOf("knowledge" to null)).andExpect(status().isOk).json()
        assertThat(cleared["overridden"].map { it.asText() }).doesNotContain("knowledge")
        assertThat(cleared["knowledge"]["sections"].map { it.asText() }).containsExactly("domain", "conventions")
    }

    @Test
    fun `unknown sections and fields are refused with a clear message`() {
        val (pid, _) = newProject("knowledge-bad", files)
        val e = put("/api/projects/$pid/agents/test-author", mapOf("knowledge" to mapOf("sections" to listOf("architecture", "secrets"))))
            .andExpect(status().isBadRequest).json()
        assertThat(e["error"].asText()).contains("Unknown knowledge section: secrets")
        assertThat(e["hint"].asText()).contains("journeys")
        put("/api/projects/$pid/agents/test-author", mapOf("knowledge" to mapOf("colour" to "red"))).andExpect(status().isBadRequest)
        put("/api/projects/$pid/agents/test-author", mapOf("knowledge" to "all")).andExpect(status().isBadRequest)
    }
}
