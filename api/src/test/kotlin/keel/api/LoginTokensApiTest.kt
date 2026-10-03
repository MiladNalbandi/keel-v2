package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** CLI logins (Claude setup-token, Codex auth.json, GitHub token for Copilot) live in the encrypted secrets table. */
class LoginTokensApiTest : ApiTest() {
    private val claudeToken = "sk-ant-oat01-LOGIN-token-abc"
    private val codexAuth = """{"tokens":{"access_token":"codex-secret-XYZ"}}"""

    @AfterEach
    fun clean() {
        listOf("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "GH_TOKEN").forEach { runCatching { delete("/api/secrets/$it") } }
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"))).andExpect(status().isOk)
    }

    @Test
    fun `connections show the login secret of each subscription provider and never its value`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to claudeToken)).andExpect(status().isOk)
        val text = get("/api/connections").andExpect(status().isOk).andReturn().response.contentAsString
        assertThat(text).doesNotContain(claudeToken)
        val p = get("/api/connections").json()["providers"]
        val claude = p.first { it["id"].asText() == "claude" }
        assertThat(claude["login_secret"].asText()).isEqualTo("CLAUDE_CODE_OAUTH_TOKEN")
        assertThat(claude["login_set"].asBoolean()).isTrue()
        assertThat(claude["login_hint"].asText()).isEqualTo("…abc")
        assertThat(p.first { it["id"].asText() == "codex" }["login_secret"].asText()).isEqualTo("CODEX_AUTH_JSON")
        assertThat(p.first { it["id"].asText() == "copilot" }["login_secret"].asText()).isEqualTo("GH_TOKEN")
        assertThat(p.first { it["id"].asText() == "fake" }["login_secret"].isNull).isTrue()
    }

    @Test
    fun `a subscription test sends the stored login to the engine`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to claudeToken)).andExpect(status().isOk)
        put("/api/connections/claude", mapOf("mode" to "subscription")).andExpect(status().isOk)
        val res = post("/api/connections/claude/test").andExpect(status().isOk).andReturn().response.contentAsString
        assertThat(res).doesNotContain(claudeToken)
        val body = engine.lastBody("/providers/test")!!
        assertThat(body["mode"].asText()).isEqualTo("subscription")
        assertThat(body["key"].asText()).isEqualTo(claudeToken)
    }

    @Test
    fun `a flow with subscription models gets the logins under the engine's key names`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to claudeToken)).andExpect(status().isOk)
        put("/api/secrets/CODEX_AUTH_JSON", mapOf("value" to codexAuth)).andExpect(status().isOk)
        put("/api/settings/general", mapOf(
            "default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"),
            "reviewer_model" to mapOf("provider" to "codex", "mode" to "subscription", "model" to "gpt-5"),
        )).andExpect(status().isOk)
        val (pid, _) = newProject("logins")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isOk)
        val keys = engine.lastBody("/threads")!!["keys"]
        assertThat(keys["claude_oauth"].asText()).isEqualTo(claudeToken)
        assertThat(keys["codex_auth"].asText()).isEqualTo(codexAuth)
        assertThat(keys.has("claude")).isFalse()   // no API key is sent for a subscription model
    }

    @Test
    fun `a failed test explains a badly copied token without showing it`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to "••••••abcdef-half")).andExpect(status().isOk)
        put("/api/connections/claude", mapOf("mode" to "subscription")).andExpect(status().isOk)
        engine.providerTestAnswer = mapOf("ok" to false, "ms" to 10, "error" to "`claude` is not logged in inside the container.")
        val res = post("/api/connections/claude/test").andExpect(status().isOk).json()
        assertThat(res["error"].asText()).contains("characters a token never has").doesNotContain("abcdef")
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to "half-of-a-token-ABC")).andExpect(status().isOk)
        assertThat(post("/api/connections/claude/test").json()["error"].asText()).contains("does not start with sk-ant-oat01-").contains("19 characters")
        engine.providerTestAnswer = null
    }

    @Test
    fun `whitespace from a wrapped terminal copy is removed from a token`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to "sk-ant-oat01-first\n   second-half-gAA")).andExpect(status().isOk)
        put("/api/connections/claude", mapOf("mode" to "subscription")).andExpect(status().isOk)
        post("/api/connections/claude/test").andExpect(status().isOk)
        assertThat(engine.lastBody("/providers/test")!!["key"].asText()).isEqualTo("sk-ant-oat01-firstsecond-half-gAA")
    }
}
