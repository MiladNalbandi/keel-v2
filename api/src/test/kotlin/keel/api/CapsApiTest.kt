package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.within
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.UUID

/** v0.4.2: every project cap reaches the engine at flow start; the smallest one left binds; a used-up cap refuses or goes cheaper. */
class CapsApiTest : ApiTest() {

    @Autowired lateinit var jdbc: JdbcTemplate

    private val today: LocalDate = LocalDate.now(ZoneOffset.UTC)

    private fun call(pid: String, at: Instant, tokensIn: Long, tokensOut: Long, cost: Double, mode: String) {
        jdbc.update(
            "INSERT INTO agent_calls(id, project_id, agent, provider, status, started_at, tokens_in, tokens_out, cost_usd, mode) VALUES (?, ?, 'implementer', 'claude', 'done', ?, ?, ?, ?, ?)",
            "c-" + UUID.randomUUID(), pid, at.toString(), tokensIn, tokensOut, cost, mode,
        )
    }

    /**
     * Today: 150k tokens and $2 on a subscription, 400k tokens and $10 on an API key (550k / $12 today, 400k / $10 of
     * API use this month). Last month and another project do not count.
     */
    private fun project(name: String): String {
        val (pid, _) = newProject(name)
        put("/api/projects/$pid/settings", mapOf(
            "default_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"),
            "implementer_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"),
            "reviewer_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"),
            "cap_tokens" to 500_000, "on_cap" to "pause",
        )).andExpect(status().isOk)
        val now = Instant.now()
        call(pid, now, 100_000, 50_000, 2.0, "subscription")
        call(pid, now, 300_000, 100_000, 10.0, "api")
        call(pid, today.withDayOfMonth(1).minusDays(1).atStartOfDay().toInstant(ZoneOffset.UTC), 900_000, 0, 50.0, "api")
        call("another-project", now, 5_000_000, 0, 99.0, "api")
        return pid
    }

    private fun cap(pid: String, scope: String, limit: Number, unit: String, action: String): String =
        post("/api/projects/$pid/caps", mapOf("scope" to scope, "limit" to limit, "unit" to unit, "action" to action))
            .andExpect(status().isOk).json()["id"].asText()

    private fun startBody(pid: String, extra: Map<String, Any?> = emptyMap()) =
        post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Capped") + extra)

    @Test
    fun `caps left counts each scope and unit`() {
        val pid = project("caps-left")
        val dayT = cap(pid, "day", 1_000_000, "tokens", "pause")
        val dayU = cap(pid, "day", 20, "usd", "pause")
        val monU = cap(pid, "api_month", 25, "usd", "stop")
        val monT = cap(pid, "api_month", 2_000_000, "tokens", "pause")
        val flow = cap(pid, "flow", 300_000, "tokens", "stop")
        val step = cap(pid, "step", 50_000, "tokens", "cheaper")
        val stepU = cap(pid, "step", 1, "usd", "stop")

        val res = get("/api/projects/$pid/caps/left").andExpect(status().isOk).json()
        val left = res["caps"].associateBy { it["id"].asText() }
        assertThat(left[dayT]!!["used"].asDouble()).isEqualTo(550_000.0)
        assertThat(left[dayT]!!["left"].asDouble()).isEqualTo(450_000.0)
        assertThat(left[dayT]!!["window"].asText()).isEqualTo("day")
        assertThat(left[dayT]!!["resets_at"].asText()).isEqualTo(today.plusDays(1).toString() + "T00:00:00Z")
        assertThat(left[dayU]!!["left"].asDouble()).isCloseTo(8.0, within(1e-9))          // $20 - $12 reported today (both modes)
        assertThat(left[monU]!!["used"].asDouble()).isCloseTo(10.0, within(1e-9))         // only API-key runs, only this month
        assertThat(left[monU]!!["left"].asDouble()).isCloseTo(15.0, within(1e-9))
        assertThat(left[monU]!!["resets_at"].asText()).isEqualTo(today.withDayOfMonth(1).plusMonths(1).toString() + "T00:00:00Z")
        assertThat(left[monT]!!["left"].asDouble()).isEqualTo(1_600_000.0)
        assertThat(left[flow]!!["left"].asDouble()).isEqualTo(300_000.0)
        assertThat(left[flow]!!["window"].asText()).isEqualTo("flow")
        assertThat(left[step]!!["left"].asDouble()).isEqualTo(50_000.0)
        assertThat(left[stepU]!!["checked"].asBoolean()).isFalse()
        assertThat(left[stepU]!!["note"].asText()).contains("Not checked")
        assertThat(left[dayT]!!["note"].asText()).isEqualTo("450k tokens left today (550k tokens used today).")

        // the flow cap (300k) is the smallest tokens left; the day's $8 the smallest dollars; the step cap per step
        val next = res["next_flow"]
        assertThat(next["cap_tokens"].asInt()).isEqualTo(300_000)
        assertThat(next["on_cap"].asText()).isEqualTo("stop")
        assertThat(next["tokens_from"].asText()).isEqualTo(flow)
        assertThat(next["cap_usd"].asDouble()).isCloseTo(8.0, within(1e-9))
        assertThat(next["usd_from"].asText()).isEqualTo(dayU)
        assertThat(next["step_cap_tokens"].asInt()).isEqualTo(50_000)
        assertThat(next["step_on_cap"].asText()).isEqualTo("cheaper")
        assertThat(next["refused"].isNull).isTrue()

        val budgetCaps = get("/api/projects/$pid/budget").json()["caps"].associateBy { it["id"].asText() }
        assertThat(budgetCaps[stepU]!!["name"].asText()).contains("not checked")
    }

    @Test
    fun `a flow starts with the smallest cap left and the engine gets every limit`() {
        val pid = project("caps-smallest")
        cap(pid, "flow", 400_000, "tokens", "stop")
        val day = cap(pid, "day", 700_000, "tokens", "cheaper")       // 150k left today: the smallest
        cap(pid, "api_month", 25, "usd", "stop")                      // $15 left
        cap(pid, "day", 30, "usd", "pause")                           // $18 left
        cap(pid, "step", 80_000, "tokens", "pause")
        cap(pid, "step", 60_000, "tokens", "stop")                    // the smaller step cap wins

        val state = startBody(pid).andExpect(status().isOk).json()
        val s = engine.lastBody("/threads")!!["settings"]
        assertThat(s["cap_tokens"].asInt()).isEqualTo(150_000)
        assertThat(s["commit_coauthor"].asBoolean()).isTrue()          // keel's commits name KeelBot (on by default)
        assertThat(s.path("commit_author").textValue()).isNull()         // empty: the engine takes the project's git name
        assertThat(s["on_cap"].asText()).isEqualTo("cheaper")
        assertThat(s["cap_usd"].asDouble()).isCloseTo(15.0, within(1e-9))
        assertThat(s["on_cap_usd"].asText()).isEqualTo("stop")
        assertThat(s["step_cap_tokens"].asInt()).isEqualTo(60_000)
        assertThat(s["step_on_cap"].asText()).isEqualTo("stop")
        assertThat(state["cap_note"].asText()).contains("This flow gets 150k tokens").contains("All flows, per day: 700k tokens")
        assertThat(engine.lastBody("/threads")!!.has("cap_note")).isFalse()
        assertThat(get("/api/projects/$pid/caps/left").json()["next_flow"]["tokens_from"].asText()).isEqualTo(day)
    }

    @Test
    fun `a smaller cap from the start request or Settings still wins`() {
        val pid = project("caps-request")
        cap(pid, "flow", 400_000, "tokens", "stop")
        startBody(pid, mapOf("cap_tokens" to 90_000, "on_cap" to "pause")).andExpect(status().isOk)
        var s = engine.lastBody("/threads")!!["settings"]
        assertThat(s["cap_tokens"].asInt()).isEqualTo(90_000)
        assertThat(s["on_cap"].asText()).isEqualTo("pause")
        assertThat(s["cap_usd"].isNull).isTrue()
        assertThat(s["step_cap_tokens"].isNull).isTrue()

        put("/api/projects/$pid/settings", mapOf("cap_tokens" to 200_000, "on_cap" to "cheaper")).andExpect(status().isOk)
        val state = startBody(pid).andExpect(status().isOk).json()
        s = engine.lastBody("/threads")!!["settings"]
        assertThat(s["cap_tokens"].asInt()).isEqualTo(200_000)
        assertThat(s["on_cap"].asText()).isEqualTo("cheaper")
        assertThat(state.has("cap_note")).isFalse()                   // Settings bind: nothing to explain
    }

    @Test
    fun `a used-up day cap that pauses refuses the start and says what was used and when it resets`() {
        val pid = project("caps-day-out")
        cap(pid, "day", 100_000, "tokens", "pause")
        val err = startBody(pid).andExpect(status().isConflict).json()
        assertThat(err["error"].asText()).isEqualTo("The cap \"All flows, per day: 100k tokens\" is used up: 550k tokens used today.")
        assertThat(err["hint"].asText()).startsWith("It resets at 00:00 UTC tomorrow (${today.plusDays(1)}).")
            .contains("Raise or delete the cap in Budget")
        assertThat(get("/api/projects/$pid/caps/left").json()["next_flow"]["refused"]["error"].asText()).contains("is used up")
    }

    @Test
    fun `a used-up month cap that stops refuses the start until the 1st`() {
        val pid = project("caps-month-out")
        cap(pid, "api_month", 10, "usd", "stop")
        val err = startBody(pid).andExpect(status().isConflict).json()
        assertThat(err["error"].asText()).isEqualTo("The cap \"API keys, per month: \$10.00\" is used up: \$10.00 spent on API keys this month.")
        assertThat(err["hint"].asText()).startsWith("It resets on the 1st (${today.withDayOfMonth(1).plusMonths(1)}, 00:00 UTC).")
    }

    @Test
    fun `a used-up cap that says cheaper starts every agent on the cheaper model and says so`() {
        val pid = project("caps-cheaper")
        val cheaper = mapOf("provider" to "claude", "mode" to "subscription", "model" to "haiku")
        put("/api/projects/$pid/settings", mapOf("cheaper_model" to cheaper)).andExpect(status().isOk)
        cap(pid, "day", 10, "usd", "cheaper")                         // $12 used today
        cap(pid, "flow", 300_000, "tokens", "pause")
        val state = startBody(pid).andExpect(status().isOk).json()
        val body = engine.lastBody("/threads")!!
        val models = body["models"]
        assertThat(models.size()).isGreaterThan(1)
        models.fields().forEach { (k, m) -> assertThat(m["model"].asText()).describedAs(k).isEqualTo("haiku") }
        assertThat(body["settings"]["cap_usd"].isNull).isTrue()      // the used-up cap acted by switching the model
        assertThat(body["settings"]["cap_tokens"].asInt()).isEqualTo(300_000)
        assertThat(state["cap_note"].asText()).contains("All flows, per day: \$10.00").contains("used up").contains("cheaper model")
    }

    @Test
    fun `a used-up cheaper cap refuses when only the fake model is the cheaper one`() {
        val pid = project("caps-cheaper-fake")
        put("/api/projects/$pid/settings", mapOf(
            "default_model" to mapOf("provider" to "claude", "mode" to "subscription", "model" to "sonnet"),
            "cheaper_model" to mapOf("provider" to "fake", "mode" to "api", "model" to "fake"),
        )).andExpect(status().isOk)
        cap(pid, "day", 100_000, "tokens", "cheaper")
        val err = startBody(pid).andExpect(status().isConflict).json()
        assertThat(err["error"].asText()).contains("is used up").contains("no real cheaper model is set")
        assertThat(err["hint"].asText()).contains("Settings › Cheaper model")
    }

    @Test
    fun `no project cap leaves the Settings cap alone`() {
        val pid = project("caps-none")
        val res = get("/api/projects/$pid/caps/left").andExpect(status().isOk).json()
        assertThat(res["caps"].size()).isEqualTo(0)
        assertThat(res["next_flow"]["cap_tokens"].asInt()).isEqualTo(500_000)
        assertThat(res["next_flow"]["tokens_from"].asText()).isEqualTo("settings")
        get("/api/projects/nope/caps/left").andExpect(status().isNotFound)
    }
}
