package keel.api.budget

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.common.Json
import keel.api.common.KvStore
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.notifications.NotificationService
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RestController
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.concurrent.ConcurrentHashMap

/** One window of a provider's plan: 5-hour, weekly, monthly. used_pct is 0..1; null when the source does not say. */
data class UsageWindow(
    val window: String,
    val label: String,
    val usedPct: Double? = null,
    val used: Double? = null,
    val cap: Double? = null,
    val remaining: Double? = null,
    val resetsAt: String? = null,
    val status: String? = null,
)

/** A usage card: what one set-up provider says is used and what remains, with where the numbers come from and when. */
data class ProviderUsage(
    val id: String,
    val name: String,
    val kind: String,                  // subscription | api
    val source: String,                // "last run", "codex app-server", "GitHub (unofficial)", "keel's own count"
    val fetchedAt: String?,
    val windows: List<UsageWindow>,
    val live: Boolean,                 // read from the provider now (codex, copilot), not only from keel's runs
    val canRefresh: Boolean,
    val error: String? = null,
    val note: String? = null,
)

/**
 * Stores plan windows (from engine events and live reads) in provider_usage, one row per provider and window, and
 * tells the user once when a window crosses 80%.
 */
@Service
class ProviderUsageStore(private val jdbc: JdbcTemplate, private val notifications: NotificationService) {

    fun store(provider: String, windows: List<Map<String, Any?>>, source: String, fetchedAt: String = Time.now()) {
        for (w in windows) {
            val name = w["window"]?.toString()?.takeIf { it.isNotBlank() } ?: continue
            val pct = w.num("used_pct")
            val before = jdbc.query("SELECT used_pct, fetched_at FROM provider_usage WHERE provider = ? AND \"window\" = ?",
                { rs, _ -> rs.getDouble(1).takeIf { !rs.wasNull() } to rs.getString(2) }, provider, name).firstOrNull()
            if (before != null && before.second > fetchedAt) continue          // an older reading never wins
            val resets = resetsIso(w["resets_at"])
            jdbc.update(
                """INSERT INTO provider_usage(provider, "window", used_pct, used, cap, remaining, resets_at, status, source, fetched_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, "window") DO UPDATE SET used_pct = excluded.used_pct, used = excluded.used, cap = excluded.cap,
                     remaining = excluded.remaining, resets_at = excluded.resets_at, status = excluded.status, source = excluded.source,
                     fetched_at = excluded.fetched_at, raw_json = excluded.raw_json""",
                provider, name, pct, w.num("used"), w.num("cap"), w.num("remaining"), resets, w["status"]?.toString(), source, fetchedAt, Json.write(w),
            )
            val crossed = pct != null && pct >= WARN && (before?.first == null || before.first!! < WARN)
            if (crossed && (resets == null || resets > Time.now())) {
                val label = labelOf(provider, name, w["label"]?.toString())
                notifications.create(
                    "budget", null, "$label ${Math.round(pct!! * 100)}% used",
                    listOfNotNull(resets?.let { "Resets in ${untilText(it)}." }, "keel pauses before the next agent at 95% (Settings).").joinToString(" "),
                    "/budget",
                )
            }
        }
    }

    fun windows(provider: String): List<Pair<UsageWindow, Pair<String, String>>> = jdbc.query(
        """SELECT "window", used_pct, used, cap, remaining, resets_at, status, source, fetched_at, raw_json FROM provider_usage
           WHERE provider = ? ORDER BY CASE "window" WHEN 'five_hour' THEN 0 WHEN 'seven_day' THEN 1 ELSE 2 END, "window"""",
        { rs, _ ->
            val raw = Json.readMap(rs.getString(10))
            fun d(i: Int) = rs.getDouble(i).takeIf { !rs.wasNull() }
            UsageWindow(rs.getString(1), raw["label"]?.toString() ?: LABELS[rs.getString(1)] ?: rs.getString(1), d(2), d(3), d(4), d(5),
                rs.getString(6), rs.getString(7)) to (rs.getString(8) to rs.getString(9))
        },
        provider,
    )

    companion object {
        const val WARN = 0.8
        val LABELS = mapOf("five_hour" to "5-hour", "seven_day" to "weekly", "month" to "monthly")
        val NAMES = mapOf("claude" to "Claude", "codex" to "Codex", "copilot" to "Copilot", "api" to "API keys")

        fun labelOf(provider: String, window: String, label: String?): String {
            val name = NAMES[provider] ?: provider
            return if (window == "month") "$name monthly premium requests" else "$name ${label ?: LABELS[window] ?: window} window"
        }

        /** The engine sends unix seconds; events stored before may hold ISO text. */
        fun resetsIso(v: Any?): String? = when (v) {
            null -> null
            is Number -> Instant.ofEpochSecond(v.toLong()).toString()
            else -> v.toString().toLongOrNull()?.let { Instant.ofEpochSecond(it).toString() }
                ?: runCatching { Instant.parse(v.toString()).toString() }.getOrNull()
        }

        fun untilText(iso: String): String {
            val mins = runCatching { Duration.between(Instant.now(), Instant.parse(iso)).toMinutes() }.getOrDefault(0L).coerceAtLeast(0)
            return when {
                mins < 60 -> "$mins min"
                mins < 48 * 60 -> if (mins % 60 == 0L) "${mins / 60}h" else "${mins / 60}h ${mins % 60}m"
                else -> "${mins / 1440} days"
            }
        }
    }
}

/**
 * The usage cards. Only providers that are set up (a login or a key saved, or in the environment) get one:
 * claude from its runs (provider_usage, "as of the last run"), codex and copilot read live through the engine
 * (cached 60 s), API keys from keel's own count of api-mode jobs this month against the cap you set.
 */
@Service
class ProviderUsageService(
    private val jdbc: JdbcTemplate,
    private val secrets: SecretService,
    private val engine: EngineClient,
    private val store: ProviderUsageStore,
    private val kv: KvStore,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val cache = ConcurrentHashMap<String, Pair<Long, ProviderUsage>>()

    fun list(): List<ProviderUsage> = buildList {
        if (secrets.loginFor("claude") != null) add(claude())
        if (secrets.loginFor("codex") != null) add(live("codex", force = false))
        if (secrets.loginFor("copilot") != null) add(live("copilot", force = false))
        if (listOf("claude", "codex", "copilot").any { secrets.keyForProvider(it) != null }) add(api())
    }

    fun refresh(id: String): ProviderUsage = when (id) {
        "codex", "copilot" -> {
            if (secrets.loginFor(id) == null) throw BadRequest("${ProviderUsageStore.NAMES[id]} is not set up", "Save its login in Connections first.")
            live(id, force = true)
        }
        "claude" -> probeClaude()
        "api" -> api()
        else -> throw BadRequest("No usage source for \"$id\"", "Pick one of: claude, codex, copilot, api")
    }

    /** The latest windows for the engine's pause rule, sent with each new flow. */
    fun windowsForEngine(): List<Map<String, Any?>> = listOf("claude", "codex", "copilot").flatMap { p ->
        store.windows(p).map { (w, meta) ->
            mapOf("provider" to p, "window" to w.window, "used_pct" to w.usedPct, "resets_at" to w.resetsAt, "source" to meta.first, "fetched_at" to meta.second)
        }
    }

    private fun stored(provider: String, source: String, live: Boolean, canRefresh: Boolean, error: String? = null, note: String? = null): ProviderUsage {
        val rows = store.windows(provider)
        val newest = rows.maxByOrNull { it.second.second }?.second
        return ProviderUsage(provider, ProviderUsageStore.NAMES.getValue(provider), "subscription", newest?.first ?: source, newest?.second,
            rows.map { it.first }, live, canRefresh, error, note)
    }

    private fun claude(): ProviderUsage {
        val rows = store.windows("claude")
        return stored("claude", "last run", live = false, canRefresh = true,
            note = if (rows.isEmpty()) "No numbers yet: they arrive with the next Claude run, or press refresh (one tiny Haiku call)." else null)
    }

    /** Refresh for Claude: one tiny Haiku call through the engine's provider test, then the windows that run reported. */
    private fun probeClaude(): ProviderUsage {
        val login = secrets.loginFor("claude") ?: throw BadRequest("Claude is not set up", "Save the Claude login token in Connections first.")
        val error = runCatching {
            val test = engine.providerTest(mapOf("provider" to "claude", "mode" to "subscription", "model" to "haiku", "key" to login))
            if (test["ok"]?.asBoolean() != true) return@runCatching test["error"]?.asText() ?: "The Haiku check did not answer OK."
            val res = engine.post("/providers/usage", mapOf("provider" to "claude"))
            if (res["ok"]?.asBoolean() == true) {
                store.store("claude", windowsOf(res), "refresh (one Haiku call)", res["at"]?.asText() ?: Time.now())
                null
            } else res["error"]?.asText() ?: "Claude reported no numbers."
        }.getOrElse { it.message ?: "The engine did not answer." }
        return stored("claude", "last run", live = false, canRefresh = true, error = error)
    }

    private fun live(provider: String, force: Boolean): ProviderUsage {
        val now = System.currentTimeMillis()
        if (!force) cache[provider]?.let { (at, u) -> if (now - at < CACHE_MS) return u }
        val source = if (provider == "copilot") "GitHub (unofficial)" else "codex app-server"
        val error = runCatching {
            val res = engine.post("/providers/usage", mapOf("provider" to provider, "key" to secrets.loginFor(provider)))
            if (res["ok"]?.asBoolean() == true) {
                store.store(provider, windowsOf(res), source, res["at"]?.asText() ?: Time.now())
                null
            } else res["error"]?.asText() ?: "No answer."
        }.getOrElse { it.message ?: "The engine did not answer." }
        if (error != null) log.info("{} usage could not be read: {}", provider, error)
        val out = if (error != null && provider == "copilot") copilotFallback(error) else
            stored(provider, source, live = true, canRefresh = true, error = error?.let { "Could not read it now: $it" })
        cache[provider] = now to out
        return out
    }

    /** GitHub did not answer: keel's own premium-request count this month against the cap you set (Budget › Accounts). */
    private fun copilotFallback(error: String): ProviderUsage {
        val rows = store.windows("copilot")
        if (rows.isNotEmpty()) return stored("copilot", "GitHub (unofficial)", live = true, canRefresh = true, error = "Could not read it now: $error")
        val used = (jdbc.queryForObject("SELECT COALESCE(SUM(premium_requests), 0) FROM agent_calls WHERE provider = 'copilot' AND started_at >= ?",
            Long::class.java, monthStart()) ?: 0L).toDouble()
        val cap = manualCap("copilot")
        return ProviderUsage("copilot", "Copilot", "subscription", "keel's own count", Time.now(),
            listOf(UsageWindow("month", "monthly", if (cap > 0) used / cap else null, used, cap.takeIf { it > 0 }, if (cap > 0) cap - used else null, nextMonth())),
            live = false, canRefresh = true, error = "GitHub did not answer ($error). Showing keel's own count and your cap.")
    }

    private fun api(): ProviderUsage {
        val spent = jdbc.queryForObject("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_calls WHERE mode = 'api' AND started_at >= ?",
            Double::class.java, monthStart()) ?: 0.0
        val cap = manualCap("api")
        return ProviderUsage("api", "API keys", "api", "keel's own count", Time.now(),
            listOf(UsageWindow("month", "this month", if (cap > 0) spent / cap else null, spent, cap.takeIf { it > 0 },
                if (cap > 0) cap - spent else null, nextMonth())),
            live = false, canRefresh = false, note = "USD spent through API keys this month (subscription runs are not counted).")
    }

    private fun manualCap(id: String): Double =
        (kv.get<List<Limit>>(BudgetService.LIMITS) ?: BudgetService.DEFAULT_LIMITS).firstOrNull { it.id == id }?.cap ?: 0.0

    private fun windowsOf(res: JsonNode): List<Map<String, Any?>> =
        res["windows"]?.map { Json.mapper.convertValue(it, Map::class.java).entries.associate { (k, v) -> k.toString() to v } }.orEmpty()

    companion object {
        const val CACHE_MS = 60_000L
        fun monthStart(): String = LocalDate.now(ZoneOffset.UTC).withDayOfMonth(1).atStartOfDay().toInstant(ZoneOffset.UTC).toString()
        fun nextMonth(): String = LocalDate.now(ZoneOffset.UTC).withDayOfMonth(1).plusMonths(1).atStartOfDay().toInstant(ZoneOffset.UTC).toString()
    }
}

private fun Map<String, Any?>.num(key: String): Double? = when (val v = this[key]) {
    is Number -> v.toDouble()
    is String -> v.toDoubleOrNull()
    else -> null
}

@RestController
class ProviderUsageController(private val usage: ProviderUsageService) {
    @GetMapping("/api/usage/providers")
    fun list(): List<ProviderUsage> = usage.list()

    @PostMapping("/api/usage/providers/{id}/refresh")
    fun refresh(@PathVariable id: String): ProviderUsage = usage.refresh(id)
}
