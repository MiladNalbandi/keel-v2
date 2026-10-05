package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.time.Instant

class ProviderUsageApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    private val logins = listOf("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "GH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN")

    private fun ev(type: String, pid: String, tid: String, callId: String? = null, data: Map<String, Any?> = emptyMap()) =
        mapOf("type" to type, "thread_id" to tid, "project_id" to pid, "step" to "red", "at" to Instant.now().toString(), "call_id" to callId, "data" to data)

    private fun send(vararg events: Map<String, Any?>) =
        post("/internal/events", events.toList(), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    private fun cards() = get("/api/usage/providers").andExpect(status().isOk).json()

    @BeforeEach
    fun clean() {
        logins.forEach { jdbc.update("DELETE FROM secrets WHERE name = ?", it) }
        jdbc.update("DELETE FROM provider_usage")
        engine.usageAnswers.clear()
    }

    @Test
    fun `V5 adds the job mode and the provider_usage table`() {
        val cols = jdbc.queryForList("PRAGMA table_info(agent_calls)").map { it["name"] }
        assertThat(cols).contains("mode")
        val usageCols = jdbc.queryForList("PRAGMA table_info(provider_usage)").map { it["name"] }
        assertThat(usageCols).contains("provider", "window", "used_pct", "used", "cap", "remaining", "resets_at", "source", "fetched_at", "raw_json")
    }

    @Test
    fun `only api-mode jobs count toward the API keys meter`() {
        val (pid, _) = newProject("usage-mode")
        fun apiUsed() = get("/api/limits").json().first { it["id"].asText() == "api" }["used"].asDouble()
        val before = apiUsed()
        send(
            ev("agent.started", pid, "t-mode", "m-api", mapOf("agent" to "implementer", "provider" to "claude", "mode" to "api")),
            ev("agent.finished", pid, "t-mode", "m-api", mapOf("status" to "done", "cost_usd" to 1.25)),
            ev("agent.started", pid, "t-mode", "m-sub", mapOf("agent" to "implementer", "provider" to "claude", "mode" to "subscription")),
            ev("agent.finished", pid, "t-mode", "m-sub", mapOf("status" to "done", "cost_usd" to 7.5)),
        )
        assertThat(apiUsed() - before).isEqualTo(1.25)
        val modes = jdbc.queryForList("SELECT id, mode FROM agent_calls WHERE thread_id = 't-mode'").associate { it["id"] to it["mode"] }
        assertThat(modes).isEqualTo(mapOf("m-api" to "api", "m-sub" to "subscription"))
    }

    @Test
    fun `claude windows come from provider_usage events and cross 80 percent once`() {
        val (pid, _) = newProject("usage-claude")
        put("/api/secrets/CLAUDE_CODE_OAUTH_TOKEN", mapOf("value" to "sk-ant-oat01-test")).andExpect(status().isOk)
        assertThat(cards().map { it["id"].asText() }).containsExactly("claude")
        assertThat(cards()[0]["windows"].size()).isEqualTo(0)
        assertThat(cards()[0]["note"].asText()).contains("next Claude run")

        val resets = Instant.now().epochSecond + 3600
        fun usage(pct: Double) = ev("provider.usage", pid, "t-claude", data = mapOf("provider" to "claude", "source" to "last run",
            "at" to Instant.now().toString(), "windows" to listOf(
                mapOf("window" to "five_hour", "label" to "5-hour", "used_pct" to pct, "resets_at" to resets, "status" to "allowed"),
                mapOf("window" to "seven_day", "label" to "weekly", "used_pct" to 0.31, "resets_at" to resets + 86400),
            )))
        val notes = { jdbc.queryForObject("SELECT COUNT(*) FROM notifications WHERE title = 'Claude 5-hour window 82% used'", Int::class.java) }
        send(usage(0.5))
        send(usage(0.82))
        send(usage(0.85))
        assertThat(notes()).isEqualTo(1)

        val card = cards()[0]
        assertThat(card["source"].asText()).isEqualTo("last run")
        assertThat(card["fetched_at"].asText()).isNotBlank()
        assertThat(card["windows"].map { it["window"].asText() }).containsExactly("five_hour", "seven_day")
        assertThat(card["windows"][0]["used_pct"].asDouble()).isEqualTo(0.85)
        assertThat(card["windows"][0]["resets_at"].asText()).isEqualTo(Instant.ofEpochSecond(resets).toString())

        // the account limit shows the provider's own numbers next to the manual cap
        val limit = get("/api/limits").json().first { it["id"].asText() == "claude" }
        assertThat(limit["source"].asText()).isEqualTo("last run")
        assertThat(limit["used_pct"].asDouble()).isEqualTo(0.85)
    }

    @Test
    fun `providers that are not set up get no card`() {
        assertThat(cards().map { it["id"].asText() }).doesNotContain("claude", "codex", "copilot", "api")
        put("/api/secrets/OPENAI_API_KEY", mapOf("value" to "sk-test")).andExpect(status().isOk)
        assertThat(cards().map { it["id"].asText() }).containsExactly("api")
        assertThat(cards()[0]["source"].asText()).isEqualTo("keel's own count")
    }

    @Test
    fun `codex is read live through the engine, cached for a minute, and refresh reads again`() {
        put("/api/secrets/CODEX_AUTH_JSON", mapOf("value" to "{\"tokens\":{}}")).andExpect(status().isOk)
        engine.usageAnswers["codex"] = mapOf("ok" to true, "source" to "codex app-server", "at" to Instant.now().toString(), "windows" to listOf(
            mapOf("window" to "five_hour", "label" to "5-hour", "used_pct" to 0.2, "resets_at" to Instant.now().epochSecond + 600),
            mapOf("window" to "seven_day", "label" to "weekly", "used_pct" to 0.08, "resets_at" to Instant.now().epochSecond + 86400),
        ))
        fun engineCalls() = engine.calls.count { it.path == "/providers/usage" && it.body?.get("provider")?.asText() == "codex" }
        val start = engineCalls()
        post("/api/usage/providers/codex/refresh").andExpect(status().isOk)       // a fresh read, whatever the cache holds
        val card = cards().first { it["id"].asText() == "codex" }
        cards()
        assertThat(engineCalls() - start).isEqualTo(1)
        assertThat(card["source"].asText()).isEqualTo("codex app-server")
        assertThat(card["live"].asBoolean()).isTrue()
        assertThat(card["windows"].map { it["used_pct"].asDouble() }).containsExactly(0.2, 0.08)
        assertThat(engine.lastBody("/providers/usage")!!["key"].asText()).isEqualTo("{\"tokens\":{}}")
        post("/api/usage/providers/codex/refresh").andExpect(status().isOk)
        assertThat(engineCalls() - start).isEqualTo(2)
    }

    @Test
    fun `copilot falls back to keel's own count and the manual cap when GitHub does not answer`() {
        put("/api/secrets/GH_TOKEN", mapOf("value" to "gho_test")).andExpect(status().isOk)
        jdbc.update("DELETE FROM kv WHERE key = 'limits'")           // the default caps (another test may have saved its own)
        val card = post("/api/usage/providers/copilot/refresh").andExpect(status().isOk).json()
        assertThat(card["source"].asText()).isEqualTo("keel's own count")
        assertThat(card["error"].asText()).contains("GitHub did not answer")
        assertThat(card["windows"][0]["cap"].asDouble()).isEqualTo(300.0)

        engine.usageAnswers["copilot"] = mapOf("ok" to true, "at" to Instant.now().toString(), "windows" to listOf(
            mapOf("window" to "month", "label" to "monthly", "used_pct" to 0.707, "used" to 212, "cap" to 300, "remaining" to 88, "resets_at" to "2026-11-01T00:00:00Z"),
        ))
        val live = post("/api/usage/providers/copilot/refresh").json()
        assertThat(live["source"].asText()).isEqualTo("GitHub (unofficial)")
        assertThat(live["windows"][0]["remaining"].asDouble()).isEqualTo(88.0)
    }

    @Test
    fun `new flows send the latest windows and thresholds to the engine`() {
        jdbc.update("""INSERT INTO provider_usage(provider, "window", used_pct, resets_at, source, fetched_at) VALUES ('claude', 'five_hour', 0.96, '2099-01-01T00:00:00Z', 'last run', ?)""",
            Instant.now().toString())
        val (pid, _) = newProject("usage-start")
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Ranks", "allow_fake" to true)).andExpect(status().isOk)
        val settings = engine.lastBody("/threads")!!["settings"]
        assertThat(settings["usage_pause"].asDouble()).isEqualTo(0.95)
        assertThat(settings["provider_windows"][0]["window"].asText()).isEqualTo("five_hour")
        assertThat(settings["provider_windows"][0]["used_pct"].asDouble()).isEqualTo(0.96)
    }

    @Test
    fun `usage thresholds are checked in settings`() {
        put("/api/settings/general", mapOf("usage_warn" to 1.5)).andExpect(status().isBadRequest)
    }
}
