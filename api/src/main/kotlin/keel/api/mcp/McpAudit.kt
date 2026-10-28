package keel.api.mcp

import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.common.ApiPath
import keel.api.common.Time
import keel.api.events.EngineEvent
import keel.api.events.EventService
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import org.springframework.web.util.ContentCachingRequestWrapper

/**
 * v0.16.0 every write keel's MCP server sends (`keel2 mcp --write | --admin`) says who acted: X-Keel-Via: mcp,
 * X-Keel-Client (the client's own name, a label) and X-Keel-Tool. After the request this records an `mcp.call` event
 * on the project (and thread), so its timeline shows "via MCP · <client>: <tool>". Reads are not recorded.
 * keel TUI (cli/, `keel2 tui`) does the same with X-Keel-Via: tui and its command in X-Keel-Tool: a `tui.call` event,
 * "via keel TUI · /approve"; the xal client (clients/xal) with X-Keel-Via: xal, an `xal.call` event. Both act as the
 * person at the keyboard, like the dashboard ([McpLevelGuard] leaves them be).
 * keel 0.18.55: a call whose JSON body has `sql` (the Database plugin's query, keel_db_query and keel_db_change, at every
 * level) records that SQL too, cut at [SQL_MAX] characters, with the secrets in its literals as ••• ([auditSql]).
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE - 1)   // before McpLevelGuard, so a refused call is recorded too
class McpAudit(private val events: EventService, private val jdbc: JdbcTemplate, private val mapper: ObjectMapper) : OncePerRequestFilter() {

    override fun shouldNotFilter(request: HttpServletRequest): Boolean =
        request.method == "GET" || viaOf(request) == null || !ApiPath.of(request).startsWith("/api/") ||
            // 0.18.6 which project and chat a front end shows (ui/UiFocus.kt) is not a change to record
            ApiPath.of(request) == "/api/ui/focus"

    /**
     * 0.18.64 who acted: a request with an MCP level token is always an MCP call (until 0.18.63 only one that also sent
     * X-Keel-Via: mcp was recorded, so a client that left the header out acted unseen); else keel TUI or xal by their
     * header; else null (not recorded).
     */
    private fun viaOf(request: HttpServletRequest): String? =
        if (request.getAttribute(keel.api.auth.LocalAuth.WHO_ATTR) == "mcp") "mcp" else request.getHeader("X-Keel-Via")?.takeIf { it in VIAS }

    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        val cached = ContentCachingRequestWrapper(request, BODY_MAX)
        try {
            chain.doFilter(cached, response)
        } finally {
            runCatching { record(cached, response.status) }.onFailure { logger.warn("could not record an MCP call: ${it.message}") }
        }
    }

    /** The `sql` of the call's JSON body (read again if nobody read it: a refused call), ready for the audit; or null. */
    private fun sqlOf(request: ContentCachingRequestWrapper): String? {
        if (request.contentType?.contains("json", ignoreCase = true) != true) return null
        var body = request.contentAsByteArray
        if (body.isEmpty()) body = runCatching { request.inputStream.readNBytes(BODY_MAX); request.contentAsByteArray }.getOrDefault(body)
        if (body.isEmpty()) return null
        val sql = runCatching { mapper.readTree(body)?.get("sql") }.getOrNull()?.takeIf { it.isTextual }?.asText() ?: return null
        return auditSql(sql)
    }

    private fun record(request: ContentCachingRequestWrapper, status: Int) {
        val path = ApiPath.of(request)
        val via = viaOf(request) ?: return
        val tid = THREAD.find(path)?.groupValues?.get(1) ?: INBOX.find(path)?.groupValues?.get(1)
        val pid = PROJECT.find(path)?.groupValues?.get(1)
            ?: tid?.let { jdbc.query("SELECT project_id FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, it).firstOrNull() }
            ?: return
        val mcp = via == "mcp"
        val client = request.getHeader("X-Keel-Client")?.take(80)?.ifBlank { null }
            ?: when (via) { "tui" -> "keel TUI"; "xal" -> "xal"; else -> "an MCP client" }
        val tool = request.getHeader("X-Keel-Tool")?.take(80)?.ifBlank { null } ?: if (mcp) "a tool" else "a command"
        val data = mutableMapOf<String, Any?>(
            "via" to via, "client" to client, "tool" to tool, "method" to request.method, "path" to path,
            "ok" to (status < 400), "status" to status,
        )
        sqlOf(request)?.let { data["sql"] = it }
        events.ingest(listOf(EngineEvent("$via.call", tid ?: via, pid, via, Time.now(), null, data)))
    }

    companion object {
        /** who may say they acted: keel's MCP server, keel TUI and the xal client; the event is `<via>.call` */
        val VIAS = setOf("mcp", "tui", "xal")
        const val SQL_MAX = 2000
        private const val BODY_MAX = 64 * 1024
        private const val MASK = "•••"
        private const val HIDDEN_COLUMN = "\\w*(?:pass(?:word|wd)?|secret|token|hash|salt|api_?key|private_?key|credential)\\w*"
        private const val LIT = "'(?:[^']|'')*'"
        /** `password_hash = '…'`, `token <> '…'`, `secret LIKE '…'` */
        private val SECRET_CMP = Regex("(?i)(\\b$HIDDEN_COLUMN[`\"\\]]?\\s*(?:=|<>|!=|:=|\\bi?like\\b)\\s*)$LIT")
        /** `token IN ('…', '…')` */
        private val SECRET_IN = Regex("(?i)(\\b$HIDDEN_COLUMN[`\"\\]]?\\s+in\\s*)\\(([^)]*)\\)")
        /** `INSERT INTO t (…, password, …) VALUES (…)`: every literal of its values */
        private val INSERT = Regex("(?is)^(\\s*insert\\s+into\\s+[^(]+\\(([^)]*)\\)\\s*values)(.*)$")
        private val IDENTIFIED = Regex("(?i)(\\bidentified\\s+(?:with\\s+\\S+\\s+)?by\\s+)$LIT")
        private val URL_PASSWORD = Regex("(://[^:/@\\s']+:)[^@\\s']+@")
        private val LITERAL = Regex(LIT)

        /** The SQL as the audit keeps it: secrets in its literals as •••, at most [SQL_MAX] characters (then …). */
        fun auditSql(sql: String): String {
            var out = sql
            INSERT.matchEntire(out)?.let { m ->
                if (Regex("(?i)\\b$HIDDEN_COLUMN\\b").containsMatchIn(m.groupValues[2])) {
                    out = m.groupValues[1] + LITERAL.replace(m.groupValues[3]) { "'$MASK'" }
                }
            }
            out = SECRET_CMP.replace(out) { "${it.groupValues[1]}'$MASK'" }
            out = SECRET_IN.replace(out) { "${it.groupValues[1]}(${LITERAL.replace(it.groupValues[2]) { "'$MASK'" }})" }
            out = IDENTIFIED.replace(out) { "${it.groupValues[1]}'$MASK'" }
            out = URL_PASSWORD.replace(out) { "${it.groupValues[1]}$MASK@" }
            return if (out.length > SQL_MAX) out.take(SQL_MAX) + "…" else out
        }
        private val PROJECT = Regex("^/api/projects/([^/]+)")
        private val THREAD = Regex("^/api/threads/([^/]+)")
        private val INBOX = Regex("^/api/inbox/(?!tasks/)([^/]+)/act")
    }
}

data class RunAction(val args: Map<String, Any?>? = null)
