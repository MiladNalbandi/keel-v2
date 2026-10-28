package keel.api.mcp

import jakarta.annotation.PreDestroy
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.auth.LocalAuth
import keel.api.common.ApiPath
import keel.api.common.KvStore
import keel.api.common.Time
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.filter.OncePerRequestFilter
import java.time.LocalDate
import java.util.concurrent.ConcurrentHashMap

/** One AI tool (or terminal app) keel has seen: its name as it says it, how it came (mcp, tui, xal), its level. */
data class McpClientSeen(
    val client: String,
    val via: String,
    val level: String,
    val firstAt: String,
    val lastAt: String,
    val lastTool: String? = null,
    val day: String = "",
    val today: Int = 0,
    val total: Long = 0,
)

/**
 * keel 0.18.66 the AI tools that called keel: Help › Start here's step 3 turns green on the first call of one, and
 * Settings › Access lists them with their level, last call and calls today. Every MCP-token request counts, reads too
 * (McpAudit records only the changes, on the project's timeline): keel's MCP server says the client's name on every
 * request (X-Keel-Client). keel's own agents' MCP server says X-Keel-Mcp-Kind: agent and is left out: it is not a
 * person's tool. keel TUI and xal count as "the terminal" (they act as the person).
 * Kept in memory and written to the kv table every few seconds when something changed, so a busy client costs no
 * database write per call.
 */
@Component
class McpClients(private val kv: KvStore) {
    private val seen = ConcurrentHashMap<String, McpClientSeen>()
    @Volatile private var dirty = false

    init {
        runCatching { kv.get<List<McpClientSeen>>(KEY) }.getOrNull()?.forEach { seen[key(it.via, it.client, it.level)] = it }
    }

    fun record(via: String, client: String, level: String, tool: String?) {
        val now = Time.now()
        val day = LocalDate.now().toString()
        seen.compute(key(via, client, level)) { _, old ->
            if (old == null) McpClientSeen(client, via, level, now, now, tool, day, 1, 1)
            else old.copy(lastAt = now, lastTool = tool ?: old.lastTool, day = day,
                today = if (old.day == day) old.today + 1 else 1, total = old.total + 1)
        }
        if (seen.size > MAX) seen.values.minByOrNull { it.lastAt }?.let { seen.remove(key(it.via, it.client, it.level)) }
        dirty = true
    }

    /** Newest first; a count of today is 0 when its last call was on another day. */
    fun list(): List<McpClientSeen> {
        val day = LocalDate.now().toString()
        return seen.values.map { if (it.day == day) it else it.copy(today = 0) }.sortedByDescending { it.lastAt }
    }

    @Scheduled(fixedDelay = 5_000, initialDelay = 5_000)
    @PreDestroy
    fun flush() {
        if (!dirty) return
        dirty = false
        runCatching { kv.put(KEY, seen.values.toList()) }.onFailure { dirty = true }
    }

    fun clear() {
        seen.clear()
        kv.delete(KEY)
    }

    companion object {
        const val KEY = "mcp.clients"
        private const val MAX = 50
        private fun key(via: String, client: String, level: String) = "$via|$client|$level"
    }
}

/**
 * Counts each call an AI tool or terminal app made ([McpClients]). It runs after [LocalAuth] (who the caller proved to
 * be) and records after the call, refused or not: a tool that reached keel is connected, even when its level was too low.
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE - 2)
class McpClientsFilter(private val clients: McpClients, private val tokens: McpTokens) : OncePerRequestFilter() {

    override fun shouldNotFilter(request: HttpServletRequest): Boolean {
        val path = ApiPath.of(request)
        return !path.startsWith("/api/") || path == "/api/mcp/token" || path == "/api/mcp/clients"
    }

    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        try {
            chain.doFilter(request, response)
        } finally {
            runCatching { record(request) }
        }
    }

    private fun record(request: HttpServletRequest) {
        val name = request.getHeader("X-Keel-Client")?.trim()?.take(80)?.ifBlank { null }
        val tool = request.getHeader("X-Keel-Tool")?.trim()?.take(80)?.ifBlank { null }
        // an open route (GET /api/health: keel_health) passes LocalAuth without a "who": the token still says MCP
        val level = tokens.levelOf(request.getHeader(McpLevelGuard.TOKEN_HEADER))
        val who = request.getAttribute(LocalAuth.WHO_ATTR)
        if (level != null && (who == null || who == "mcp")) {
            if (request.getHeader(KIND_HEADER) == "agent") return
            clients.record("mcp", name ?: "an MCP client", level, tool)
            return
        }
        val via = request.getHeader("X-Keel-Via")
        if (via in TERMINALS && who in setOf("person", "browser")) {
            clients.record(via!!, name ?: if (via == "tui") "keel TUI" else via, "you", tool)
        }
    }

    companion object {
        /** keel's own agents' MCP server says so (engine mcp_api.py): not a person's tool */
        const val KIND_HEADER = "X-Keel-Mcp-Kind"
        val TERMINALS = setOf("tui", "xal")
    }
}

@RestController
class McpClientsController(private val clients: McpClients) {
    /** keel 0.18.66 the AI tools and terminal apps that called keel (Help › Start here, Settings › Access). */
    @GetMapping("/api/mcp/clients")
    fun list(): Map<String, Any> = mapOf("clients" to clients.list())
}
