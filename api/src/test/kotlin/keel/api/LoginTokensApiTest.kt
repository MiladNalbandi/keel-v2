package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * #101 keel's own harness runs every model on its API key: flows and tests get the API keys from the encrypted secrets
 * table, never an old CLI login (Claude setup-token, Codex auth.json). GitHub Models also takes the old Copilot login's
 * GH_TOKEN and the GitHub sign-in's token.
 */
class LoginTokensApiTest : ApiTest() {
    private val claudeToken = "sk-ant-oat01-LOGIN-token-abc"
    private val codexAuth = """{"tokens":{"access_token":"codex-secret-XYZ"}}"""
    private val anthropicKey = "sk-ant-api03-KEY-abc"
    private val openaiKey = "sk-proj-KEY-xyz"

    private val signIn = listOf("GITHUB_REPO_TOKEN", "GITHUB_REPO_INFO")

    @BeforeEach
    fun noSignIn() = signIn.forEach { jdbc.update("DELETE FROM secrets WHERE name = ?", it) }

    @AfterEach
    fun clean() {
        listOf("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "GH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN").forEach { runCatching { delete("/api/secrets/$it") } }
        noSignIn()
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"))).andExpect(status().isOk)
    }

    private fun provider(id: String) = get("/api/connections").json()["providers"].first { it["id"].asText() == id }

    @Test
    fun `an old CLI login is no key, connections never show it, and it stays keel's own until deleted`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to claudeToken)).andExpect(status().isOk)
        put("/api/secrets/CODEX_AUTH_JSON", mapOf("value" to codexAuth)).andExpect(status().isOk)
        val text = get("/api/connections").andExpect(status().isOk).andReturn().response.contentAsString
        assertThat(text).doesNotContain(claudeToken).doesNotContain("codex-secret")
        for (p in get("/api/connections").json()["providers"]) {
            assertThat(p.has("login_secret") || p.has("login_set") || p.has("login_hint")).describedAs(p["id"].asText()).isFalse()
        }
        assertThat(provider("claude")["key_set"].asBoolean()).isFalse()
        assertThat(provider("codex")["key_set"].asBoolean()).isFalse()
        // never a project's or an MCP server's secret, and it can still be deleted
        for (n in listOf("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "GH_TOKEN")) assertThat(secretStore.keelOwn(n)).describedAs(n).isTrue()
        delete("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN").andExpect(status().isOk)
        delete("/api/secrets/CODEX_AUTH_JSON").andExpect(status().isOk)
    }

    @Test
    fun `GitHub Models takes GITHUB_TOKEN, else the old GH_TOKEN, else the GitHub sign-in's token, and says where it came from`() {
        val none = provider("copilot")
        assertThat(none["key_set"].asBoolean()).isFalse()
        assertThat(none["modes"][0]["ready"].asBoolean()).isFalse()
        assertThat(none["modes"][0]["detail"].asText()).isEqualTo("Add GITHUB_TOKEN, or sign in with GitHub.")

        secretStore.put("GITHUB_REPO_TOKEN", "gho_SIGNIN-token-abc")
        val signedIn = provider("copilot")
        assertThat(signedIn["key_set"].asBoolean()).isTrue()
        assertThat(signedIn["key_hint"].asText()).isEqualTo("from the GitHub sign-in")
        assertThat(signedIn["modes"][0]["ready"].asBoolean()).isTrue()
        assertThat(signedIn["modes"][0]["detail"].asText()).isEqualTo("Token from the GitHub sign-in.")
        post("/api/connections/copilot/test").andExpect(status().isOk)
        assertThat(engine.lastBody("/providers/test")!!["key"].asText()).isEqualTo("gho_SIGNIN-token-abc")

        // a sign-in to GitHub Enterprise: its token goes to that host only, never to GitHub Models
        secretStore.put("GITHUB_REPO_INFO", """{"host":"ghe.example.com","login":"ana"}""")
        assertThat(provider("copilot")["key_set"].asBoolean()).isFalse()
        jdbc.update("DELETE FROM secrets WHERE name = 'GITHUB_REPO_INFO'")

        put("/api/secrets/GH_TOKEN", mapOf("value" to "gho_OLD-copilot-login-def")).andExpect(status().isOk)
        assertThat(provider("copilot")["key_hint"].asText()).isEqualTo("…def")
        assertThat(provider("copilot")["modes"][0]["detail"].asText()).isEqualTo("Token saved.")
        put("/api/secrets/GITHUB_TOKEN", mapOf("value" to "ghp_MODELS-token-ghi")).andExpect(status().isOk)
        assertThat(provider("copilot")["key_hint"].asText()).isEqualTo("…ghi")
        post("/api/connections/copilot/test").andExpect(status().isOk)
        assertThat(engine.lastBody("/providers/test")!!["key"].asText()).isEqualTo("ghp_MODELS-token-ghi")
    }

    @Test
    fun `a test sends the API key, never the login, whatever mode was saved`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to claudeToken)).andExpect(status().isOk)
        put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to anthropicKey)).andExpect(status().isOk)
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"))).andExpect(status().isOk)
        put("/api/connections/claude", mapOf("mode" to "subscription")).andExpect(status().isBadRequest)
        val res = post("/api/connections/claude/test").andExpect(status().isOk).andReturn().response.contentAsString
        assertThat(res).doesNotContain(claudeToken).doesNotContain(anthropicKey)
        val body = engine.lastBody("/providers/test")!!
        assertThat(body["mode"].asText()).isEqualTo("api")
        assertThat(body["key"].asText()).isEqualTo(anthropicKey)
    }

    @Test
    fun `a flow gets the API keys under the providers' names and no login`() {
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to claudeToken)).andExpect(status().isOk)
        put("/api/secrets/CODEX_AUTH_JSON", mapOf("value" to codexAuth)).andExpect(status().isOk)
        put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to anthropicKey)).andExpect(status().isOk)
        put("/api/secrets/OPENAI_API_KEY", mapOf("value" to openaiKey)).andExpect(status().isOk)
        put("/api/secrets/GH_TOKEN", mapOf("value" to "gho_SIGNIN-token")).andExpect(status().isOk)
        put("/api/settings/general", mapOf(
            "default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"),
            "reviewer_model" to mapOf("provider" to "codex", "mode" to "subscription", "model" to "gpt-5"),
            "implementer_model" to mapOf("provider" to "copilot", "mode" to "opencode", "model" to "gpt-5"),
        )).andExpect(status().isOk)
        val (pid, _) = newProject("logins")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).andExpect(status().isOk)
        val sent = engine.lastBody("/threads")!!
        val keys = sent["keys"]
        assertThat(keys["claude"].asText()).isEqualTo(anthropicKey)
        assertThat(keys["codex"].asText()).isEqualTo(openaiKey)
        assertThat(keys["copilot"].asText()).isEqualTo("gho_SIGNIN-token")      // GitHub Models takes the old Copilot login's token
        assertThat(keys.has("claude_oauth") || keys.has("codex_auth")).isFalse()
        assertThat(sent.toString()).doesNotContain(claudeToken).doesNotContain("codex-secret")
        // no plan windows or their thresholds any more
        assertThat(sent["settings"].has("usage_warn") || sent["settings"].has("usage_pause") || sent["settings"].has("provider_windows")).isFalse()
    }

    @org.springframework.beans.factory.annotation.Autowired lateinit var secretStore: keel.api.connections.SecretService

    @Test
    fun `a failed test explains a badly copied key without showing it`() {
        // saved before keel checked the shape on save (PUT refuses these now)
        secretStore.put("ANTHROPIC_API_KEY", "••••••abcdef-half")
        engine.providerTestAnswer = mapOf("ok" to false, "ms" to 10, "error" to "Anthropic did not accept the API key.")
        val res = post("/api/connections/claude/test").andExpect(status().isOk).json()
        assertThat(res["error"].asText()).contains("API key has characters a token never has").doesNotContain("abcdef")
        secretStore.put("ANTHROPIC_API_KEY", "half-of-a-key-ABC")
        assertThat(post("/api/connections/claude/test").json()["error"].asText()).contains("does not start with sk-ant-")
        engine.providerTestAnswer = null
    }

    @Test
    fun `saving refuses a key that cannot be right and says why, without showing it`() {
        val res = put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to "sk-ant-api03-test_ABC#\nmore-XYZ")).andExpect(status().isBadRequest).json()
        assertThat(res["error"].asText()).isEqualTo("The Anthropic API key has characters a token never has (#).")
        assertThat(res["hint"].asText()).contains("in one piece")
        assertThat(res.toString()).doesNotContain("test_ABC").doesNotContain("XYZ")
        val short = put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to "api03-test_ABC")).andExpect(status().isBadRequest).json()
        assertThat(short["error"].asText()).isEqualTo("The Anthropic API key does not start with sk-ant- (it has 14 characters).")
        assertThat(short["hint"].asText()).isEqualTo("Check that you pasted the right key.")
        put("/api/secrets/GH_TOKEN", mapOf("value" to "gho_te#st")).andExpect(status().isBadRequest)
        assertThat(provider("claude")["key_set"].asBoolean()).isFalse()
        // a key wrapped over two lines is still fine: line breaks and spaces are removed
        put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to " sk-ant-api03-test_ABC\n  -more-XYZ ")).andExpect(status().isOk)
    }

    @Test
    fun `whitespace from a wrapped terminal copy is removed from a key`() {
        put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to "sk-ant-api03-first\n   second-half-gAA")).andExpect(status().isOk)
        put("/api/connections/claude", mapOf("mode" to "api")).andExpect(status().isOk)
        post("/api/connections/claude/test").andExpect(status().isOk)
        assertThat(engine.lastBody("/providers/test")!!["key"].asText()).isEqualTo("sk-ant-api03-firstsecond-half-gAA")
    }

    @Test
    fun `resume sends the keys again, because the engine keeps them only in memory`() {
        put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to anthropicKey)).andExpect(status().isOk)
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"))).andExpect(status().isOk)
        val (pid, _) = newProject("resume-keys")
        val tid = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).json()["thread_id"].asText()
        post("/api/threads/$tid/resume", mapOf("decision" to "approve")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/$tid/resume")!!["keys"]["claude"].asText()).isEqualTo(anthropicKey)
    }

    @Test
    fun `resume and rewind send the project's folder now, so a flow follows a moved mount`() {
        val (pid, root) = newProject("resume-root")
        val tid = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x", "allow_fake" to true)).json()["thread_id"].asText()
        post("/api/threads/$tid/resume", mapOf("decision" to "approve")).andExpect(status().isOk)
        assertThat(engine.lastBody("/threads/$tid/resume")!!["root"].asText()).isEqualTo(root.toString())
        post("/api/threads/$tid/rewind", mapOf("checkpoint_id" to "c1"))
        assertThat(engine.lastBody("/threads/$tid/rewind")!!["root"].asText()).isEqualTo(root.toString())
    }

    @org.springframework.beans.factory.annotation.Autowired lateinit var jdbc: org.springframework.jdbc.core.JdbcTemplate
    @org.springframework.beans.factory.annotation.Autowired lateinit var flows: keel.api.flow.FlowService

    @Test
    fun `after a restart running flows get their keys and folder back`() {
        put("/api/secrets/ANTHROPIC_API_KEY", mapOf("value" to anthropicKey)).andExpect(status().isOk)
        put("/api/settings/general", mapOf("default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"))).andExpect(status().isOk)
        val (pid, root) = newProject("continue-after-restart")
        val tid = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "x")).json()["thread_id"].asText()
        jdbc.update("UPDATE threads SET status = 'running' WHERE id = ?", tid)
        flows.continueAfterRestart()
        val body = engine.lastBody("/threads/$tid/continue")!!
        assertThat(body["keys"]["claude"].asText()).isEqualTo(anthropicKey)
        assertThat(body["root"].asText()).isEqualTo(root.toString())
    }
}
