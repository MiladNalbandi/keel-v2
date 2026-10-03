package keel.api.budget

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

data class Limit(
    val id: String = "",
    val name: String = "",
    val unit: String = "",
    val used: Double = 0.0,
    val cap: Double = 0.0,
    val note: String = "",
)

@Service
class BudgetService(
    private val jdbc: JdbcTemplate,
    private val projects: ProjectService,
    private val settings: SettingsService,
    private val kv: KvStore,
    private val capService: CapService,
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
            BudgetCap(c.id, c.scope, c.limit, c.unit, c.action, "$name, ${scopeText[c.scope] ?: c.scope}", "yours")
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
                "claude" -> l.copy(used = tokens("claude", fiveHours).toDouble())
                "codex" -> l.copy(used = tokens("codex", fiveHours).toDouble())
                "copilot" -> l.copy(used = (jdbc.queryForObject("SELECT COALESCE(SUM(premium_requests), 0) FROM agent_calls WHERE started_at >= ?", Long::class.java, since) ?: 0L).toDouble())
                "api" -> l.copy(used = jdbc.queryForObject("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_calls WHERE started_at >= ?", Double::class.java, since) ?: 0.0)
                else -> l
            }
        }
    }

    fun saveLimits(limits: List<Limit>): List<Limit> {
        if (limits.any { it.id.isBlank() || it.name.isBlank() }) throw BadRequest("Every limit needs an id and a name")
        if (limits.any { it.cap < 0 }) throw BadRequest("A cap cannot be below 0")
        kv.put(LIMITS, limits)
        return limits()
    }

    companion object {
        const val LIMITS = "limits"
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

    @GetMapping("/api/limits")
    fun limits(): List<Limit> = budget.limits()

    @PutMapping("/api/limits")
    fun saveLimits(@RequestBody body: List<Limit>): List<Limit> = budget.saveLimits(body)
}
