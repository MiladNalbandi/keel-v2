package keel.api.plugins

import keel.api.mcp.McpTokens
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * #96 Access "private" (Results only to you) and the switch for keel's MCP server: an MCP client's query on a private
 * connection asks the engine for a private result (an id, never the rows); Settings › Databases › MCP clients keeps every
 * MCP call to private or to nothing; only the person changes that switch and reads the results.
 */
class DatabasePrivateApiTest : ApiTest() {

    @Autowired lateinit var tokens: McpTokens

    private val via = mapOf("X-Keel-Via" to "mcp", "X-Keel-Client" to "Claude Code", "X-Keel-Tool" to "keel_db_query")
    private fun mcp(level: String) = via + mapOf("X-Keel-Mcp-Token" to tokens.tokenFor(level))
    private fun mcpGet(url: String) = mvc.perform(MockMvcRequestBuilders.get(url).also { b -> mcp("admin").forEach { (k, v) -> b.header(k, v) } })

    @AfterEach
    fun connectionAccessAgain() {
        put("/api/databases/mcp", mapOf("access" to "connection")).andExpect(status().isOk)
    }

    private fun project(name: String): String {
        val (pid, _) = newProject(name)
        put("/api/projects/$pid/plugins/db", mapOf("enabled" to true)).andExpect(status().isOk)
        return pid
    }

    private fun connection(pid: String, name: String, access: String, env: String = "local") =
        post("/api/projects/$pid/databases", mapOf("name" to name, "env" to env, "driver" to "sqlite", "default_db" to "$name.db",
            "agent_access" to access)).andExpect(status().isOk).json()

    @Test
    fun `an MCP query on a private connection asks the engine for a private result`() {
        val pid = project("priv-q")
        val made = connection(pid, "crm", "private", env = "prod")
        assertThat(made["agent_access"].asText()).isEqualTo("private")
        assertThat(made["agent_access_effective"].asText()).isEqualTo("private")

        val answer = post("/api/projects/$pid/db/query", mapOf("sql" to "select email from users", "connection" to "crm"), via)
            .andExpect(status().isOk).json()
        assertThat(answer["private"].asBoolean()).isTrue()
        assertThat(answer.has("rows")).isFalse()
        val sent = engine.lastBody("/plugins/db/query")!!
        assertThat(sent["private"].asBoolean()).isTrue()
        assertThat(sent["project"].asText()).isEqualTo(pid)
        assertThat(sent["who"].asText()).isEqualTo("Claude Code")
        assertThat(sent["mask"].asBoolean()).isTrue()

        // the person's own query on it shows the rows (it is not an agent's)
        post("/api/projects/$pid/db/query", mapOf("sql" to "select email from users", "connection" to "crm")).andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/db/query")!!["private"].asBoolean()).isFalse()

        // an agent never changes data there, at any MCP level
        val refused = post("/api/projects/$pid/db/query", mapOf("sql" to "delete from users", "connection" to "crm", "change" to true),
            mcp("write")).andExpect(status().isForbidden).json()
        assertThat(refused["error"].asText()).contains("agents only run reads, and the results go to the person")
    }

    @Test
    fun `an unknown Access is refused`() {
        val pid = project("priv-bad")
        post("/api/projects/$pid/databases", mapOf("name" to "x", "driver" to "sqlite", "default_db" to "x.db", "agent_access" to "secret"))
            .andExpect(status().isBadRequest)
    }

    @Test
    fun `the MCP switch keeps every MCP call to private results or to no database, and only the person sets it`() {
        val pid = project("priv-cap")
        connection(pid, "ro", "read")
        connection(pid, "rw", "write_approve")
        assertThat(get("/api/databases/mcp").json()["access"].asText()).isEqualTo("connection")

        put("/api/databases/mcp", mapOf("access" to "private"), mcp("admin")).andExpect(status().isForbidden)
        put("/api/databases/mcp", mapOf("access" to "everything")).andExpect(status().isBadRequest)
        put("/api/databases/mcp", mapOf("access" to "private")).andExpect(status().isOk)

        post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "ro"), via).andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/db/query")!!["private"].asBoolean()).isTrue()
        // an MCP client's list says what it really may do there
        val seen = mcpGet("/api/projects/$pid/databases").json().associate { it["name"].asText() to it["agent_access_effective"].asText() }
        assertThat(seen).containsEntry("ro", "private").containsEntry("rw", "private")
        assertThat(get("/api/projects/$pid/databases").json().first { it["name"].asText() == "ro" }["agent_access_effective"].asText())
            .isEqualTo("read")                                                    // the person sees the connection's own
        post("/api/projects/$pid/db/query", mapOf("sql" to "update t set a = 1", "connection" to "rw", "change" to true), mcp("write"))
            .andExpect(status().isForbidden)
        // keel's own agents (the keel-db server) still follow each connection's Access
        assertThat(flowsKeys(pid)["db:ro"]).contains("\"agent_access\":\"read\"")

        put("/api/databases/mcp", mapOf("access" to "none")).andExpect(status().isOk)
        assertThat(mcpGet("/api/projects/$pid/databases").andExpect(status().isOk).json().size()).isZero()
        val off = post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "ro"), via)
            .andExpect(status().isForbidden).json()
        assertThat(off["error"].asText()).isEqualTo("Databases are off for MCP clients")
        mcpGet("/api/projects/$pid/db/schema?connection=ro").andExpect(status().isForbidden)
        // the person still queries
        post("/api/projects/$pid/db/query", mapOf("sql" to "select 1", "connection" to "ro")).andExpect(status().isOk)
    }

    @Test
    fun `only the person reads and forgets private results`() {
        val pid = project("priv-res")
        mcpGet("/api/projects/$pid/db/results").andExpect(status().isForbidden)
        mcpGet("/api/projects/$pid/db/results/r_000000000001").andExpect(status().isForbidden)
        delete("/api/projects/$pid/db/results/r_000000000001", mcp("admin")).andExpect(status().isForbidden)

        assertThat(get("/api/projects/$pid/db/results").andExpect(status().isOk).json()[0]["id"].asText()).isEqualTo("r_000000000001")
        assertThat(engine.lastBody("/plugins/db/results")!!["project"].asText()).isEqualTo(pid)
        assertThat(get("/api/projects/$pid/db/results/r_000000000001").andExpect(status().isOk).json()["rows"][0][0].asInt()).isEqualTo(1)
        get("/api/projects/$pid/db/results/r_gone").andExpect(status().isNotFound)
        delete("/api/projects/$pid/db/results/r_000000000001").andExpect(status().isOk)
    }

    @Autowired lateinit var flows: keel.api.flow.FlowService
    private fun flowsKeys(pid: String) = flows.contributedKeys(pid)
}
