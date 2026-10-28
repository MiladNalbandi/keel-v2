package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * keel 0.18.64 the spec check and the citation check on a file of the project (keel_spec_check, keel_citations_check):
 * the api sends the project's folder to the engine (runtime/spec_routes.py, here a stub), and a read MCP token may call
 * both, since they change nothing.
 */
class SpecCheckApiTest : ApiTest() {
    private val routes = listOf("/specs/check", "/citations/check")
    private val finding = mapOf("level" to "fix", "text" to "src/orders.py:88-95 does not exist: the file has 40 lines.")

    @BeforeEach
    fun stub() {
        engine.extraRoutes["/specs/check"] = { b ->
            200 to mapOf("path" to b!!.path("path").asText(), "criteria" to listOf("AC-1"), "findings" to listOf(finding), "fix" to 1)
        }
        engine.extraRoutes["/citations/check"] = { b ->
            200 to mapOf("path" to b!!.path("path").asText(null), "checked" to 1,
                "bad" to listOf(mapOf("cite" to "src/orders.py:88-95", "problem" to "src/orders.py:88-95 does not exist", "level" to "fix")))
        }
    }

    @AfterEach
    fun unstub() = routes.forEach { engine.extraRoutes.remove(it) }

    @Test
    fun `both checks go to the engine with the project's folder`() {
        val (pid, root) = newProject("spec-check")
        val spec = post("/api/projects/$pid/specs/check", mapOf("path" to " docs/specs/orders.md ")).andExpect(status().isOk).json()
        assertThat(spec["fix"].asInt()).isEqualTo(1)
        assertThat(engine.lastBody("/specs/check")!!["root"].asText()).isEqualTo(root.toString())
        assertThat(engine.lastBody("/specs/check")!!["path"].asText()).isEqualTo("docs/specs/orders.md")

        val file = post("/api/projects/$pid/citations/check", mapOf("path" to "docs/specs/orders.md")).andExpect(status().isOk).json()
        assertThat(file["bad"][0]["cite"].asText()).isEqualTo("src/orders.py:88-95")
        assertThat(engine.lastBody("/citations/check")!!["root"].asText()).isEqualTo(root.toString())
        post("/api/projects/$pid/citations/check", mapOf("text" to "see src/orders.py:88")).andExpect(status().isOk)
        assertThat(engine.lastBody("/citations/check")!!["text"].asText()).isEqualTo("see src/orders.py:88")
        assertThat(engine.lastBody("/citations/check")!!.has("path")).isFalse()

        // refused before the engine: no path, both a path and a text, neither, an unknown project
        post("/api/projects/$pid/specs/check", mapOf("path" to " ")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/citations/check", mapOf("path" to "a.md", "text" to "x")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/citations/check", emptyMap<String, Any>()).andExpect(status().isBadRequest)
        post("/api/projects/nope/specs/check", mapOf("path" to "a.md")).andExpect(status().isNotFound)
    }

    @Test
    fun `a read MCP token may run both checks`() {
        val (pid, _) = newProject("spec-check-mcp")
        val mcp = mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "keel_spec_check")
        post("/api/projects/$pid/specs/check", mapOf("path" to "docs/specs/orders.md"), mcp).andExpect(status().isOk)
        post("/api/projects/$pid/citations/check", mapOf("text" to "src/orders.py:88"), mcp).andExpect(status().isOk)
        // a POST that is not a check is still write for that token
        post("/api/projects/$pid/stacks/save", mapOf("yaml" to "name: x\n"), mcp).andExpect(status().isForbidden)
    }
}
