package keel.api

import keel.api.mcp.McpLevelGuard
import keel.api.support.ApiTest
import keel.api.support.RouteLevels
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * 0.18.64 a non-GET route no list of McpLevelGuard names needs the MCP admin level (until 0.18.63 it was write: an MCP
 * write token could add an MCP server that runs any command). Every route keel maps must be named in one list, so a
 * new route is a choice, never an accident.
 */
class McpRouteLevelsApiTest : ApiTest() {

    @Autowired lateinit var guard: McpLevelGuard

    private fun token(level: String): String =
        post("/api/mcp/token", mapOf("level" to level), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk).json()["token"].asText()

    private fun mcp(level: String): Map<String, String> =
        mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "t", "X-Keel-Mcp-Token" to token(level))

    @Test
    fun `every non-GET route is named in exactly one list`() {
        val routes = RouteLevels.all(wac).filterNot {
            it.pattern == "/api/mcp/token" || McpLevelGuard.ACTION.matches(RouteLevels.sample(it.pattern))
        }
        assertThat(routes).hasSizeGreaterThan(100)
        val unnamed = routes.filter { it.levels.isEmpty() }.map { "${it.method} ${it.pattern} (${it.handler})" }
        assertThat(unnamed).describedAs("routes no list of McpLevelGuard names: add each to READ_POSTS, WRITE or ADMIN").isEmpty()
        val twice = routes.filter { it.levels.size > 1 }.map { "${it.method} ${it.pattern} ${it.levels}" }
        assertThat(twice).describedAs("routes in more than one list").isEmpty()
    }

    @Test
    fun `an unnamed route needs admin`() {
        assertThat(guard.levels("POST", "/api/some-new-route")).isEmpty()
        val req = org.springframework.mock.web.MockHttpServletRequest("POST", "/api/some-new-route")
        assertThat(guard.need(req)).isEqualTo("admin")
    }

    @Test
    fun `what keel's MCP tools send keeps its level`() {
        // engine keel_engine/mcp_*.py: the write tools' routes stay write, the read tools' POSTs read
        val write = listOf(
            "POST /api/projects/p/flows", "POST /api/projects/p/doctor/workspace/exclude", "POST /api/threads/t/stop",
            "POST /api/threads/t/resume", "POST /api/threads/t/mode", "POST /api/threads/t/models", "POST /api/threads/t/rewind",
            "POST /api/threads/t/sync", "PUT /api/threads/t/document", "POST /api/inbox/t/act", "POST /api/projects/p/docs/history/restore",
            "POST /api/projects/p/keel-state/close", "POST /api/projects/p/plugins/ask", "POST /api/projects/p/agents",
            "PUT /api/projects/p/agents/a", "POST /api/projects/p/workflows/import", "POST /api/projects/p/rules/packs",
            "POST /api/projects/p/skills/import", "POST /api/projects/p/skills/market/install", "PUT /api/projects/p/skills/s",
            "POST /api/projects/p/stacks/save", "POST /api/projects/p/stacks/market/install", "PUT /api/projects/p/stacks/s/off",
            "DELETE /api/projects/p/stacks/s",
        )
        val read = listOf(
            "POST /api/diagrams/ascii", "POST /api/projects/p/citations/check", "POST /api/projects/p/specs/check",
            "POST /api/projects/p/remotes/fetch", "POST /api/projects/p/rules/check", "POST /api/projects/p/workflows/check",
            "POST /api/projects/p/stacks/check", "POST /api/plugins/requests", "POST /api/projects/p/db/query",
        )
        val admin = listOf(
            "POST /api/mcp-servers", "PUT /api/mcp-servers/x", "POST /api/mcp-servers/x/test", "DELETE /api/mcp-servers/x",
            "PUT /api/projects/p/mcp-allow", "POST /api/plugin-host/restart", "PUT /api/projects/p/skills/repo",
            "PUT /api/ui/focus", "DELETE /api/projects/p/flows/t", "PUT /api/workflows/w", "PUT /api/projects/p/rules/config",
            "POST /api/projects/p/stacks/s/install", "PUT /api/providers/local/endpoints/e", "POST /api/providers/local/endpoints/e/test",
        )
        for ((list, level) in listOf(write to "write", read to "read", admin to "admin")) {
            for (r in list) {
                val (m, p) = r.split(" ")
                assertThat(guard.levels(m, p)).describedAs(r).containsExactly(level)
            }
        }
    }

    @Test
    fun `an MCP write token cannot add, change or test an MCP server, or change the allowlist`() {
        val (pid, _) = newProject("lv-mcp-servers")
        val spec = mapOf("name" to "lv-any", "command" to "sh", "args" to listOf("-c", "true"))
        post("/api/mcp-servers", spec, mcp("write")).andExpect(status().isForbidden)
        put("/api/mcp-servers/keel", mapOf("enabled" to true), mcp("write")).andExpect(status().isForbidden)
        post("/api/mcp-servers/keel/test", null, mcp("write")).andExpect(status().isForbidden)
        put("/api/projects/$pid/mcp-allow", mapOf("planner" to listOf("x")), mcp("write")).andExpect(status().isForbidden)
        post("/api/plugin-host/restart", null, mcp("write")).andExpect(status().isForbidden)
        // the admin level gets past the guard
        post("/api/mcp-servers", spec, mcp("admin")).andExpect(status().isOk)
        delete("/api/mcp-servers/lv-any", mcp("admin")).andExpect(status().isOk)
    }
}
