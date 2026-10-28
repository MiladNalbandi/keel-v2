package keel.api.mcp

import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.common.BadRequest
import keel.api.common.ErrorBody
import keel.api.common.Hash
import keel.api.common.KeelProperties
import org.springframework.beans.factory.ObjectProvider
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
 * 0.18.64 a plugin's own routes an MCP server may call below the admin level: the routes its read and acting MCP tools
 * use. Any other non-GET route of a plugin needs admin ([McpLevelGuard]: what no list names is admin).
 */
interface McpRouteLevels {
    /** (method, path) a read token may call: a POST that changes nothing. */
    val read: List<Pair<String, Regex>> get() = emptyList()

    /** (method, path) a write token may call: what the plugin's acting MCP tools send. */
    val write: List<Pair<String, Regex>> get() = emptyList()
}

/**
 * v0.16.1 the api refuses an MCP call above the level its token proves. It only looks at requests that say they come
 * from MCP (X-Keel-Via: mcp, or an X-Keel-Mcp-Token header) and at the MCP-only route POST /api/projects/{pid}/actions/{name};
 * the web app sends neither header, so it works as before. Without a valid token a request is at the read level.
 * v0.18.1: [keel.api.auth.LocalAuth] runs first: a request reaches this guard only with a credential; this guard
 * keeps an MCP call within its level, and a person (browser, TUI) or keel itself (internal token) is not limited here.
 * GET is fine at read; [READ_POSTS] are read, [WRITE] routes write, [ADMIN] routes admin; an action needs the level
 * the engine gives it ([READ_ACTIONS], [ADMIN_ACTIONS], others write). It runs inside [McpAudit], so a refused call is
 * still recorded.
 * 0.18.64 a non-GET route no list names needs admin (until 0.18.63 it was write: a new route, such as adding an MCP
 * server that runs any command, was open to every write token). McpRouteLevelsApiTest fails on a core route no list
 * names; a plugin names its own read and write routes ([McpRouteLevels]).
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE)
class McpLevelGuard(
    private val tokens: McpTokens,
    private val mapper: ObjectMapper,
    private val plugins: ObjectProvider<McpRouteLevels>,
) : OncePerRequestFilter() {

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
        return levels(method, keel.api.common.ApiPath.of(request)).firstOrNull() ?: "admin"
    }

    /**
     * 0.18.64 the levels the lists give a non-GET route (none: no list names it, so it needs admin). The read lists come
     * first, then admin, then write: a route in two lists gets the first (McpRouteLevelsApiTest wants one list each).
     */
    fun levels(method: String, path: String): List<String> {
        val p = path.trimEnd('/')
        fun on(list: List<Pair<String, Regex>>) = list.any { (m, re) -> m == method && re.matches(p) }
        val mine = plugins.orderedStream().toList()
        val out = mutableListOf<String>()
        if (on(READ_POSTS) || mine.any { on(it.read) }) out += "read"
        if (on(ADMIN) || ADMIN_PREFIXES.any { p == it || p.startsWith("$it/") }) out += "admin"
        if (on(WRITE) || mine.any { on(it.write) }) out += "write"
        return out
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

        /** The MCP-only route of the engine's actions: its level is the action's ([need]). */
        val ACTION = Regex("^/api/projects/[^/]+/actions/([^/]+)$")
        private fun post(re: String) = "POST" to Regex(re)
        private fun put(re: String) = "PUT" to Regex(re)
        private fun delete(re: String) = "DELETE" to Regex(re)

        /** Reads that are POSTs (they change nothing). */
        val READ_POSTS = listOf(
            // a rule pack's check and dry-run
            post("^/api/projects/[^/]+/rules/check$"),
            // keel's ASCII diagrams (keel TUI, keel2 mcp's diagram tools): drawing changes nothing
            post("^/api/diagrams/ascii$"),
            // 0.18.5 a workflow's check (keel_workflow_check): it saves nothing
            post("^/api/projects/[^/]+/workflows/check$"),
            // 0.18.9 a stack's check (keel_stack_check): it saves nothing
            post("^/api/projects/[^/]+/stacks/check$"),
            // 0.18.64 keel's spec check and citation check on a file of the project (keel_spec_check, keel_citations_check)
            post("^/api/projects/[^/]+/(specs|citations)/check$"),
            // 0.18.64 a fetch of the project's remotes (keel_fetch_remotes): it updates only the remote-tracking refs, no
            // branch and no file. A flow branch's sync (POST /api/threads/{id}/sync, keel_branch_sync) is write.
            post("^/api/projects/[^/]+/remotes/fetch$"),
            // step 4: keel_plugin_request is a read-level tool (agents run keel2 mcp --read-only); it only opens an Inbox
            // request a person approves, and the marketplace rule "agents may ask" still decides (PluginRequests)
            post("^/api/plugins/requests$"),
            // the Database plugin's query: a change needs write (DatabaseController checks the body)
            post("^/api/projects/[^/]+/db/query$"),
            // 0.18.64 an estimate of unsaved workflow YAML, and the look at the uncommitted files: both change nothing
            post("^/api/projects/[^/]+/estimate$"),
            post("^/api/projects/[^/]+/doctor/workspace$"),
        )

        /**
         * 0.18.64 what keel's MCP write tools send (engine keel_engine/mcp_*.py), and what is like it (a new workflow
         * written in the editor, as keel_workflow_create imports one). Each acting tool asks the person first where it
         * changes something hard to undo (mcp_levels.CONFIRM).
         */
        val WRITE = listOf(
            // flows: start one (keel_start_flow; its "ignore this path" is the doctor's exclude), stop, go on, answer
            post("^/api/projects/[^/]+/flows$"),
            post("^/api/projects/[^/]+/doctor/workspace/exclude$"),
            post("^/api/threads/[^/]+/(stop|resume|mode|models|rewind|sync)$"),
            put("^/api/threads/[^/]+/document$"),
            // 0.18.65 the base branch and the canonical remote (keel_base_branch asks the person first)
            put("^/api/projects/[^/]+/base$"),
            // keel_answer and keel_approve_gate answer a flow's question (an approval the api asked itself needs more:
            // InboxService.act)
            post("^/api/inbox/[^/]+/act$"),
            post("^/api/projects/[^/]+/docs/history/restore$"),
            // keel_state's close of keel v1's leftover state file
            post("^/api/projects/[^/]+/keel-state/close$"),
            // an acting tool asks the person in the Inbox (0.18.64: also the confirmation of a client without forms)
            post("^/api/projects/[^/]+/plugins/ask$"),
            // agents: a new one (keel_agent_create), its model and its skills (keel_agent_model, keel_skill_attach)
            post("^/api/projects/[^/]+/agents$"),
            put("^/api/projects/[^/]+/agents/[^/]+$"),
            // 0.18.5 a new workflow and a new rule pack (keel_workflow_create, keel_rule_create)
            post("^/api/projects/[^/]+/workflows(/import)?$"),
            post("^/api/projects/[^/]+/rules/packs$"),
            // 0.18.6 skills: a new one, an import, the agents it is on and a marketplace install
            post("^/api/projects/[^/]+/skills(/import|/market/install)?$"),
            put("^/api/projects/[^/]+/skills/(?!repo$)[^/]+$"),
            // 0.18.9 stacks: saving, removing, turning off and a marketplace install
            post("^/api/projects/[^/]+/stacks(/save|/market/install)?$"),
            put("^/api/projects/[^/]+/stacks/[^/]+/off$"),
            delete("^/api/projects/[^/]+/stacks/[^/]+$"),
        )

        /**
         * Routes that need admin, named so McpRouteLevelsApiTest knows each was looked at (what no list names needs admin
         * too). keel's own configuration and what can reach the machine or the account: an agent with MCP write must not
         * install a plugin, store a secret, sign in somewhere, add an MCP server (it runs any command), change a setting
         * or approve a plugin install.
         */
        val ADMIN = listOf(
            // 0.18.65 Settings › Review and the project's lens files (no MCP tool changes them)
            put("^/api/projects/[^/]+/review-setup$"),
            post("^/api/projects/[^/]+/review-setup/lenses$"),
            put("^/api/projects/[^/]+/review-setup/lenses/[^/]+$"),
            // a flow's run removed (keel_delete_run), its worktree removed, a project unlocked
            delete("^/api/projects/[^/]+/flows/[^/]+$"),
            post("^/api/threads/[^/]+/worktree/remove$"),
            post("^/api/projects/[^/]+/unlock$"),
            post("^/api/projects$"),
            post("^/api/projects/bulk$"),
            delete("^/api/projects/[^/]+$"),
            put("^/api/projects/[^/]+/settings$"),
            put("^/api/projects/[^/]+/plugins/[^/]+$"),
            put("^/api/workflows/[^/]+$"),
            delete("^/api/workflows/[^/]+$"),
            put("^/api/projects/[^/]+/workflows/[^/]+/folder$"),
            post("^/api/projects/[^/]+/library/[^/]+/install$"),
            post("^/api/projects/[^/]+/workflows/market/(install|[^/]+/update)$"),
            // v0.17.2 keel's rules: a change decides what agents may do
            put("^/api/projects/[^/]+/rules/config$"),
            post("^/api/projects/[^/]+/rules/library/[^/]+/install$"),
            // 0.18.5 a new rule pack is write (it only refuses; keel_rule_create asks the person first); changing or
            // removing one stays admin
            put("^/api/projects/[^/]+/rules/packs/[^/]+$"),
            delete("^/api/projects/[^/]+/rules/packs/[^/]+$"),
            post("^/api/projects/[^/]+/rule-packs/(install|uninstall|market/install)$"),
            // 0.18.6 removing a skill, and the repo's own skills on or off
            delete("^/api/projects/[^/]+/skills/[^/]+$"),
            put("^/api/projects/[^/]+/skills/repo$"),
            // a stack's setup commands run on the machine
            post("^/api/projects/[^/]+/stacks/(?!market/)[^/]+/install$"),
            // agents: removing one, and a test (it runs a model); a step explained by a model
            delete("^/api/projects/[^/]+/agents/[^/]+$"),
            post("^/api/agents/[^/]+/test$"),
            post("^/api/projects/[^/]+/workflows/explain-step$"),
            // what keel remembers about a project
            post("^/api/projects/[^/]+/memory$"),
            put("^/api/projects/[^/]+/memory/[^/]+$"),
            delete("^/api/projects/[^/]+/memory/[^/]+$"),
            // budget caps and limits, the workspace doctor's apply, a hunt's close, jobs and quality runs
            post("^/api/projects/[^/]+/caps$"),
            put("^/api/projects/[^/]+/caps/[^/]+$"),
            delete("^/api/projects/[^/]+/caps/[^/]+$"),
            put("^/api/limits$"),
            post("^/api/projects/[^/]+/doctor/workspace/apply$"),
            post("^/api/projects/[^/]+/hunts/[^/]+/close$"),
            post("^/api/jobs/[^/]+/stop$"),
            post("^/api/quality/runs(/[^/]+/stop)?$"),
            put("^/api/quality/schedule$"),
            post("^/api/usage/providers/[^/]+/refresh$"),
            // which project the dashboard and keel TUI look at (keel_focus is an admin tool)
            put("^/api/ui/focus$"),
            // plugins, the marketplace and the plugin host (0.18.64: its restart, like /api/plugins/restart)
            post("^/api/plugins/(install|install-file|restart)$"),
            post("^/api/plugins/[^/]+/(update|rollback)$"),
            put("^/api/plugins/(sources|rules)$"),
            put("^/api/plugins/[^/]+$"),
            delete("^/api/plugins/[^/]+$"),
            post("^/api/plugin-host/restart$"),
            post("^/api/marketplace/refresh$"),
            // 0.18.64 MCP servers: each runs any command with the secrets it names; which agent may use which tool
            post("^/api/mcp-servers$"),
            put("^/api/mcp-servers/[^/]+$"),
            delete("^/api/mcp-servers/[^/]+$"),
            post("^/api/mcp-servers/[^/]+/test$"),
            put("^/api/projects/[^/]+/mcp-allow$"),
            // secrets, sign-ins, settings, connections and model servers (0.18.64: a changed host must not get a saved token)
            put("^/api/secrets/[^/]+$"),
            delete("^/api/secrets/[^/]+$"),
            put("^/api/settings/general$"),
            put("^/api/connections/[^/]+$"),
            post("^/api/connections/[^/]+/test$"),
            post("^/api/logins$"),
            post("^/api/logins/[^/]+/code$"),
            delete("^/api/logins/[^/]+$"),
            put("^/api/providers/(local|vertex)$"),
            put("^/api/providers/local/endpoints/[^/]+$"),
            delete("^/api/providers/local/endpoints/[^/]+$"),
            post("^/api/providers/local/endpoints/[^/]+/test$"),
            post("^/api/approvals/[^/]+/decide$"),
            // devices (pairing a phone), the web sign-in, notifications
            post("^/api/devices/(pair|pairing)$"),
            put("^/api/devices/addresses$"),
            delete("^/api/devices/[^/]+$"),
            post("^/api/web/(code|sign-out|sign-out-everywhere)$"),
            put("^/api/notification-settings$"),
            // 0.18.66 the update check (its setting, and Check now: a call to GitHub) and the first-run welcome
            put("^/api/updates/settings$"),
            post("^/api/updates/check$"),
            put("^/api/help/welcome$"),
            post("^/api/notifications/(read-all|[^/]+/read)$"),
            delete("^/api/notifications(/[^/]+)?$"),
        )

        /** Every method under these prefixes needs admin (all of /api/github: sign-in, sign-out, tokens; the Database
         *  plugin's /api/databases: a connection every project sees, its test and its server's databases). */
        val ADMIN_PREFIXES = listOf("/api/github", "/api/databases")
    }
}
