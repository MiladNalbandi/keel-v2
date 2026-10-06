package keel.api.budget

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.KvStore
import keel.api.projects.ProjectService
import keel.api.settings.SettingsService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

data class Month(val tokens: Long, val costUsd: Double, val premiumRequests: Long, val flows: Int)
data class Day(val day: String, val claude: Long, val codex: Long, val copilot: Long, val fake: Long)
/** A cap as the budget page shows it: the contract Cap plus a readable name and where it comes from. */
data class BudgetCap(
    val id: String,
    val scope: String,
    val limit: Double,
    val unit: String,
    val action: String,
    val name: String,
    val source: String,
)
data class TopAgent(val agent: String, val provider: String?, val tokens: Long, val costUsd: Double)
data class RecentFlow(val title: String, val estimate: Long?, val real: Long, val status: String)
data class Budget(val month: Month, val days: List<Day>, val caps: List<BudgetCap>, val top: List<TopAgent>, val recent: List<RecentFlow>)

/** Tokens (cached input counts a tenth) and dollars at API prices. */
data class Spend(val tokens: Long, val costUsd: Double)
/** A running or waiting flow and what it used against its own caps (null = no cap). */
data class FlowSpend(
    val threadId: String,
    val title: String,
    val status: String,
    val tokens: Long,
    val costUsd: Double,
    val capTokens: Long?,
    val capUsd: Double?,
)
/** The budget bar on every page: database only, so the web can ask often. `caps` = the day and month caps with what is left. */
data class BudgetNow(val today: Spend, val month: Spend, val flows: List<FlowSpend>, val caps: List<CapLeft>)

data class Limit(
    val id: String = "",
    val name: String = "",
    val unit: String = "",
    val used: Double = 0.0,
    val cap: Double = 0.0,
    val note: String = "",
    // From the provider when keel can read it (usage dashboard); null = only the manual cap and keel's own count.
    val source: String? = null,
    val window: String? = null,
    val usedPct: Double? = null,
    val remaining: Double? = null,
    val resetsAt: String? = null,
    val fetchedAt: String? = null,
)

@Service
class BudgetService(
    private val jdbc: JdbcTemplate,
    private val projects: ProjectService,
    private val settings: SettingsService,
    private val kv: KvStore,
    private val capService: CapService,
    private val usage: ProviderUsageStore,
    private val planner: CapPlanner,
    private val mapper: ObjectMapper,
) {
    private fun monthStart(): String = LocalDate.now(ZoneOffset.UTC).withDayOfMonth(1).atStartOfDay().toInstant(ZoneOffset.UTC).toString()

    fun budget(pid: String): Budget {
        projects.require(pid)
        val since = monthStart()
        val month = jdbc.queryForObject(
            "SELECT COALESCE(SUM(tokens_in + tokens_out + tokens_cached / 10), 0), COALESCE(SUM(cost_usd), 0), COALESCE(SUM(premium_requests), 0) FROM agent_calls WHERE project_id = ? AND started_at >= ?",
            { rs, _ -> Triple(rs.getLong(1), rs.getDouble(2), rs.getLong(3)) }, pid, since,
        )!!
        val flows = jdbc.queryForObject("SELECT COUNT(*) FROM threads WHERE project_id = ? AND created_at >= ?", Int::class.java, pid, since) ?: 0

        val today = LocalDate.now(ZoneOffset.UTC)
        val first = today.minusDays(13)
        val byDay = jdbc.query(
            "SELECT substr(started_at, 1, 10) AS d, COALESCE(provider, 'fake'), SUM(tokens_in + tokens_out + tokens_cached / 10) FROM agent_calls WHERE project_id = ? AND started_at >= ? GROUP BY d, 2",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getLong(3)) }, pid, first.toString(),
        ).groupBy { it.first }
        val days = (0L..13L).map { first.plusDays(it) }.map { d ->
            val rows = byDay[d.format(DateTimeFormatter.ISO_LOCAL_DATE)].orEmpty().associate { it.second to it.third }
            Day(d.toString(), rows["claude"] ?: 0, rows["codex"] ?: 0, rows["copilot"] ?: 0, rows["fake"] ?: 0)
        }

        val s = settings.effective(pid)
        val name = projects.require(pid).name
        val scopeText = mapOf("day" to "per day", "flow" to "per flow", "step" to "per step", "api_month" to "API spend per month")
        val caps = listOf(
            BudgetCap("settings", "flow", s.capTokens.toDouble(), "tokens", s.onCap, "$name, per flow (settings)", "settings"),
        ) + capService.list(pid).map { c ->
            // keel counts tokens per step, not dollars: such a cap is kept but limits nothing (CapPlanner says so too)
            val unchecked = if (c.scope == "step" && c.unit == "usd") " (not checked: keel cannot count dollars per step)" else ""
            BudgetCap(c.id, c.scope, c.limit, c.unit, c.action, "$name, ${scopeText[c.scope] ?: c.scope}$unchecked", "yours")
        }

        val top = jdbc.query(
            "SELECT agent, provider, SUM(tokens_in + tokens_out + tokens_cached / 10) t, SUM(cost_usd) FROM agent_calls WHERE project_id = ? AND started_at >= ? AND agent IS NOT NULL GROUP BY agent, provider ORDER BY t DESC LIMIT 5",
            { rs, _ -> TopAgent(rs.getString(1), rs.getString(2), rs.getLong(3), rs.getDouble(4)) }, pid, since,
        )
        val recent = jdbc.query(
            """SELECT t.title, t.estimate, COALESCE((SELECT SUM(tokens_in + tokens_out + tokens_cached / 10) FROM agent_calls c WHERE c.thread_id = t.id), 0), t.status
               FROM threads t WHERE t.project_id = ? ORDER BY t.created_at DESC LIMIT 10""",
            { rs, _ -> RecentFlow(rs.getString(1), rs.getLong(2).takeIf { !rs.wasNull() }, rs.getLong(3), rs.getString(4)) }, pid,
        )
        return Budget(Month(month.first, month.second, month.third, flows), days, caps, top, recent)
    }

    fun now(pid: String): BudgetNow {
        projects.require(pid)
        fun spend(since: String) = jdbc.queryForObject(
            "SELECT COALESCE(SUM($TOKENS), 0), COALESCE(SUM(cost_usd), 0) FROM agent_calls WHERE project_id = ? AND started_at >= ?",
            { rs, _ -> Spend(rs.getLong(1), rs.getDouble(2)) }, pid, since,
        )!!
        val today = LocalDate.now(ZoneOffset.UTC).atStartOfDay().toInstant(ZoneOffset.UTC).toString()
        val flows = jdbc.query(
            """SELECT t.id, t.title, t.status, t.state_json,
                      COALESCE((SELECT SUM($TOKENS) FROM agent_calls c WHERE c.thread_id = t.id), 0),
                      COALESCE((SELECT SUM(cost_usd) FROM agent_calls c WHERE c.thread_id = t.id), 0)
               FROM threads t WHERE t.project_id = ? AND t.status IN ('running', 'waiting') ORDER BY t.updated_at DESC LIMIT 5""",
            { rs, _ ->
                val usage = rs.getString(4)?.let { runCatching { mapper.readTree(it).get("usage") }.getOrNull() }
                fun num(k: String) = usage?.get(k)?.takeIf { it.isNumber }
                // the engine's own count when it is ahead of the finished agent calls (it is what the flow checks)
                val counted = (num("tokens_in")?.asLong() ?: 0) + (num("tokens_out")?.asLong() ?: 0) + (num("tokens_cached")?.asLong() ?: 0) / 10
                FlowSpend(
                    rs.getString(1), rs.getString(2) ?: "", rs.getString(3),
                    maxOf(rs.getLong(5), counted), maxOf(rs.getDouble(6), num("cost_usd")?.asDouble() ?: 0.0),
                    num("cap_tokens")?.asLong()?.takeIf { it > 0 }, num("cap_usd")?.asDouble()?.takeIf { it > 0 },
                )
            }, pid,
        )
        val caps = planner.left(pid).filter { it.checked && it.window in setOf("day", "month") }
        return BudgetNow(spend(today), spend(monthStart()), flows, caps)
    }

    // ---- account limits ---------------------------------------------------------------------

    private fun stored(): List<Limit> = kv.get<List<Limit>>(LIMITS) ?: DEFAULT_LIMITS

    /** Caps and notes are the user's; `used` comes from jobs where keel can count it. */
    fun limits(): List<Limit> {
        val since = monthStart()
        val fiveHours = java.time.Instant.now().minusSeconds(5 * 3600).toString()
        fun tokens(provider: String, from: String) = jdbc.queryForObject(
            "SELECT COALESCE(SUM(tokens_in + tokens_out + tokens_cached / 10), 0) FROM agent_calls WHERE provider = ? AND started_at >= ?", Long::class.java, provider, from,
        ) ?: 0L
        return stored().map { l ->
            when (l.id) {
                "claude" -> withProvider(l.copy(used = tokens("claude", fiveHours).toDouble()))
                "codex" -> withProvider(l.copy(used = tokens("codex", fiveHours).toDouble()))
                "copilot" -> withProvider(l.copy(used = (jdbc.queryForObject("SELECT COALESCE(SUM(premium_requests), 0) FROM agent_calls WHERE started_at >= ?", Long::class.java, since) ?: 0L).toDouble()))
                // Only jobs that billed an API key: a subscription run's notional cost is paid by the plan.
                "api" -> l.copy(used = jdbc.queryForObject("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_calls WHERE mode = 'api' AND started_at >= ?", Double::class.java, since) ?: 0.0)
                else -> l
            }
        }
    }

    /** The provider's own numbers for this account (its fullest window), when keel has read them. */
    private fun withProvider(l: Limit): Limit {
        val (w, meta) = usage.windows(l.id).maxByOrNull { it.first.usedPct ?: -1.0 } ?: return l
        return l.copy(source = meta.first, window = w.window, usedPct = w.usedPct, remaining = w.remaining, resetsAt = w.resetsAt, fetchedAt = meta.second)
    }

    fun saveLimits(limits: List<Limit>): List<Limit> {
        if (limits.any { it.id.isBlank() || it.name.isBlank() }) throw BadRequest("Every limit needs an id and a name")
        if (limits.any { it.cap < 0 }) throw BadRequest("A cap cannot be below 0")
        kv.put(LIMITS, limits)
        return limits()
    }

    companion object {
        const val LIMITS = "limits"
        private const val TOKENS = "tokens_in + tokens_out + tokens_cached / 10"
        val DEFAULT_LIMITS = listOf(
            Limit("claude", "Claude subscription", "tokens in the last 5 hours", 0.0, 0.0, "Set the cap your plan allows. 0 = not set."),
            Limit("codex", "Codex (ChatGPT)", "tokens in the last 5 hours", 0.0, 0.0, "Set the cap your plan allows. 0 = not set."),
            Limit("copilot", "Copilot premium requests", "requests this month", 0.0, 300.0, "Resets on the 1st."),
            Limit("api", "API keys (all)", "USD spent this month", 0.0, 100.0, "Anthropic + OpenAI keys."),
        )
    }
}

@RestController
class BudgetController(private val budget: BudgetService) {
    @GetMapping("/api/projects/{pid}/budget")
    fun budget(@PathVariable pid: String): Budget = budget.budget(pid)

    @GetMapping("/api/projects/{pid}/budget/now")
    fun now(@PathVariable pid: String): BudgetNow = budget.now(pid)

    @GetMapping("/api/limits")
    fun limits(): List<Limit> = budget.limits()

    @PutMapping("/api/limits")
    fun saveLimits(@RequestBody body: List<Limit>): List<Limit> = budget.saveLimits(body)
}
