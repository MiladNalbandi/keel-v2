package keel.api

import keel.api.auth.WebKey
import keel.api.common.KvStore
import keel.api.help.StartHereController
import keel.api.mcp.McpClients
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** keel 0.18.66 Help › Start here (its five steps), the first-run welcome, the AI tools keel saw, Settings › Access. */
class StartHereApiTest : ApiTest() {
    @Autowired lateinit var clients: McpClients
    @Autowired lateinit var kv: KvStore
    @Autowired lateinit var key: WebKey

    @BeforeEach
    fun fresh() {
        clients.clear()
        kv.delete(StartHereController.KEY)
        put("/api/devices/addresses", mapOf("urls" to emptyList<Any>())).andExpect(status().isOk)
    }

    private fun token(level: String): String =
        post("/api/mcp/token", mapOf("level" to level), mapOf("X-Keel-Token" to TOKEN)).json()["token"].asText()

    @Test
    fun `an MCP read call counts - a GET too - with its client and level, an agent's server does not`() {
        assertThat(get("/api/mcp/clients").json()["clients"]).isEmpty()
        val read = token("read")
        mvc.perform(MockMvcRequestBuilders.get("/api/projects").header("X-Keel-Mcp-Token", read).header("X-Keel-Client", "claude-code"))
            .andExpect(status().isOk)
        mvc.perform(MockMvcRequestBuilders.get("/api/projects").header("X-Keel-Mcp-Token", read).header("X-Keel-Client", "claude-code"))
            .andExpect(status().isOk)
        // keel_health: an open route, the token still says MCP
        mvc.perform(MockMvcRequestBuilders.get("/api/health").header("X-Keel-Mcp-Token", token("write")).header("X-Keel-Client", "opencode"))
            .andExpect(status().isOk)
        // keel's own agents' server
        mvc.perform(MockMvcRequestBuilders.get("/api/projects").header("X-Keel-Mcp-Token", read).header("X-Keel-Client", "claude-code")
            .header("X-Keel-Mcp-Kind", "agent")).andExpect(status().isOk)
        // a refused call is a connected tool too
        post("/api/projects", mapOf("root" to "/nowhere"), mapOf("X-Keel-Via" to "mcp", "X-Keel-Mcp-Token" to read, "X-Keel-Client" to "codex-mcp-client"))
            .andExpect(status().isForbidden)
        clients.flush()
        val list = get("/api/mcp/clients").json()["clients"]
        val byName = list.associateBy { it["client"].asText() + " " + it["level"].asText() }
        assertThat(byName.keys).containsExactlyInAnyOrder("claude-code read", "opencode write", "codex-mcp-client read")
        assertThat(byName["claude-code read"]!!["today"].asInt()).isEqualTo(2)
        assertThat(byName["claude-code read"]!!["via"].asText()).isEqualTo("mcp")
        assertThat(byName["claude-code read"]!!["last_at"].asText()).endsWith("Z")
        // the person's own calls (the browser, keel2) and keel's internal token never count
        assertThat(list.none { it["client"].asText() == "an MCP client" }).isTrue()
    }

    @Test
    fun `keel TUI counts as the terminal, at the person's level`() {
        bare.perform(MockMvcRequestBuilders.get("/api/projects").header("Authorization", "Bearer ${key.value}")
            .header("X-Keel-Via", "tui").header("X-Keel-Client", "keel TUI 0.18.66")).andExpect(status().isOk)
        val c = get("/api/mcp/clients").json()["clients"].single()
        assertThat(c["via"].asText()).isEqualTo("tui")
        assertThat(c["level"].asText()).isEqualTo("you")
        // Start here's step 3 is about AI tools over MCP only
        assertThat(get("/api/help/start").json()["mcp"]).isEmpty()
    }

    @Test
    fun `start here says what keel sees - projects, init, the first flow that is not init, the MCP tools`() {
        val (pid, root) = newProject("start-here")
        var s = get("/api/help/start?project=$pid").andExpect(status().isOk).json()
        assertThat(s["projects"].asInt()).isGreaterThanOrEqualTo(1)
        assertThat(s["project"]["id"].asText()).isEqualTo(pid)
        assertThat(s["project"]["configured"].asBoolean()).isFalse()
        assertThat(s["models"].isArray).isTrue()
        assertThat(s["models"].none { it["provider"].asText() == "fake" }).isTrue()
        assertThat(s["welcome"]["seen"].asBoolean()).isFalse()

        java.nio.file.Files.createDirectories(root.resolve(".keel"))
        java.nio.file.Files.writeString(root.resolve(".keel/config.yml"), "stacks: []\n")
        mvc.perform(MockMvcRequestBuilders.get("/api/projects").header("X-Keel-Mcp-Token", token("write")).header("X-Keel-Client", "claude-code"))
        s = get("/api/help/start?project=$pid").json()
        assertThat(s["project"]["configured"].asBoolean()).isTrue()
        assertThat(s["mcp"].map { it["client"].asText() }).containsExactly("claude-code")
    }

    @Test
    fun `the welcome is seen once per keel, and an MCP write token cannot say so`() {
        assertThat(get("/api/help/welcome").json()["seen"].asBoolean()).isFalse()
        val write = token("write")
        put("/api/help/welcome", mapOf("seen" to true), mapOf("X-Keel-Via" to "mcp", "X-Keel-Mcp-Token" to write)).andExpect(status().isForbidden)
        val w = put("/api/help/welcome", mapOf("seen" to true, "how" to "skipped")).andExpect(status().isOk).json()
        assertThat(w["seen"].asBoolean()).isTrue()
        assertThat(w["how"].asText()).isEqualTo("skipped")
        assertThat(get("/api/help/welcome").json()["seen"].asBoolean()).isTrue()
        assertThat(put("/api/help/welcome", mapOf("seen" to false)).json()["seen"].asBoolean()).isFalse()
    }

    @Test
    fun `access says this computer, Wi-Fi with its addresses, or Tailscale, from what keel2 sent`() {
        // the tests' environment has no KEEL_BIND (an older keel2): the addresses decide
        assertThat(get("/api/access").json()["mode"].asText()).isEqualTo("local")
        put("/api/devices/addresses", mapOf("urls" to listOf(mapOf("kind" to "tailscale", "url" to "https://my-computer.tail0000.ts.net"))))
        val t = get("/api/access").json()
        assertThat(t["mode"].asText()).isEqualTo("tailscale")
        assertThat(t["tailscale"].asText()).isEqualTo("https://my-computer.tail0000.ts.net")
        put("/api/devices/addresses", mapOf("urls" to listOf(
            mapOf("kind" to "wifi", "url" to "http://192.168.1.20:8088"),
            mapOf("kind" to "tailscale", "url" to "https://my-computer.tail0000.ts.net"),
        )))
        val w = get("/api/access").json()
        assertThat(w["mode"].asText()).isEqualTo("wifi")
        assertThat(w["wifi"].map { it.asText() }).containsExactly("http://192.168.1.20:8088")
        assertThat(w["tailscale"].asText()).isEqualTo("https://my-computer.tail0000.ts.net")
    }
}
