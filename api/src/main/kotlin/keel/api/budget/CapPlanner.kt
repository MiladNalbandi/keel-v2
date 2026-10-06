package keel.api.budget

import keel.api.agents.AgentService
import keel.api.settings.SettingsService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.RestController
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.Locale
import kotlin.math.floor

/** What one project cap leaves for a flow that starts now (GET /api/projects/{pid}/caps/left). */
data class CapLeft(
    val id: String,
    val scope: String,
    val unit: String,
    val action: String,
    val limit: Double,
    /** What already counts against it: this project's use today (day) or this month's API-key use (api_month); 0 for flow and step. */
    val used: Double,
    /** limit - used, never below 0. */
    val left: Double,
    /** "day" (UTC day) | "month" (UTC month) | "flow" | "step". */
    val window: String,
    /** When `used` starts again from 0 (ISO, UTC): the next midnight for day, the 1st of next month for api_month. */
    val resetsAt: String? = null,
    /** false = keel cannot check this cap (dollars per step): it is kept but does not limit a flow. */
    val checked: Boolean = true,
    /** The same in words, for people. */
    val note: String = "",
)

/** A start the caps refuse: the error names the cap, what was used and when it resets. */
data class CapRefusal(val capId: String, val error: String, val hint: String)

/**
 * The limits a flow gets at start from Settings (or the start request) and every cap of the project: the smallest
 * one left binds. Sent to the engine as ThreadSettings (cap_tokens, on_cap, cap_usd, on_cap_usd, step_cap_tokens, step_on_cap).
 */
data class FlowLimits(
    val capTokens: Int,
    val onCap: String,
    val capUsd: Double? = null,
    val onCapUsd: String? = null,
    val stepCapTokens: Int? = null,
    val stepOnCap: String? = null,
    /** A used-up cap says "cheaper": every agent of the flow starts on the cheaper model. */
    val cheaper: Boolean = false,
    /** What set cap_tokens / cap_usd / step_cap_tokens: "settings", "flow" (the start request) or a cap id. */
    val tokensFrom: String? = null,
    val usdFrom: String? = null,
    val stepFrom: String? = null,
    /** One line per cap that changed this flow's limits (or its model), for the start response and the Budget page. */
    val notes: List<String> = emptyList(),
    /** Set when the flow may not start (a used-up cap that pauses or stops). */
    val refused: CapRefusal? = null,
)

data class CapsLeft(val caps: List<CapLeft>, val nextFlow: FlowLimits)

@Service
class CapPlanner(
    private val jdbc: JdbcTemplate,
    private val caps: CapService,
    private val settings: SettingsService,
    private val agents: AgentService,
) {
    /** Every cap of the project with what is left of it right now. */
    fun left(pid: String): List<CapLeft> {
        val list = caps.list(pid)
        if (list.isEmpty()) return emptyList()
        val today = LocalDate.now(ZoneOffset.UTC)
        val month = today.withDayOfMonth(1)
        val day by lazy { used(pid, today, apiOnly = false) }
        val apiMonth by lazy { used(pid, month, apiOnly = true) }
        return list.map { c ->
            when {
                c.scope == "step" && c.unit == "usd" -> CapLeft(c.id, c.scope, c.unit, c.action, c.limit, 0.0, c.limit, "step", checked = false,
                    note = "Not checked: keel counts tokens per step, not dollars. Use tokens for a step cap.")
                c.scope == "flow" || c.scope == "step" -> CapLeft(c.id, c.scope, c.unit, c.action, c.limit, 0.0, c.limit, c.scope,
                    note = "Each ${c.scope} gets ${CapText.amount(c.limit, c.unit)}.")
                else -> {
                    val isDay = c.scope == "day"
                    val (tokens, usd) = if (isDay) day else apiMonth
                    val used = if (c.unit == "usd") usd else tokens
                    val left = (c.limit - used).coerceAtLeast(0.0)
                    val reset = if (isDay) today.plusDays(1) else month.plusMonths(1)
                    val resetsAt = reset.atStartOfDay().toInstant(ZoneOffset.UTC).toString()
                    val note = if (left <= 0) "Used up (${CapText.used(c.scope, c.unit, used)}). ${CapText.resets(c.scope, reset)}"
                        else "${CapText.amount(left, c.unit)} left ${if (isDay) "today" else "this month"} (${CapText.used(c.scope, c.unit, used)})."
                    CapLeft(c.id, c.scope, c.unit, c.action, c.limit, used, left, if (isDay) "day" else "month", resetsAt, true, note)
                }
            }
        }
    }

    /** Tokens (new input + output + a tenth of cache reads, as everywhere) and reported cost of this project's runs since `from`. */
    private fun used(pid: String, from: LocalDate, apiOnly: Boolean): Pair<Double, Double> = jdbc.queryForObject(
        "SELECT COALESCE(SUM(tokens_in + tokens_out + tokens_cached / 10), 0), COALESCE(SUM(cost_usd), 0) FROM agent_calls " +
            "WHERE project_id = ? AND started_at >= ?" + (if (apiOnly) " AND mode = 'api'" else ""),
        { rs, _ -> rs.getDouble(1) to rs.getDouble(2) }, pid, from.atStartOfDay().toInstant(ZoneOffset.UTC).toString(),
    ) ?: (0.0 to 0.0)

    /**
     * The limits for a flow that starts now. `requestedTokens`/`requestedOn` come from the start request (null = Settings);
     * `cheaperOk` says whether the cheaper model may run this flow (never the fake model for a flow with real models).
     */
    fun limits(pid: String, requestedTokens: Int? = null, requestedOn: String? = null, cheaperOk: Boolean? = null): FlowLimits {
        val s = settings.effective(pid)
        val ok = cheaperOk ?: run {
            val models = listOf(s.defaultModel) + agents.list(pid).filter { it.enabled }.map { it.model }
            s.cheaperModel.provider != "fake" || models.all { it.provider == "fake" }
        }
        val from = if (requestedTokens != null) "flow" else "settings"
        return combine(left(pid), requestedTokens ?: s.capTokens, requestedOn ?: s.onCap, from, ok)
    }

    fun summary(pid: String): CapsLeft = CapsLeft(left(pid), limits(pid))

    companion object {
        /** On a tie the stricter action binds. */
        private val STRICT = mapOf("stop" to 0, "pause" to 1, "cheaper" to 2)

        private data class Candidate(val left: Double, val action: String, val from: String, val cap: CapLeft?)

        /** The smallest cap left binds; a used-up cap refuses the start (pause, stop) or starts it on the cheaper model. */
        fun combine(caps: List<CapLeft>, baseTokens: Int, baseOn: String, baseFrom: String, cheaperOk: Boolean): FlowLimits {
            val checked = caps.filter { it.checked }
            val usedUp = checked.filter { it.resetsAt != null && it.left <= 0 }
            val blocking = usedUp.firstOrNull { it.action != "cheaper" } ?: usedUp.firstOrNull { !cheaperOk }
            if (blocking != null) return FlowLimits(baseTokens.coerceAtLeast(0), baseOn, refused = refusal(blocking, cheaperOk))

            val notes = usedUp.map { "${CapText.label(it)} is used up (${CapText.used(it.scope, it.unit, it.used)}): every agent starts on the cheaper model." }
            val live = checked - usedUp.toSet()
            fun smallest(list: List<Candidate>) = list.minWithOrNull(compareBy<Candidate>({ it.left }, { STRICT[it.action] ?: 9 }))

            val base = Candidate(baseTokens.toDouble(), baseOn, baseFrom, null).takeIf { baseTokens > 0 }
            val tokens = smallest(listOfNotNull(base) + live.filter { it.unit == "tokens" && it.scope != "step" }.map { Candidate(it.left, it.action, it.id, it) })
            val usd = smallest(live.filter { it.unit == "usd" && it.scope != "step" }.map { Candidate(it.left, it.action, it.id, it) })
            val step = smallest(live.filter { it.unit == "tokens" && it.scope == "step" }.map { Candidate(it.left, it.action, it.id, it) })

            val more = mutableListOf<String>()
            tokens?.cap?.let { more += "This flow gets ${CapText.tokens(it.left)} tokens, then ${CapText.ACTION[it.action]}: ${CapText.why(it)}." }
            usd?.cap?.let { more += "This flow may spend ${CapText.usd(it.left)} (reported cost), then ${CapText.ACTION[it.action]}: ${CapText.why(it)}." }
            step?.cap?.let { more += "Each agent step gets ${CapText.tokens(it.left)} tokens, then ${CapText.ACTION[it.action]} (a step's own smaller limit still wins)." }
            return FlowLimits(
                capTokens = tokens?.let { floor(it.left).toInt().coerceAtLeast(1) } ?: 0,
                onCap = tokens?.action ?: baseOn,
                capUsd = usd?.let { Math.round(it.left * 10_000) / 10_000.0 }?.coerceAtLeast(0.0001),
                onCapUsd = usd?.action,
                stepCapTokens = step?.let { floor(it.left).toInt().coerceAtLeast(1) },
                stepOnCap = step?.action,
                cheaper = usedUp.isNotEmpty(),
                tokensFrom = tokens?.from, usdFrom = usd?.from, stepFrom = step?.from,
                notes = notes + more,
            )
        }

        private fun refusal(c: CapLeft, cheaperOk: Boolean): CapRefusal {
            val reset = LocalDate.parse(c.resetsAt!!.substring(0, 10))
            val noCheaper = c.action == "cheaper" && !cheaperOk
            val error = "The cap \"${CapText.label(c)}\" is used up: ${CapText.used(c.scope, c.unit, c.used)}." +
                (if (noCheaper) " It says to switch to the cheaper model, but no real cheaper model is set." else "")
            val fix = if (noCheaper) "Pick a real model in Settings › Cheaper model, raise or delete the cap in Budget › Limits that stop a flow, or start after the reset."
                else "Raise or delete the cap in Budget › Limits that stop a flow, or start the flow after the reset."
            return CapRefusal(c.id, error, "${CapText.resets(c.scope, reset)} $fix")
        }
    }
}

/** Caps in words (the web's labels: Budget › Caps). */
object CapText {
    val SCOPE = mapOf("day" to "All flows, per day", "flow" to "Each flow", "step" to "Any single agent step", "api_month" to "API keys, per month")
    val ACTION = mapOf("pause" to "pause and ask", "cheaper" to "switch to the cheaper model", "stop" to "stop")

    /** 950, 9.5k, 212k, 1.2M, 12M. */
    fun tokens(v: Double): String {
        fun one(x: Double) = if (x < 10) String.format(Locale.ROOT, "%.1f", x).removeSuffix(".0") else Math.round(x).toString()
        return when {
            v >= 1_000_000 -> one(v / 1_000_000) + "M"
            v >= 1_000 -> one(v / 1_000) + "k"
            else -> Math.round(v).toString()
        }
    }

    fun usd(v: Double): String = "$" + String.format(Locale.ROOT, "%.2f", v)
    fun amount(v: Double, unit: String) = if (unit == "usd") usd(v) else "${tokens(v)} tokens"
    fun label(c: CapLeft) = "${SCOPE[c.scope] ?: c.scope}: ${amount(c.limit, c.unit)}"

    /** "212k tokens used today", "$25.40 spent on API keys this month". */
    fun used(scope: String, unit: String, used: Double): String = when {
        scope == "day" && unit == "usd" -> "${usd(used)} used today (reported cost)"
        scope == "day" -> "${tokens(used)} tokens used today"
        unit == "usd" -> "${usd(used)} spent on API keys this month"
        else -> "${tokens(used)} tokens on API keys this month"
    }

    fun resets(scope: String, on: LocalDate): String =
        if (scope == "day") "It resets at 00:00 UTC tomorrow ($on)." else "It resets on the 1st ($on, 00:00 UTC)."

    fun why(c: CapLeft): String = when (c.window) {
        "day" -> "what is left today of \"${label(c)}\""
        "month" -> "what is left this month of \"${label(c)}\""
        else -> "the cap \"${label(c)}\""
    }
}

@RestController
class CapLeftController(private val planner: CapPlanner) {
    /** Every cap with what is left now, and the limits a flow started now would get. */
    @GetMapping("/api/projects/{pid}/caps/left")
    fun left(@PathVariable pid: String): CapsLeft = planner.summary(pid)
}
