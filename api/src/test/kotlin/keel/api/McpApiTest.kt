package keel.api

import keel.api.common.KvStore
import keel.api.mcp.McpService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

class McpApiTest : ApiTest() {
    @Autowired lateinit var mcp: McpService
    @Autowired lateinit var jdbc: JdbcTemplate
    @Autowired lateinit var kv: KvStore

    @Test
    fun `the builtin keel server is keel v2's own MCP server, read-only and locked`() {
        val keel = get("/api/mcp-servers").json().first { it["name"].asText() == "keel" }
        assertThat(keel["builtin"].asBoolean()).isTrue()
        assertThat(keel["enabled"].asBoolean()).isTrue()
        assertThat(keel["command"].asText()).matches(".*python3?")
        assertThat(keel["args"].map { it.asText() }).containsExactly("-m", "keel_engine.mcp", "--read-only")
        put("/api/mcp-servers/keel", mapOf("args" to listOf("-m", "keel_engine.mcp", "--write"))).andExpect(status().isConflict)
        delete("/api/mcp-servers/keel").andExpect(status().isConflict)

        // An install that still points keel at keel v1's server.js is switched over on the next start.
        jdbc.update("UPDATE mcp_servers SET command = 'node', args_json = '[\"/old/keel/mcp/server.js\"]' WHERE name = 'keel'")
        mcp.seed()
        assertThat(mcp.get("keel").args).containsExactly("-m", "keel_engine.mcp", "--read-only")
    }

    @Test
    fun `keel v1's MCP server is offered, off, only when a checkout is mounted`() {
        assertThat(get("/api/mcp-servers").json().map { it["name"].asText() }).doesNotContain("keel-v1")

        val dir = Files.createTempDirectory("keel-v1-optional")
        Files.createDirectories(dir.resolve("mcp"))
        Files.writeString(dir.resolve("mcp/server.js"), "// keel v1")
        mcp.seedKeelV1(dir)
        val v1 = get("/api/mcp-servers").json().first { it["name"].asText() == "keel-v1" }
        assertThat(v1["enabled"].asBoolean()).isFalse()
        assertThat(v1["builtin"].asBoolean()).isFalse()
        assertThat(v1["label"].asText()).isEqualTo("keel v1 (optional)")
        assertThat(v1["command"].asText()).isEqualTo("node")
        assertThat(v1["args"][0].asText()).isEqualTo(dir.resolve("mcp/server.js").toString())
        // It is a server like any other: it can be turned on and given to agents.
        put("/api/mcp-servers/keel-v1", mapOf("enabled" to true)).andExpect(status().isOk)
        assertThat(mcp.specsFor(listOf("keel", "keel-v1")).map { it.name }).containsExactly("keel", "keel-v1")
        put("/api/mcp-servers/keel-v1", mapOf("enabled" to false)).andExpect(status().isOk)

        // Mount gone: the untouched offer goes away.
        Files.delete(dir.resolve("mcp/server.js"))
        mcp.seedKeelV1(dir)
        assertThat(get("/api/mcp-servers").json().map { it["name"].asText() }).doesNotContain("keel-v1")

        // Removed by the user: not offered again.
        Files.writeString(dir.resolve("mcp/server.js"), "// keel v1")
        mcp.seedKeelV1(dir)
        delete("/api/mcp-servers/keel-v1").andExpect(status().isOk)
        mcp.seedKeelV1(dir)
        assertThat(get("/api/mcp-servers").json().map { it["name"].asText() }).doesNotContain("keel-v1")
        kv.delete("mcp-keel-v1-dismissed")
    }

}
