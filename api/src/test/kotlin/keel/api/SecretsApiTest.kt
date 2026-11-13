package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission

class SecretsApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private val value = "sk-ant-test-0123456789-SECRETxyz"

    @Test
    fun `a secret is stored encrypted and only its hint ever comes back`() {
        val put = put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to value)).andExpect(status().isOk)
        val body = put.andReturn().response.contentAsString
        assertThat(body).doesNotContain(value).contains("xyz")
        assertThat(put.json()["hint"].asText()).isEqualTo("…xyz")

        val conn = get("/api/connections").andExpect(status().isOk)
        val text = conn.andReturn().response.contentAsString
        assertThat(text).doesNotContain(value)
        val claude = conn.json()["providers"].first { it["id"].asText() == "claude" }
        assertThat(claude["key_set"].asBoolean()).isTrue()
        assertThat(claude["key_hint"].asText()).isEqualTo("…xyz")
        assertThat(claude["modes"].first { it["id"].asText() == "api" }["ready"].asBoolean()).isTrue()

        // Not in the db in clear text.
        val stored = jdbc.queryForList("SELECT * FROM secrets WHERE name = 'ANTHROPIC_API_KEY'").single()
        assertThat(stored.values.joinToString { if (it is ByteArray) String(it, Charsets.ISO_8859_1) else it.toString() }).doesNotContain(value)

        // Master key file exists and is private.
        val key = dataDir.resolve("master.key")
        assertThat(Files.size(key)).isEqualTo(32)
        assertThat(Files.getPosixFilePermissions(key)).containsExactlyInAnyOrder(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)

        // The engine gets the key (internal use), the web never does.
        put("/api/connections/claude", mapOf("mode" to "api")).andExpect(status().isOk)
        val test = post("/api/connections/claude/test").andExpect(status().isOk)
        assertThat(test.andReturn().response.contentAsString).doesNotContain(value)
        assertThat(engine.lastBody("/providers/test")!!["key"].asText()).isEqualTo(value)
        delete("/api/secrets/ANTHROPIC_API_KEY").andExpect(status().isOk)
        delete("/api/secrets/ANTHROPIC_API_KEY").andExpect(status().isNotFound)
    }

    @Test
    fun `secret names are checked`() {
        put("/api/secrets/bad name!", mapOf("value" to "x")).andExpect(status().isBadRequest)
        put("/api/secrets/OK_NAME", mapOf("value" to "")).andExpect(status().isBadRequest)
    }

    @Test
    fun `connections list every provider and the machine tools`() {
        val c = get("/api/connections").json()
        assertThat(c["providers"].map { it["id"].asText() }).containsExactly("fake", "claude", "codex", "copilot", "vertex", "local")
        // #101 keel's harness runs every model on its API: one mode each, always selected
        for (p in c["providers"]) {
            assertThat(p["modes"].map { it["id"].asText() }).describedAs(p["id"].asText()).containsExactly("api")
            assertThat(p["selected"].asText()).isEqualTo("api")
        }
        assertThat(c["providers"].first { it["id"].asText() == "copilot" }["modes"][0]["label"].asText()).isEqualTo("GitHub Models (token)")
        // keel v1 is not part of keel v2 (0.4.1), and no coding-agent app is listed any more (#101)
        assertThat(c["machine"].map { it["name"].asText() }).containsExactly("node", "git", "java", "docker")
        put("/api/connections/copilot", mapOf("mode" to "api")).andExpect(status().isOk)
        for (mode in listOf("subscription", "opencode", "teleport"))
            put("/api/connections/copilot", mapOf("mode" to mode)).andExpect(status().isBadRequest)
        put("/api/connections/nobody", mapOf("mode" to "api")).andExpect(status().isNotFound)
    }
}
