package keel.api.mcp

import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.common.BadRequest
import keel.api.common.ErrorBody
import keel.api.common.Hash
import keel.api.common.KeelProperties
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.http.MediaType
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.security.SecureRandom

/**
 * v0.16.1 MCP level tokens (read, write, admin). keel's MCP server gets the one for its level from POST /api/mcp/token,
 * which needs the internal token (KEEL_INTERNAL_TOKEN: keel2 and the engine have it, agents never do), and sends it as
 * X-Keel-Mcp-Token; [McpLevelGuard] checks it. v0.18.1: each token is an HMAC of the internal token and its level, so it
 * stays the same while keel runs, also across an api restart (a plugin install restarts the api; the MCP servers of a
 * client and of keel's running agents keep working). Without an internal token they are random, as before.
 */
@Component
class McpTokens(props: KeelProperties) {
    private val tokens: Map<String, String> = LEVELS.associateWith { level ->
        if (props.internalToken.isEmpty()) newToken() else hmac(props.internalToken, "keel-mcp:$level")
    }

    fun tokenFor(level: String): String = tokens[level] ?: throw BadRequest("No MCP level called \"$level\"", "One of: ${LEVELS.joinToString(", ")}")

    /** The level a token proves (null: not one of ours). Every token is compared, in constant time. */
    fun levelOf(token: String?): String? {
        if (token.isNullOrEmpty()) return null
        var found: String? = null
        for ((level, t) in tokens) if (same(token, t)) found = level
        return found
    }

    companion object {
        val LEVELS = listOf("read", "write", "admin")
        fun rank(level: String): Int = LEVELS.indexOf(level)
        fun same(a: String, b: String): Boolean = Hash.constantTimeEquals(a, b)
        private val random = SecureRandom()
        private fun newToken(): String = Hash.hex(ByteArray(32).also { random.nextBytes(it) })
        private fun hmac(key: String, text: String): String {
            val mac = javax.crypto.Mac.getInstance("HmacSHA256")
            mac.init(javax.crypto.spec.SecretKeySpec(key.toByteArray(), "HmacSHA256"))
            return Hash.hex(mac.doFinal(text.toByteArray()))
        }
    }
}

data class McpTokenRequest(val level: String = "")

/**
 * v0.16.1 the api refuses an MCP call above the level its token proves. It only looks at requests that say they come
 * from MCP (X-Keel-Via: mcp, or an X-Keel-Mcp-Token header) and at the MCP-only route POST /api/projects/{pid}/actions/{name};
 * the web app sends neither header, so it works as before. Without a valid token a request is at the read level.
 * v0.18.1: [keel.api.auth.LocalAuth] runs first: a request reaches this guard only with a credential; this guard
 * keeps an MCP call within its level, and a person (browser, TUI) or keel itself (internal token) is not limited here.
 * GET is fine at read; other methods need write; [admin] routes need admin; an action needs the level the engine gives
 * it ([READ_ACTIONS], [ADMIN_ACTIONS], others write). It runs inside [McpAudit], so a refused call is still recorded.
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE)
class McpLevelGuard(private val tokens: McpTokens, private val mapper: ObjectMapper) : OncePerRequestFilter() {

    override fun shouldNotFilter(request: HttpServletRequest): Boolean {
        val path = keel.api.common.ApiPath.of(request)
        if (!path.startsWith("/api/") || path == "/api/mcp/token") return true
        val who = request.getAttribute(keel.api.auth.LocalAuth.WHO_ATTR)
        if (who != null && who != "mcp" && request.getHeader("X-Keel-Via") != "mcp") return true
        return request.getHeader("X-Keel-Via") != "mcp" && request.getHeader(TOKEN_HEADER) == null && action(request) == null
    }

    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        val has = tokens.levelOf(request.getHeader(TOKEN_HEADER)) ?: "read"
        val need = need(request)
        if (McpTokens.rank(has) < McpTokens.rank(need)) {
            response.status = HttpServletResponse.SC_FORBIDDEN
            response.contentType = MediaType.APPLICATION_JSON_VALUE
            val flag = if (need == "admin") "--admin" else "--write"
            mapper.writeValue(response.outputStream, ErrorBody("This needs the MCP $need level: run keel2 mcp $flag",
                "This MCP server is at the $has level; a level comes with a token from keel, not from the client."))
            return
        }
        request.setAttribute(LEVEL_ATTR, has)
        chain.doFilter(request, response)
    }

    /** The level a request needs. */
    fun need(request: HttpServletRequest): String {
        val method = request.method
        if (method == "GET" || method == "HEAD" || method == "OPTIONS") return "read"
        action(request)?.let { name ->
            return when (name) { in READ_ACTIONS -> "read"; in ADMIN_ACTIONS -> "admin"; else -> "write" }
        }
        val path = keel.api.common.ApiPath.of(request).trimEnd('/')
        if (method == "POST" && DB_QUERY.matches(path)) return "read"     // a change needs write: DatabaseController checks the body
        // step 4: keel_plugin_request is a read-level tool (agents run keel2 mcp --read-only); it only opens an Inbox
        // request a person approves, and the marketplace rule "agents may ask" still decides (PluginRequests)
        if (method == "POST" && path == PLUGIN_REQUESTS) return "read"
        if (method == "POST" && READ_POSTS.any { it.matches(path) }) return "read"
        if (ADMIN.any { (m, re) -> m == method && re.matches(path) }) return "admin"
        if (ADMIN_PREFIXES.any { path == it || path.startsWith("$it/") }) return "admin"
        return "write"
    }

    private fun action(request: HttpServletRequest): String? =
        if (request.method == "POST") ACTION.find(keel.api.common.ApiPath.of(request).trimEnd('/'))?.groupValues?.get(1) else null

    companion object {
        const val TOKEN_HEADER = "X-Keel-Mcp-Token"
        /** The level an MCP request proved (only set on MCP requests): "read", "write" or "admin". */
        const val LEVEL_ATTR = "keel.mcp.level"

        /**
         * The engine actions MCP may run at the read level. Twin of the "read" names in engine
         * runtime/action_docs.py ACTION_LEVELS: change both together (McpLevelsApiTest and engine test_mcp.py pin them).
         */
        val READ_ACTIONS = setOf("audit", "trace", "trace_strict", "arch", "arch_detect", "coverage_report", "lint_scope",
            "lint_report", "review_lenses", "ci_report")

        /** The engine actions that need the admin level. Twin of the "admin" names in engine action_docs.ACTION_LEVELS. */
        val ADMIN_ACTIONS = setOf("arch_set", "open_pr")

        private val ACTION = Regex("^/api/projects/[^/]+/actions/([^/]+)$")
        private val DB_QUERY = Regex("^/api/projects/[^/]+/db/query$")
        private const val PLUGIN_REQUESTS = "/api/plugins/requests"
        private val ADMIN = listOf(
            // 0.18.5 stopping a flow is write (keel TUI and the dashboard let the person do it; the MCP tool asks first)
            "DELETE" to Regex("^/api/projects/[^/]+/flows/[^/]+$"),
            "POST" to Regex("^/api/projects$"),
            "POST" to Regex("^/api/projects/bulk$"),
            "DELETE" to Regex("^/api/projects/[^/]+$"),
            "PUT" to Regex("^/api/workflows/[^/]+$"),
            "DELETE" to Regex("^/api/workflows/[^/]+$"),
            // v0.17.2 keel's rules: a change decides what agents may do
            "PUT" to Regex("^/api/projects/[^/]+/rules/config$"),
            "POST" to Regex("^/api/projects/[^/]+/rules/library/[^/]+/install$"),
            // 0.18.5 a new rule pack is write (it only refuses; keel_rule_create asks the person first); changing or
            // removing one stays admin
            "PUT" to Regex("^/api/projects/[^/]+/rules/packs/[^/]+$"),
            "DELETE" to Regex("^/api/projects/[^/]+/rules/packs/[^/]+$"),
            "POST" to Regex("^/api/projects/[^/]+/rule-packs/(install|uninstall|market/install)$"),
            // 0.18.6 skills: a new one, an import, the agents it is on and a marketplace install are write (each MCP tool
            // asks the person first); removing one is admin
            "DELETE" to Regex("^/api/projects/[^/]+/skills/[^/]+$"),
            // keel's own configuration and what can reach the machine or the account: an agent with MCP write must not
            // install a plugin, store a secret, sign in somewhere, change a setting or approve a plugin install. No MCP
            // tool calls these except keel_connect_github, which is an admin tool.
            "POST" to Regex("^/api/plugins/(install|install-file|restart)$"),
            "POST" to Regex("^/api/plugins/[^/]+/(update|rollback)$"),
            "PUT" to Regex("^/api/plugins/(sources|rules)$"),
            "PUT" to Regex("^/api/plugins/[^/]+$"),
            "DELETE" to Regex("^/api/plugins/[^/]+$"),
            "POST" to Regex("^/api/marketplace/refresh$"),
            "PUT" to Regex("^/api/secrets/[^/]+$"),
            "DELETE" to Regex("^/api/secrets/[^/]+$"),
            "PUT" to Regex("^/api/settings/general$"),
            "PUT" to Regex("^/api/connections/[^/]+$"),
            "POST" to Regex("^/api/connections/[^/]+/test$"),
            "POST" to Regex("^/api/logins$"),
            "POST" to Regex("^/api/logins/[^/]+/code$"),
            "DELETE" to Regex("^/api/logins/[^/]+$"),
            "POST" to Regex("^/api/approvals/[^/]+/decide$"),
        )
        /** Every method under these prefixes needs admin (all of /api/github: sign-in, sign-out, tokens; the Database
         *  plugin's /api/databases: a connection every project sees, its test and its server's databases). */
        private val ADMIN_PREFIXES = listOf("/api/github", "/api/databases")
        /** Reads that are POSTs (they change nothing): a rule pack's check and dry-run. */
        private val READ_POSTS = listOf(
            Regex("^/api/projects/[^/]+/rules/check$"),
            // keel's ASCII diagrams (keel TUI, keel2 mcp's diagram tools): drawing changes nothing
            Regex("^/api/diagrams/ascii$"),
            // 0.18.5 a workflow's check (keel_workflow_check): it saves nothing
            Regex("^/api/projects/[^/]+/workflows/check$"),
            // 0.18.9 a stack's check (keel_stack_check): it saves nothing. Saving, removing, turning off and a marketplace
            // install are write (keel_stack_create, _remove and _market_install ask the person first)
            Regex("^/api/projects/[^/]+/stacks/check$"),
            // 0.18.64 keel's spec check and citation check on a file of the project (keel_spec_check, keel_citations_check)
            Regex("^/api/projects/[^/]+/(specs|citations)/check$"),
            // 0.18.64 a fetch of the project's remotes (keel_fetch_remotes): it updates only the remote-tracking refs, no
            // branch and no file. A flow branch's sync (POST /api/threads/{id}/sync, keel_branch_sync) is write.
            Regex("^/api/projects/[^/]+/remotes/fetch$"),
        )
    }
}
