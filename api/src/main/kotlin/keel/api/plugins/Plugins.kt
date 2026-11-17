package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.approvals.ApprovalService
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.flow.FlowContributor
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

/** v0.10.0: Settings › Plugins. enabled for this project, and whether that comes from its own choice or every project's. */
data class PluginSwitch(val enabled: Boolean = true, val scope: String = "project")
data class PluginAskBody(val title: String = "", val command: String = "")

/**
 * keel's installable plugins (engine `GET /plugins`, content/plugins/<name>/plugin.yml): Database and Git. A project turns
 * one on (or every project does, `*`); a project's own row wins. Flows and KeelBot get the list and the plugins' secrets
 * with every call: `github` → the GitHub token (keysFor), and what each plugin's FlowContributor adds (the Database
 * plugin: `db:<name>` → the connection as JSON).
 */
/**
 * A plugin was turned on or off for a project ([projectId]) or for every project ("*"). Part of keel's api SDK: a plugin
 * whose engine part must know while flows run listens (the Supervisor stops at once).
 */
data class PluginSwitched(val projectId: String, val plugin: String, val enabled: Boolean)

@Service
class PluginService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val secrets: SecretService,
    private val mapper: ObjectMapper,
    private val approvals: ApprovalService,
    private val publisher: org.springframework.beans.factory.ObjectProvider<org.springframework.context.ApplicationEventPublisher>,
) : FlowContributor {
    @Volatile private var cache: Pair<Long, JsonNode>? = null

    /** A web or content-only plugin came or went without a restart: its KeelBot commands may be in the catalog now. */
    @org.springframework.context.event.EventListener
    fun reloaded(@Suppress("UNUSED_PARAMETER") e: keel.api.pluginhost.PluginsReloaded) {
        cache = null
    }

    /** The catalog from the engine (kept a minute; an engine that is down gives an empty list). */
    fun catalog(): JsonNode {
        cache?.let { (at, v) -> if (System.currentTimeMillis() - at < 60_000) return v }
        val v = try { engine.get("/plugins") } catch (e: EngineDown) { return mapper.createArrayNode() }
        cache = System.currentTimeMillis() to v
        return v
    }

    private fun names(): Set<String> = catalog().mapNotNull { it.path("name").asText(null) }.toSet()

    private fun rows(pid: String): Map<Pair<String, String>, Boolean> = jdbc.query(
        "SELECT project_id, plugin, enabled FROM project_plugins WHERE project_id IN (?, '*')",
        { rs, _ -> (rs.getString(1) to rs.getString(2)) to (rs.getInt(3) == 1) }, pid,
    ).toMap()

    /** The plugins on for this project. */
    fun enabled(pid: String): List<String> {
        val r = rows(pid)
        return names().filter { r[pid to it] ?: r["*" to it] ?: false }.sorted()
    }

    fun on(pid: String, name: String): Boolean = name in enabled(pid)

    fun require(pid: String, name: String) {
        if (!on(pid, name)) {
            val title = mapOf("db" to "Database", "git" to "Git", "ci" to "CI/CD", "review" to "Code Review")[name] ?: name
            throw Conflict("The $title plugin is off for this project", "Turn it on in Settings › Plugins.")
        }
    }

    /** The catalog for one project: each plugin with enabled and scope (project | all | null when off everywhere). */
    fun forProject(pid: String): List<Map<String, Any?>> {
        projects.require(pid)
        val r = rows(pid)
        return catalog().map { p ->
            val name = p.path("name").asText()
            val own = r[pid to name]
            val all = r["*" to name]
            @Suppress("UNCHECKED_CAST")
            (mapper.convertValue(p, Map::class.java) as Map<String, Any?>) + mapOf(
                "enabled" to (own ?: all ?: false),
                "scope" to when { own != null -> "project"; all != null -> "all"; else -> null },
            )
        }
    }

    fun set(pid: String, name: String, body: PluginSwitch): List<Map<String, Any?>> {
        projects.require(pid)
        if (name !in names()) throw NotFound("No plugin called $name", "Known: ${names().sorted().joinToString()}")
        if (body.scope !in setOf("project", "all")) throw BadRequest("scope is project or all")
        val target = if (body.scope == "all") "*" else pid
        jdbc.update(
            "INSERT INTO project_plugins(project_id, plugin, enabled, updated_at) VALUES (?, ?, ?, ?) " +
                "ON CONFLICT(project_id, plugin) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
            target, name, if (body.enabled) 1 else 0, Time.now(),
        )
        // "for every project" also means this one: its own older choice gives way
        if (body.scope == "all") jdbc.update("DELETE FROM project_plugins WHERE project_id = ? AND plugin = ?", pid, name)
        publisher.ifAvailable { it.publishEvent(PluginSwitched(target, name, body.enabled)) }
        return forProject(pid)
    }

    /**
     * The secrets a flow or KeelBot turn of this project carries for its plugins (the engine keeps them in memory). A
     * plugin brings its own as a FlowContributor bean (the Database plugin: DatabaseKeys, `db:<name>`).
     */
    fun keysFor(pid: String): Map<String, String> {
        val out = linkedMapOf<String, String>()
        out.putAll(githubKeys())
        return out
    }

    // ---- FlowContributor: what flows get from the plugins -----------------------------------

    override fun keys(pid: String): Map<String, String> = keysFor(pid)

    /** `plugins`: the ones on for the project (null when none, as the engine always got it). */
    override fun settings(pid: String): Map<String, Any?> = mapOf("plugins" to enabled(pid).takeIf { it.isNotEmpty() })

    /** A template of a plugin in the catalog shows while that plugin is on; any other plugin is not ours. */
    override fun templateOn(pid: String, plugin: String): Boolean? = if (plugin in names()) on(pid, plugin) else null

    fun ask(pid: String, body: PluginAskBody): JsonNode {
        projects.require(pid)
        if (body.command.isBlank()) throw BadRequest("Say what would run")
        return engine.post("/plugins/ask", mapOf("project" to pid, "title" to body.title.ifBlank { "Claude Code (keel MCP)" }.take(200),
            "command" to body.command.take(4000)))
    }

    /** keel2 mcp polls its question (core approvals): {waiting: true} until the person answers, then the answer. */
    fun asked(qid: String): JsonNode {
        if (!Regex("^p_[0-9a-f]{12}$").matches(qid)) throw BadRequest("That is not a question id")
        return approvals.asked(qid)
    }

    fun github(): Map<String, Any?> {
        val hint = secrets.hintOf(GITHUB)
        val env = System.getenv(GITHUB)?.takeIf { it.isNotBlank() }
        // v0.18.30 who a "Sign in with GitHub" connected (login, scopes, host; never the token)
        val who = if (hint != null) secrets.get("GITHUB_REPO_INFO")?.let { runCatching { mapper.readTree(it) }.getOrNull() } else null
        return mapOf(
            "set" to (hint != null || env != null), "hint" to hint, "from" to when { hint != null -> "keel"; env != null -> "env"; else -> null },
            "login" to who?.get("login")?.takeIf { !it.isNull }?.asText(), "scopes" to (who?.get("scopes")?.map { it.asText() } ?: emptyList<String>()),
            "host" to (who?.get("host")?.asText() ?: "github.com"), "via" to who?.get("via")?.takeIf { !it.isNull }?.asText(),
        )
    }

    /** The GitHub token of Connections › GitHub (its own; the Copilot login is not used for git). */
    fun githubToken(): String? = secrets.get(GITHUB) ?: System.getenv(GITHUB)?.takeIf { it.isNotBlank() }

    /** The host [githubToken] belongs to: github.com, or the Enterprise host set at sign-in. The token goes to it only. */
    fun githubHost(): String = secrets.githubHost()

    /**
     * The GitHub login as the engine reads it: keys["github"] (the token) and keys["github_host"] (its host). The
     * engine's git and gh calls give the token to that host only (engine tools/git_auth.py). Empty without a token.
     */
    fun githubKeys(): Map<String, String> =
        githubToken()?.let { mapOf("github" to it, "github_host" to githubHost()) } ?: emptyMap()

    companion object {
        const val GITHUB = "GITHUB_REPO_TOKEN"
    }
}
