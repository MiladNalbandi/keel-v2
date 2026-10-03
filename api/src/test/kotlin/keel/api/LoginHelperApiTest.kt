package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** Logging the CLIs in from the dashboard; the result lands encrypted in the secrets table, never in a response. */
class LoginHelperApiTest : ApiTest() {
    @AfterEach
    fun clean() {
        listOf("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "GH_TOKEN").forEach { runCatching { delete("/api/secrets/$it") } }
    }

    private fun waitFor(id: String, vararg states: String): com.fasterxml.jackson.databind.JsonNode {
        repeat(80) {
            val v = get("/api/logins/$id").json()
            if (v["status"].asText() in states) return v
            Thread.sleep(100)
        }
        error("login $id never reached ${states.toList()}")
    }

    @Test
    fun `codex device login shows link and code, then saves auth json`() {
        val v = post("/api/logins", mapOf("provider" to "codex")).andExpect(status().isOk).json()
        assertThat(v["url"].asText()).isEqualTo("https://auth.openai.com/codex/device")
        assertThat(v["code"].asText()).isEqualTo("AB12-CD345")
        val done = waitFor(v["id"].asText(), "done", "failed")
        assertThat(done["status"].asText()).isEqualTo("done")
        assertThat(done.toString()).doesNotContain("codex-from-device")
        val p = get("/api/connections").json()["providers"].first { it["id"].asText() == "codex" }
        assertThat(p["login_set"].asBoolean()).isTrue()
    }

    @Test
    fun `copilot device login saves the github token it received`() {
        val v = post("/api/logins", mapOf("provider" to "copilot")).json()
        assertThat(v["url"].asText()).isEqualTo("https://github.com/login/device")
        assertThat(v["code"].asText()).isEqualTo("32B2-75E8")
        val done = waitFor(v["id"].asText(), "done", "failed")
        assertThat(done["status"].asText()).isEqualTo("done")
        assertThat(done["hint"].asText()).isEqualTo("…456")
    }

    @Test
    fun `claude login asks for the page code, then saves the printed token`() {
        val v = post("/api/logins", mapOf("provider" to "claude")).json()
        assertThat(v["status"].asText()).isEqualTo("code_needed")
        assertThat(v["url"].asText()).startsWith("https://claude.com/cai/oauth/authorize")
        post("/api/logins/${v["id"].asText()}/code", mapOf("code" to "good-code")).andExpect(status().isOk)
        val done = waitFor(v["id"].asText(), "done", "failed")
        assertThat(done["status"].asText()).isEqualTo("done")
        assertThat(done.toString()).doesNotContain("sk-ant-oat01")
        assertThat(done["hint"].asText()).isEqualTo("…123")
    }

    @Test
    fun `a wrong claude code fails with a readable message`() {
        val v = post("/api/logins", mapOf("provider" to "claude")).json()
        post("/api/logins/${v["id"].asText()}/code", mapOf("code" to "bad-code")).andExpect(status().isOk)
        val done = waitFor(v["id"].asText(), "failed")
        assertThat(done["message"].asText()).contains("invalid code")
        post("/api/logins", mapOf("provider" to "nope")).andExpect(status().isBadRequest)
    }
}
