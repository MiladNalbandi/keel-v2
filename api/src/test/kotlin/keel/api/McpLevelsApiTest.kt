package keel.api

import keel.api.mcp.McpLevelGuard
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.16.1 the api itself refuses an MCP call above the level its token proves. v0.18.1: every caller proves who it is
 *  (auth/LocalAuth); the tests' plain requests carry the internal token (support/SignedIn), as keel's engine does. */
class McpLevelsApiTest : ApiTest() {

    private fun token(level: String): String =
        post("/api/mcp/token", mapOf("level" to level), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk).json()["token"].asText()

    private fun mcp(level: String? = null): Map<String, String> =
        mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "test", "X-Keel-Tool" to "t") + (level?.let { mapOf("X-Keel-Mcp-Token" to token(it)) } ?: emptyMap())

    private val flow = mapOf("workflow_id" to "feature", "title" to "Scores")

    @Test
    fun `a level token comes only with the internal token`() {
        bare.perform(MockMvcRequestBuilders.post("/api/mcp/token").contentType(MediaType.APPLICATION_JSON)
            .content("{\"level\":\"admin\"}")).andExpect(status().isUnauthorized)
        post("/api/mcp/token", mapOf("level" to "admin"), mapOf("X-Keel-Token" to "wrong")).andExpect(status().isForbidden)
        val write = token("write")
        assertThat(write).hasSize(64).isNotEqualTo(token("admin")).isNotEqualTo(token("read"))
        assertThat(token("write")).isEqualTo(write)
        post("/api/mcp/token", mapOf("level" to "root"), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isBadRequest)
    }

    @Test
    fun `an MCP write without a token, or with the read token, is refused`() {
        val (pid, _) = newProject("lv-write")
        val res = post("/api/projects/$pid/flows", flow, mcp()).andExpect(status().isForbidden).json()
        assertThat(res["error"].asText()).contains("MCP write level").contains("keel2 mcp --write")
        post("/api/projects/$pid/flows", flow, mcp("read")).andExpect(status().isForbidden)
        post("/api/projects/$pid/flows", flow, mapOf("X-Keel-Mcp-Token" to "made-up")).andExpect(status().isForbidden)
        post("/api/projects/$pid/flows", flow, mcp("write")).andExpect(status().isOk)
    }

    @Test
    fun `an admin route needs the admin token`() {
        val res = post("/api/projects", mapOf("root" to "/nowhere"), mcp("write")).andExpect(status().isForbidden).json()
        assertThat(res["error"].asText()).contains("keel2 mcp --admin")
        // 0.18.5 stopping a flow is write, as in keel TUI (the MCP tool asks the person first); read still may not
        post("/api/threads/t-stub-1/stop", null, mcp("write")).andExpect(status().isOk)
        post("/api/threads/t-stub-1/stop", null, mcp("read")).andExpect(status().isForbidden)
    }

    @Test
    fun `0_18_64 a fetch of the remotes is read, a flow branch's sync is write`() {
        val (pid, _) = newProject("lv-fetch")
        post("/api/projects/$pid/remotes/fetch", null, mcp("read")).andExpect(status().isOk)
        post("/api/threads/t-stub-1/sync", mapOf("onto" to "main"), mcp("read")).andExpect(status().isForbidden)
    }

    @Test
    fun `keel's own requests (the internal token) are not limited by MCP levels`() {
        val (pid, _) = newProject("lv-web")
        post("/api/projects/$pid/flows", flow).andExpect(status().isOk)
        post("/api/threads/t-stub-1/stop").andExpect(status().isOk)
        get("/api/projects/$pid").andExpect(status().isOk)
    }

    @Test
    fun `actions need the action's level`() {
        val (pid, _) = newProject("lv-actions")
        post("/api/projects/$pid/actions/verify_fast", emptyMap<String, Any>(), mcp()).andExpect(status().isForbidden)
        post("/api/projects/$pid/actions/verify_fast", emptyMap<String, Any>(), mapOf("X-Keel-Mcp-Token" to token("read"))).andExpect(status().isForbidden)
        post("/api/projects/$pid/actions/verify_fast", emptyMap<String, Any>(), mcp("read")).andExpect(status().isForbidden)
        post("/api/projects/$pid/actions/open_pr", emptyMap<String, Any>(), mcp("write")).andExpect(status().isForbidden)
        assertThat(post("/api/projects/$pid/actions/trace", emptyMap<String, Any>()).andReturn().response.status).isNotEqualTo(403)
        assertThat(post("/api/projects/$pid/actions/verify_fast", emptyMap<String, Any>(), mcp("write")).andReturn().response.status).isNotEqualTo(403)
    }

    // the Database plugin's query route at the read level: plugins/db/api DatabaseMcpLevelApiTest

    @Test
    fun `an agent at the read level may ask for a plugin, never install one`() {
        // keel_plugin_request is a read-level tool (step 4): its POST only opens an Inbox request a person approves
        val ask = mapOf("name" to "lv-hello", "reason" to "the flow needs it", "source" to "agent")
        assertThat(post("/api/plugins/requests", ask, mcp()).andReturn().response.status).isNotEqualTo(403)
        assertThat(post("/api/plugins/requests", ask, mcp("read")).andReturn().response.status).isNotEqualTo(403)
        post("/api/plugins/install", mapOf("name" to "lv-hello"), mcp("read")).andExpect(status().isForbidden)
    }

    @Test
    fun `configuration, secrets, sign-in and approvals need the admin token, not write`() {
        val routes = listOf(
            "POST" to "/api/plugins/install", "POST" to "/api/plugins/install-file", "POST" to "/api/plugins/restart",
            "POST" to "/api/plugins/lv-hello/update", "POST" to "/api/plugins/lv-hello/rollback",
            "PUT" to "/api/plugins/sources", "PUT" to "/api/plugins/rules", "PUT" to "/api/plugins/lv-hello", "DELETE" to "/api/plugins/lv-hello",
            "PUT" to "/api/secrets/TOKEN_X", "DELETE" to "/api/secrets/TOKEN_X", "PUT" to "/api/settings/general",
            "PUT" to "/api/connections/github", "POST" to "/api/connections/github/test", "POST" to "/api/logins",
            "POST" to "/api/github/login", "DELETE" to "/api/github", "DELETE" to "/api/github/login/x",
            "POST" to "/api/approvals/q1/decide",
        )
        for ((method, path) in routes) {
            val req = when (method) {
                "POST" -> post(path, emptyMap<String, Any>(), mcp("write"))
                "PUT" -> put(path, emptyMap<String, Any>(), mcp("write"))
                else -> delete(path, mcp("write"))
            }
            assertThat(req.andReturn().response.status).describedAs("$method $path at the write level").isEqualTo(403)
        }
        // the admin level gets past the guard (the route itself may still refuse the empty body)
        assertThat(post("/api/approvals/q1/decide", mapOf("decision" to "deny"), mcp("admin")).andReturn().response.status).isNotEqualTo(403)
        // what the write tools need still works at write
        val (pid, _) = newProject("lv-still-write")
        post("/api/projects/$pid/flows", flow, mcp("write")).andExpect(status().isOk)
    }

    @Test
    fun `the read actions are the engine's`() {
        // Twin of engine tests/test_mcp.py test_read_actions_match_the_api: both pin action_docs.ACTION_LEVELS.
        assertThat(McpLevelGuard.READ_ACTIONS).containsExactlyInAnyOrder("audit", "trace", "trace_strict", "arch", "arch_detect",
            "coverage_report", "lint_scope", "lint_report", "review_lenses")
        assertThat(McpLevelGuard.ADMIN_ACTIONS).containsExactlyInAnyOrder("arch_set", "open_pr")
    }
}
