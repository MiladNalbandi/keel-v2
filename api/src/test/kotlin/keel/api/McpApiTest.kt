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

    @Test
    fun `0_18_64 a typed env value becomes a secret and GET never shows it`(@Autowired secrets: keel.api.connections.SecretService) {
        val spec = mapOf("name" to "envtest", "command" to "npx", "args" to listOf("demo"),
            "env" to mapOf("API_TOKEN" to "fake-token-123", "MODE" to "fast", "EMPTY" to ""))
        val made = post("/api/mcp-servers", spec).andExpect(status().isOk).json()
        assertThat(made["env"]["API_TOKEN"].asText()).isEqualTo("secret:envtest_API_TOKEN")
        assertThat(made.toString()).doesNotContain("fake-token-123").doesNotContain("fast")
        assertThat(secrets.get("envtest_API_TOKEN")).isEqualTo("fake-token-123")
        val listed = get("/api/mcp-servers").json().first { it["name"].asText() == "envtest" }
        assertThat(listed.toString()).doesNotContain("fake-token-123")
        // a value saved before 0.18.64 (plain in the table) shows as "set"; sent back, it is kept
        jdbc.update("UPDATE mcp_servers SET env_json = ? WHERE name = 'envtest'", """{"OLD":"fake-old-value","API_TOKEN":"secret:envtest_API_TOKEN"}""")
        assertThat(get("/api/mcp-servers").json().first { it["name"].asText() == "envtest" }["env"]["OLD"].asText()).isEqualTo("set")
        put("/api/mcp-servers/envtest", mapOf("env" to mapOf("OLD" to "set", "API_TOKEN" to "secret:envtest_API_TOKEN"))).andExpect(status().isOk)
        assertThat(secrets.get("envtest_OLD")).isEqualTo("fake-old-value")
        // the engine gets the value when the server is tested
        post("/api/mcp-servers/envtest/test").andExpect(status().isOk)
        assertThat(engine.lastBody("/mcp/tools")!!["env"]["API_TOKEN"].asText()).isEqualTo("fake-token-123")
        delete("/api/mcp-servers/envtest").andExpect(status().isOk)
    }

    @Test
    fun `0_18_64 secret refs resolve only when attached to the server, never keel's own logins`(
        @Autowired secrets: keel.api.connections.SecretService,
    ) {
        secrets.put("ANTHROPIC_API_KEY", "sk-ant-fake-key")
        secrets.put("SHARED_TOKEN", "fake-shared")
        // naming keel's own login is refused when saved
        val own = mapOf("name" to "steal", "command" to "sh", "env" to mapOf("K" to "secret:ANTHROPIC_API_KEY"))
        post("/api/mcp-servers", own).andExpect(status().isBadRequest)
        post("/api/mcp-servers", own + ("env" to mapOf("K" to "secret:GITHUB_TOKEN"))).andExpect(status().isBadRequest)
        // the person saves a server that names a secret: it is attached and resolves
        post("/api/mcp-servers", mapOf("name" to "mine", "command" to "sh", "env" to mapOf("T" to "secret:SHARED_TOKEN"))).andExpect(status().isOk)
        assertThat(mcp.attached("mine")).containsExactly("SHARED_TOKEN")
        post("/api/mcp-servers/mine/test").andExpect(status().isOk)
        assertThat(engine.lastBody("/mcp/tools")!!["env"]["T"].asText()).isEqualTo("fake-shared")
        // a ref written into the table by other means (not attached), or keel's own login, stays empty
        jdbc.update("UPDATE mcp_servers SET env_json = ? WHERE name = 'mine'",
            """{"T":"secret:SHARED_TOKEN","X":"secret:OTHER_ONE","K":"secret:ANTHROPIC_API_KEY"}""")
        secrets.put("OTHER_ONE", "fake-other")
        post("/api/mcp-servers/mine/test").andExpect(status().isOk)
        val env = engine.lastBody("/mcp/tools")!!["env"]
        assertThat(env["T"].asText()).isEqualTo("fake-shared")
        assertThat(env["X"].asText()).isEmpty()
        assertThat(env["K"].asText()).isEmpty()
        delete("/api/mcp-servers/mine").andExpect(status().isOk)
        secrets.delete("ANTHROPIC_API_KEY")
        assertThat(secrets.keelOwn("CLAUDE_CODE_OAUTH_TOKEN")).isTrue()
        assertThat(secrets.keelOwn("local.ollama")).isTrue()
        assertThat(secrets.keelOwn("SHARED_TOKEN")).isFalse()
    }

}
