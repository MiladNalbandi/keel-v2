package keel.api.jira

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.projects.ProjectService
import keel.api.tasks.TaskMachine
import keel.api.tasks.TaskStatus
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.security.MessageDigest

/** A project's Jira connection, without the token (that is a secret). */
data class JiraSettings(
    /** cloud (https://<site>.atlassian.net, email + API token) | server (Server / Data Center, personal access token) */
    val kind: String = "cloud",
    val baseUrl: String = "",
    val email: String? = null,
    val projectKey: String? = null,
    val boardId: String? = null,
    /** Blank = [JiraService.DEFAULT_JQL] (and "project = KEY AND …" when a project key is set and no board). */
    val jql: String? = null,
    /** keel status → Jira status or transition name; "-" = do not move the ticket for that status. */
    val statusMap: Map<String, String> = emptyMap(),
    /** The Jira field that holds reviewers (customfield_…). */
    val reviewerField: String? = null,
    /** People for that field: Atlassian account ids or emails (Cloud), user names (Server). */
    val jiraReviewers: List<String> = emptyList(),
    /** GitHub logins asked to review the PR. */
    val githubReviewers: List<String> = emptyList(),
    /** Minutes between syncs; 0 = only "Sync now". */
    val pollMinutes: Int = 5,
)

/** PUT body: null keeps the saved value, "" clears it; [token] blank keeps the saved token. */
data class JiraSave(
    val kind: String? = null,
    val baseUrl: String? = null,
    val email: String? = null,
    val token: String? = null,
    val projectKey: String? = null,
    val boardId: String? = null,
    val jql: String? = null,
    val statusMap: Map<String, String>? = null,
    val reviewerField: String? = null,
    val jiraReviewers: List<String>? = null,
    val githubReviewers: List<String>? = null,
    val pollMinutes: Int? = null,
)

data class JiraView(
    val connected: Boolean,
    val settings: JiraSettings,
    val tokenSet: Boolean,
    val tokenHint: String?,
    val defaultJql: String,
    /** The JQL a sync uses now. */
    val jql: String,
    val lastSyncAt: String?,
    val lastSyncError: String?,
    val me: JiraUser?,
    /** The optional mcp-atlassian server made from this connection (Tools › Catalog), when added. */
    val mcpServer: String?,
)

data class JiraTestResult(val ok: Boolean, val user: JiraUser? = null, val error: String? = null, val hint: String? = null, val kind: String? = null)

data class JiraDiscovery(
    val statuses: List<JiraStatus>,
    val transitions: List<JiraTransition>,
    /** Fields that can hold reviewers: user pickers, and custom fields with "review" in their name. */
    val fields: List<JiraField>,
    val suggested: Map<String, String>,
    val keelStatuses: List<String> = TaskStatus.ALL,
)

/** Jira's answer as an api error: 502 with Jira's words (never the token). */
class JiraFailed(e: JiraException) : ApiException(HttpStatus.BAD_GATEWAY, e.message, e.hint)

@Service
class JiraService(
    private val jdbc: JdbcTemplate,
    private val secrets: SecretService,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
) {
    /** How long a Jira call may take (a sync makes a few). */
    @Volatile var timeoutSeconds: Long = 20

    fun settings(pid: String): JiraSettings? =
        jdbc.query("SELECT json FROM jira_connections WHERE project_id = ?", { rs, _ -> rs.getString(1) }, pid).firstOrNull()
            ?.let { runCatching { Json.read<JiraSettings>(it) }.getOrNull() }

    fun connectedProjects(): List<String> = jdbc.queryForList("SELECT project_id FROM jira_connections", String::class.java)

    fun view(pid: String): JiraView {
        projects.require(pid)
        val s = settings(pid)
        val row = jdbc.query("SELECT me_json, last_sync_at, last_sync_error FROM jira_connections WHERE project_id = ?",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid).firstOrNull()
        val name = secretName(pid)
        return JiraView(
            connected = s != null && secrets.has(name), settings = s ?: JiraSettings(), tokenSet = secrets.has(name),
            tokenHint = secrets.hintOf(name), defaultJql = DEFAULT_JQL, jql = s?.let { jql(it) } ?: DEFAULT_JQL,
            lastSyncAt = row?.second, lastSyncError = row?.third,
            me = row?.first?.let { runCatching { Json.read<JiraUser>(it) }.getOrNull() },
            mcpServer = mcpName(pid).takeIf { n -> (jdbc.queryForObject("SELECT COUNT(*) FROM mcp_servers WHERE name = ?", Int::class.java, n) ?: 0) > 0 },
        )
    }

    fun save(pid: String, body: JiraSave): JiraView {
        projects.require(pid)
        val cur = settings(pid) ?: JiraSettings()
        val next = check(merge(cur, body))
        val token = body.token?.filterNot { it.isWhitespace() }?.takeIf { it.isNotEmpty() }
        if (token == null && !secrets.has(secretName(pid))) {
            throw BadRequest("The token is missing", if (next.kind == "cloud") "Paste an API token from id.atlassian.com › Security › API tokens."
            else "Paste a personal access token (Jira › Profile › Personal Access Tokens).")
        }
        if (token != null) secrets.put(secretName(pid), token)
        val now = Time.now()
        jdbc.update(
            "INSERT INTO jira_connections(project_id, json, updated_at) VALUES (?, ?, ?) " +
                "ON CONFLICT(project_id) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at",
            pid, Json.write(next), now,
        )
        refreshMcp(pid)
        return view(pid)
    }

    fun delete(pid: String) {
        projects.require(pid)
        if (jdbc.update("DELETE FROM jira_connections WHERE project_id = ?", pid) == 0) throw NotFound("This project has no Jira connection")
        if (secrets.has(secretName(pid))) secrets.delete(secretName(pid))
        jdbc.update("DELETE FROM mcp_servers WHERE name = ?", mcpName(pid))
    }

    /** A client for the saved connection; null when there is none (or no token). */
    fun client(pid: String): JiraClient? {
        val s = settings(pid) ?: return null
        val token = secrets.get(secretName(pid)) ?: return null
        return JiraClient(s.kind, s.baseUrl, s.email, token, mapper, java.time.Duration.ofSeconds(timeoutSeconds))
    }

    fun requireClient(pid: String): JiraClient =
        client(pid) ?: throw Conflict("This project has no Jira connection", "Connect Jira in Control › Connections › Jira.")

    /** Tests the saved connection, or the unsaved one in [body] (its token, else the saved one). Never throws for Jira's answer. */
    fun test(pid: String, body: JiraSave?): JiraTestResult {
        projects.require(pid)
        val client = if (body != null && (body.baseUrl != null || body.token != null)) {
            val s = check(merge(settings(pid) ?: JiraSettings(), body))
            val token = body.token?.filterNot { it.isWhitespace() }?.takeIf { it.isNotEmpty() } ?: secrets.get(secretName(pid))
                ?: return JiraTestResult(false, error = "The token is missing", hint = "Paste the token, then test.")
            JiraClient(s.kind, s.baseUrl, s.email, token, mapper, java.time.Duration.ofSeconds(timeoutSeconds))
        } else client(pid) ?: return JiraTestResult(false, error = "This project has no Jira connection", hint = "Fill in the form and save it first.")
        return try {
            val me = client.myself()
            if (settings(pid) != null && (body == null || body.baseUrl == null || body.baseUrl.trim().trimEnd('/') == settings(pid)?.baseUrl)) {
                jdbc.update("UPDATE jira_connections SET me_json = ? WHERE project_id = ?", Json.write(me), pid)
            }
            JiraTestResult(true, me)
        } catch (e: JiraException) {
            JiraTestResult(false, error = e.message, hint = e.hint, kind = e.kind)
        }
    }

    /** Statuses (of the project, or all), the transitions of one ticket, the fields that can hold reviewers, a first mapping. */
    fun discover(pid: String, key: String?): JiraDiscovery {
        val s = settings(pid) ?: throw Conflict("This project has no Jira connection", "Save the connection first.")
        val c = requireClient(pid)
        try {
            val statuses = s.projectKey?.let { c.projectStatuses(it) } ?: c.allStatuses()
            val transitions = key?.takeIf { it.isNotBlank() }?.let { c.transitions(it.trim()) }.orEmpty()
            val fields = c.fields().filter { f ->
                f.type == "user" || (f.type == "array" && f.items == "user") || (f.custom && f.name.contains("review", ignoreCase = true))
            }
            return JiraDiscovery(statuses, transitions, fields, TaskMachine.suggest(statuses.map { it.name to it.category }))
        } catch (e: JiraException) {
            throw JiraFailed(e)
        }
    }

    fun markSynced(pid: String, error: String?) {
        if (error == null) jdbc.update("UPDATE jira_connections SET last_sync_at = ?, last_sync_error = NULL WHERE project_id = ?", Time.now(), pid)
        else jdbc.update("UPDATE jira_connections SET last_sync_error = ? WHERE project_id = ?", error.take(500), pid)
    }

    fun lastSync(pid: String): Pair<String?, String?>? =
        jdbc.query("SELECT last_sync_at, last_sync_error FROM jira_connections WHERE project_id = ?", { rs, _ -> rs.getString(1) to rs.getString(2) }, pid)
            .firstOrNull()

    fun me(pid: String): JiraUser? =
        jdbc.query("SELECT me_json FROM jira_connections WHERE project_id = ?", { rs, _ -> rs.getString(1) }, pid).firstOrNull()
            ?.let { runCatching { Json.read<JiraUser>(it) }.getOrNull() }

    fun setMe(pid: String, me: JiraUser) {
        jdbc.update("UPDATE jira_connections SET me_json = ? WHERE project_id = ?", Json.write(me), pid)
    }

    /** The JQL a sync uses. */
    fun jql(s: JiraSettings): String = s.jql?.trim()?.takeIf { it.isNotEmpty() }
        ?: if (s.projectKey != null && s.boardId == null) "project = ${s.projectKey} AND $DEFAULT_JQL" else DEFAULT_JQL

    // ---- the optional mcp-atlassian server (Tools › Catalog) ----

    /**
     * Adds (or refreshes) the `jira-<project>` MCP server from this connection: mcp-atlassian (github.com/sooperset/mcp-atlassian,
     * MIT) run with `uvx mcp-atlassian`, read-only, limited to the project key. Added turned off; the token is referenced as
     * `secret:<name>` and only resolved when a flow or a test starts it.
     */
    fun addMcp(pid: String): String {
        val s = settings(pid) ?: throw Conflict("Connect Jira for this project first", "Control › Connections › Jira.")
        val name = mcpName(pid)
        jdbc.update(
            "INSERT INTO mcp_servers(name, command, args_json, env_json, enabled, builtin, status) VALUES (?, 'uvx', ?, ?, 0, 0, 'off') " +
                "ON CONFLICT(name) DO UPDATE SET env_json = excluded.env_json",
            name, Json.write(MCP_ARGS), Json.write(mcpEnv(pid, s)),
        )
        return name
    }

    private fun refreshMcp(pid: String) {
        val s = settings(pid) ?: return
        jdbc.update("UPDATE mcp_servers SET env_json = ? WHERE name = ?", Json.write(mcpEnv(pid, s)), mcpName(pid))
    }

    private fun mcpEnv(pid: String, s: JiraSettings): Map<String, String> {
        val env = linkedMapOf("JIRA_URL" to s.baseUrl)
        if (s.kind == "cloud") {
            env["JIRA_USERNAME"] = s.email.orEmpty()
            env["JIRA_API_TOKEN"] = "secret:${secretName(pid)}"
        } else {
            env["JIRA_PERSONAL_TOKEN"] = "secret:${secretName(pid)}"
        }
        env["READ_ONLY_MODE"] = "true"
        s.projectKey?.let { env["JIRA_PROJECTS_FILTER"] = it }
        return env
    }

    // ---- validation ----

    private fun merge(cur: JiraSettings, b: JiraSave): JiraSettings {
        fun str(v: String?, old: String?) = if (v == null) old else v.trim().ifEmpty { null }
        return JiraSettings(
            kind = b.kind?.trim()?.lowercase() ?: cur.kind,
            baseUrl = b.baseUrl?.trim()?.trimEnd('/') ?: cur.baseUrl,
            email = str(b.email, cur.email),
            projectKey = str(b.projectKey, cur.projectKey)?.uppercase(),
            boardId = str(b.boardId, cur.boardId),
            jql = str(b.jql, cur.jql),
            statusMap = (b.statusMap ?: cur.statusMap).mapValues { it.value.trim() }.filterValues { it.isNotEmpty() },
            reviewerField = str(b.reviewerField, cur.reviewerField),
            jiraReviewers = (b.jiraReviewers ?: cur.jiraReviewers).map { it.trim() }.filter { it.isNotEmpty() }.distinct(),
            githubReviewers = (b.githubReviewers ?: cur.githubReviewers).map { it.trim().removePrefix("@") }.filter { it.isNotEmpty() }.distinct(),
            pollMinutes = b.pollMinutes ?: cur.pollMinutes,
        )
    }

    private fun check(s: JiraSettings): JiraSettings {
        if (s.kind !in setOf("cloud", "server")) throw BadRequest("kind must be cloud or server", "cloud = *.atlassian.net; server = Jira Server or Data Center.")
        if (!Regex("^https?://[^\\s/?#]+(/[^\\s?#]*)?$").matches(s.baseUrl)) {
            throw BadRequest("The Jira URL is not valid", if (s.kind == "cloud") "For example https://your-site.atlassian.net" else "For example https://jira.example.com")
        }
        if (s.kind == "cloud" && (s.email == null || "@" !in s.email)) throw BadRequest("Jira Cloud needs the email of the account", "The token belongs to that email.")
        if (s.projectKey != null && !Regex("^[A-Z][A-Z0-9_]{0,29}$").matches(s.projectKey)) throw BadRequest("The project key is not valid", "Like ABC (the letters before the number in ABC-12).")
        if (s.boardId != null && !Regex("^\\d{1,12}$").matches(s.boardId)) throw BadRequest("The board id is a number", "Open the board in Jira: …/boards/<id>.")
        if ((s.jql?.length ?: 0) > 2000) throw BadRequest("The JQL is too long (2000 characters at most)")
        val unknown = s.statusMap.keys - TaskStatus.ALL.toSet()
        if (unknown.isNotEmpty()) throw BadRequest("Unknown keel status: ${unknown.joinToString()}", "Use: ${TaskStatus.ALL.joinToString()}.")
        if (s.statusMap.values.any { it.length > 100 }) throw BadRequest("A Jira status name is too long")
        if (s.reviewerField != null && !Regex("^[A-Za-z0-9_.-]{1,64}$").matches(s.reviewerField)) throw BadRequest("The reviewer field is a field id", "Like customfield_10010.")
        val badLogin = s.githubReviewers.firstOrNull { !Regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(/[A-Za-z0-9._-]+)?$").matches(it) }
        if (badLogin != null) throw BadRequest("\"$badLogin\" is not a GitHub login", "Logins only, like octocat (or org/team).")
        if (s.jiraReviewers.any { it.length > 128 }) throw BadRequest("A Jira reviewer is too long")
        if (s.pollMinutes !in 0..1440) throw BadRequest("poll_minutes must be 0 to 1440", "0 = only when you press Sync now.")
        return s
    }

    companion object {
        const val DEFAULT_JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC"
        val MCP_ARGS = listOf("mcp-atlassian")

        /** The secret that holds a project's Jira token (SecretService names: up to 64 of [A-Za-z0-9_.-]). */
        fun secretName(pid: String): String =
            if (pid.length <= 59) "jira.$pid" else "jira.${pid.take(42)}.${sha(pid).take(16)}"

        fun mcpName(pid: String): String {
            val p = pid.lowercase().replace(Regex("[^a-z0-9_-]"), "-")
            return if (p.length <= 35) "jira-$p" else "jira-${p.take(26)}-${sha(pid).take(8)}"
        }

        private fun sha(s: String) = MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }
    }
}
