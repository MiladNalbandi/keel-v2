package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Forbidden
import keel.api.common.KeelProperties
import keel.api.common.KvStore
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.InvalidPathException
import java.nio.file.Path
import java.nio.file.Paths
import java.util.UUID

data class DbConnectionBody(val name: String = "", val url: String? = null, val env: String? = null, val source: String? = null)
/** mask: a model reads the rows, so columns that hold a secret come back as •••; an agent's call (keel2 mcp) is always masked. */
data class DbQueryBody(val connection: String = "", val sql: String = "", val change: Boolean = false, val confirm: Boolean = false,
                       val mask: Boolean = false)

/**
 * #96 the most keel's MCP server may do with databases: what Claude Code, OpenCode, Codex or Cursor get through keel2 mcp
 * (and an agent through keel's own MCP server). `connection`: each connection's Access; `private`: at most "Results only
 * to you" (the rows go to the person, the client gets a link); `none`: no database at all. keel's own agents (KeelBot,
 * flow agents through the keel-db server) follow each connection's Access.
 */
data class DbMcpAccess(val access: String = "connection")

/** One connection as the web sees it: never the password. */
data class DbConnection(
    val name: String, val kind: String, val env: String, val shown: String, val source: String?,
    val ok: Boolean?, val server: String?, val tables: Int?, val error: String?, val checkedAt: String?,
    /** a change of data may run here (local and test databases; staging and prod are read only, always) */
    val canChange: Boolean,
    val id: String? = null,
    /** project: the project's own; global: shared with every project (or the ones it names) */
    val scope: String = "project",
    val driver: String? = null,
)

/**
 * A connection as the editor sends it (Control › Databases, a project's Databases). Every field is optional on an
 * update. `url` is a whole address (an import from "Find in project", the old routes): it fills the fields. `password`
 * is only ever sent, never returned.
 */
data class DbConfigBody(
    val name: String? = null,
    val env: String? = null,
    /** global | project: where it lives (an update may move it) */
    val scope: String? = null,
    /** the project of a project connection (scope project) */
    val project: String? = null,
    /** a global connection only for these projects (empty: every project) */
    val projects: List<String>? = null,
    val driver: String? = null,
    val connectMode: String? = null,
    val host: String? = null,
    val port: Int? = null,
    val username: String? = null,
    val password: String? = null,
    /** forget the kept password (sign in without one) */
    val clearPassword: Boolean = false,
    val defaultDb: String? = null,
    val urlOverride: String? = null,
    val socketPath: String? = null,
    val envVar: String? = null,
    val note: String? = null,
    val agentAccess: String? = null,
    val askFirst: Boolean? = null,
    val keepPassword: String? = null,
    val url: String? = null,
    val source: String? = null,
    /** a test or a server's databases for a draft of this saved connection: its kept password is used */
    val id: String? = null,
)

/** One connection in the editor's list: everything but the password. */
data class DbEntry(
    val id: String,
    val name: String,
    /** project | global */
    val source: String,
    /** a global one that this project's own connection of the same name replaces here */
    val overridden: Boolean,
    /** a global one this project turned off for itself */
    val hidden: Boolean,
    val project: String?,
    val driver: String,
    val kind: String,
    val env: String,
    val connectMode: String,
    val host: String?,
    val port: Int?,
    val username: String?,
    val defaultDb: String?,
    val urlOverride: String?,
    val socketPath: String?,
    val envVar: String?,
    val note: String?,
    val agentAccess: String,
    /** what agents really may do: staging and prod are read only for them, always */
    val agentAccessEffective: String,
    val askFirst: Boolean,
    val keepPassword: String,
    val hasPassword: Boolean,
    /** the address without the password */
    val url: String?,
    val shown: String,
    /** where keel found it (docker-compose.yml, .env.example …) */
    val foundIn: String?,
    /** a global one's projects (empty: every project) */
    val projects: List<String>,
    val ok: Boolean?,
    val server: String?,
    val tables: Int?,
    val error: String?,
    val checkedAt: String?,
    val canChange: Boolean,
)

/** A test of a connection: never the password, also not in the error. */
data class DbTestResult(val ok: Boolean, val server: String? = null, val tables: Int? = null, val ms: Long? = null,
                        val error: String? = null, val hint: String? = null)

/**
 * The Database plugin's connections and the person's own queries (Control › Databases, a project's Databases, Map ›
 * Query, KeelBot's buttons). A connection is global (every project, or the ones it names) or a project's own; a project
 * sees its own and the globals for it, and its own wins on the same name ([DatabaseStore]). The address with its
 * password is a secret (`db.<id>`); the engine (the plugin's keel_plugin_db) connects, tests, reads the schema and runs
 * queries under its rules. Since step 3 this is the plugin's api part (plugins/db, keel-plugin-db.jar) in keel's
 * package; its table db_connections stays in core (V11, V22).
 */
@Service
class DatabaseService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val plugins: PluginService,
    private val store: DatabaseStore,
    private val props: KeelProperties,
    private val kv: KvStore,
) {
    // ---------------------------------------------------------------- #96 keel's MCP server: at most what Settings says

    fun mcpAccess(): DbMcpAccess = kv.get<DbMcpAccess>(MCP_KEY)?.takeIf { it.access in MCP_ACCESS } ?: DbMcpAccess()

    /** Only the person changes it: an MCP call never may (the controller refuses one). */
    fun setMcpAccess(body: DbMcpAccess): DbMcpAccess {
        if (body.access !in MCP_ACCESS) throw BadRequest("Pick connection, private or none")
        kv.put(MCP_KEY, body)
        return body
    }

    /** What an MCP call may do with this connection: its Access, kept to what Settings › Databases allows MCP clients. */
    private fun forMcp(r: DbRow): String = capped(r.agentAccessEffective)

    private fun capped(own: String): String {
        return when (mcpAccess().access) {
            "none" -> "none"
            "private" -> if (ACCESS_ORDER.indexOf(own) > ACCESS_ORDER.indexOf("private")) "private" else own
            else -> own
        }
    }

    private fun conn(r: DbRow) = DbConnection(r.name, r.kind, r.env, r.shown, r.source, r.ok, r.server, r.tables, r.error, r.checkedAt,
        r.env in CHANGE_ENVS, r.id, if (r.global) "global" else "project", r.driver)

    private fun entry(r: DbRow, overridden: Boolean = false, hidden: Boolean = false) = DbEntry(
        r.id, r.name, if (r.global) "global" else "project", overridden, hidden, r.projectId.takeUnless { r.global }, r.driver, r.kind,
        r.env, r.connectMode, r.host, r.port, r.username, r.defaultDb, r.urlOverride, r.socketPath, r.envVar, r.note, r.agentAccess,
        r.agentAccessEffective, r.askFirst, r.keepPassword, store.hasPassword(r), store.address(r), r.shown, r.source,
        if (r.global) store.allowed(r.id) else emptyList(), r.ok, r.server, r.tables, r.error, r.checkedAt, r.env in CHANGE_ENVS,
    )

    private fun active(pid: String, name: String): DbRow = store.active(pid).firstOrNull { it.name == name }
        ?: throw NotFound("No database connection called $name", "Add it in Databases.")

    /** A project's own connection by name; a global one is changed in Control › Databases. */
    private fun own(pid: String, name: String): DbRow = store.own(pid).firstOrNull { it.name == name }
        ?: if (store.active(pid).any { it.name == name }) {
            throw Conflict("$name is shared with every project", "Change it in Control › Databases.")
        } else {
            throw NotFound("No database connection called $name", "Add it in Databases.")
        }

    private fun byId(id: String): DbRow = store.byId(id) ?: throw NotFound("No database connection with the id $id")

    /** What the engine gets to connect with: the full address, memory only. An agent's call (keel2 mcp) respects Access. */
    private fun spec(pid: String, name: String, agent: Boolean = false, change: Boolean = false): Map<String, Any?> {
        val r = active(pid, name)
        if (agent) agentMay(r, change)
        return specOf(r, pid)
    }

    private fun specOf(r: DbRow, pid: String): Map<String, Any?> {
        val name = r.name
        val c = store.connect(r, pid)
        val url = c.url ?: throw Conflict(c.unavailable ?: "keel cannot connect to $name", c.hint)
        return mapOf("name" to r.name, "kind" to (DbUrls.driverOf(url)?.let(DbUrls::kindOf) ?: r.kind), "url" to url, "env" to r.env)
    }

    private fun agentMay(r: DbRow, change: Boolean): String {
        val access = forMcp(r)
        if (access == "none") {
            if (mcpAccess().access == "none") throw Forbidden("Databases are off for MCP clients",
                "Settings › Databases › MCP clients says Claude Code, OpenCode and other MCP clients get no database.")
            throw Forbidden("${r.name} is not offered to agents", "Its Access in Databases says agents may do nothing with it.")
        }
        if (change && access == "private") {
            throw Forbidden("On ${r.name} agents only run reads, and the results go to the person",
                "Give the change to the person instead, or change its Access in Databases.")
        }
        if (change && access != "write_approve") {
            throw Forbidden("Agents may only read ${r.name}", if (r.env in CHANGE_ENVS) "Allow \"change after approval\" in its Access tab in Databases."
                else "${r.name} is a ${r.env} database: agents only read there, always.")
        }
        return access
    }

    // ---------------------------------------------------------------- the routes of keel 0.15 (by name, per project)
    // keel 0.18.55: each needs the plugin on for the project (plugins.require), like schema and query; the editor's
    // routes by id (Control › Databases, a project's Databases) stay open so connections can be set up before.

    /** keel 0.15's list. An agent (keel2 mcp) does not see a connection whose Access is none: not its host, user or database. */
    fun list(pid: String, agent: Boolean = false): List<DbConnection> {
        projects.require(pid)
        plugins.require(pid, "db")
        return store.active(pid).filter { !agent || forMcp(it) != "none" }.map(::conn)
    }

    /** The databases the project names (compose, .env.example, Spring's config, SQLite files) that are not saved yet. */
    fun suggest(pid: String): JsonNode {
        plugins.require(pid, "db")
        val root = projects.root(pid).toString()
        val saved = store.active(pid).map { it.shown }.toSet()
        val out = engine.post("/plugins/db/suggest", mapOf("root" to root))
        val arr = (out as? ArrayNode)?.deepCopy() ?: return out
        val it = arr.elements()
        while (it.hasNext()) {
            val s = it.next()
            if (s.path("shown").asText() in saved) it.remove()
            // the web never gets a compose password: `url` is the masked address; create/update read the real one again (resolveUrl)
            else if (s is ObjectNode && s.has("shown")) s.put("url", s.path("shown").asText())
        }
        return arr
    }

    /** A suggestion comes to the web masked (•••); when it comes back to be saved, the real address is read again from the project. */
    private fun resolveUrl(pid: String?, url: String): String {
        if (!url.contains(MASK)) return url
        val raw = pid?.let {
            (engine.post("/plugins/db/suggest", mapOf("root" to projects.root(it).toString())) as? ArrayNode)
                ?.firstOrNull { s -> s.path("shown").asText() == url }?.path("url")?.asText()
        }
        return raw?.takeIf { it.isNotBlank() }
            ?: throw BadRequest("The password of this address is hidden", "Type the address with its password, or pick the suggestion again.")
    }

    fun create(pid: String, body: DbConnectionBody): Map<String, Any?> {
        projects.require(pid)
        plugins.require(pid, "db")
        val env = (body.env ?: "local").lowercase()
        // keel 0.15's route: agents keep what they had there (keel2 mcp's keel_db_change asked the person on local and test)
        val r = save(null, DbConfigBody(name = body.name.trim().ifBlank { "local" }, env = env,
            url = body.url?.trim().orEmpty().ifBlank { throw BadRequest("Give the database's address") }, source = body.source,
            agentAccess = if (env in CHANGE_ENVS) "write_approve" else "read"), pid, pid)
        val t = testRow(r, pid)        // saved either way: the person sees why a test failed, and fixes the address
        return mapOf("connection" to conn(byId(r.id)), "test" to t)
    }

    fun update(pid: String, name: String, body: DbConnectionBody): DbConnection {
        plugins.require(pid, "db")
        val r = own(pid, name)
        val saved = save(r, DbConfigBody(url = body.url?.trim()?.ifBlank { null }, env = body.env), pid, pid)
        return conn(saved)
    }

    fun delete(pid: String, name: String) {
        plugins.require(pid, "db")
        remove(own(pid, name))
    }

    fun test(pid: String, name: String): Map<String, Any?> {
        plugins.require(pid, "db")
        val r = active(pid, name)
        val t = testRow(r, pid)
        return mapOf("connection" to conn(byId(r.id)), "test" to t)
    }

    fun schema(pid: String, connection: String?, agent: Boolean = false): JsonNode {
        plugins.require(pid, "db")
        val name = connection?.takeIf { it.isNotBlank() } ?: default(pid)
        return engine.post("/plugins/db/schema", mapOf("root" to projects.root(pid).toString(), "connection" to spec(pid, name, agent)), long = true)
    }

    /** The person's own query: a read, or a change on a local or test database (counted first, then `confirm`). An agent's
     *  (keel2 mcp) also needs the connection's Access: none refuses all, a change needs "change after approval"; and its
     *  rows are always masked (keel 0.18.55: `"mask": false` from an MCP client no longer shows a secret column). */
    fun query(pid: String, body: DbQueryBody, agent: Boolean = false, client: String? = null): JsonNode {
        plugins.require(pid, "db")
        if (body.sql.isBlank()) throw BadRequest("Write a query first")
        if (body.sql.length > 20_000) throw BadRequest("The query is too long")
        val r = active(pid, body.connection.ifBlank { default(pid) })
        // #96 an agent's query on a private connection: the engine keeps the rows for the person and answers an id
        val onlyPerson = agent && agentMay(r, body.change) == "private"
        return engine.post("/plugins/db/query", mapOf("root" to projects.root(pid).toString(), "connection" to specOf(r, pid),
            "sql" to body.sql, "change" to body.change, "confirm" to body.confirm, "mask" to (body.mask || agent),
            "private" to onlyPerson, "project" to pid, "who" to (client?.takeIf { it.isNotBlank() } ?: "an MCP client")), long = true)
    }

    // ---------------------------------------------------------------- #96 the person's private results (never an MCP call's)

    fun results(pid: String): JsonNode {
        plugins.require(pid, "db")
        return engine.post("/plugins/db/results", mapOf("project" to pid))
    }

    fun result(pid: String, id: String): JsonNode {
        plugins.require(pid, "db")
        return engine.post("/plugins/db/result", mapOf("project" to pid, "id" to id))
    }

    fun forget(pid: String, id: String): JsonNode {
        plugins.require(pid, "db")
        return engine.post("/plugins/db/forget", mapOf("project" to pid, "id" to id))
    }

    /** The local connection, else the test one, else the first. */
    private fun default(pid: String): String {
        val all = store.active(pid)
        if (all.isEmpty()) throw Conflict("This project has no database connection", "Add one in Databases.")
        return (all.firstOrNull { it.env == "local" } ?: all.firstOrNull { it.env == "test" } ?: all.first()).name
    }

    // ---------------------------------------------------------------- global and per project (by id)

    /** Control › Databases: every global connection (an agent: those it may use). */
    fun globals(agent: Boolean = false): List<DbEntry> =
        store.globals().filter { !agent || forMcp(it) != "none" }.map { entry(it) }.map { if (agent) mcpView(it) else it }

    /** #96 an MCP caller reads what it really may do with a connection: its Access kept to the MCP switch. */
    private fun mcpView(e: DbEntry): DbEntry = e.copy(agentAccessEffective = capped(e.agentAccessEffective))

    /** A project's Databases: its own, the globals it sees (overridden when it has its own of that name), the ones it hides.
     *  An agent (keel2 mcp) sees only the ones it may use. */
    fun effective(pid: String, agent: Boolean = false): List<DbEntry> {
        projects.require(pid)
        val seen = store.effective(pid).map { entry(it.row, it.overridden) } + store.hiddenGlobals(pid).map { entry(it, hidden = true) }
        return if (agent) seen.filter { e -> !e.hidden && !e.overridden && capped(e.agentAccessEffective) != "none" }.map(::mcpView) else seen
    }

    /** One connection; to an agent, one whose Access is none does not exist (keel 0.18.55: it showed host, port, user, db). */
    fun get(id: String, agent: Boolean = false): DbEntry {
        val r = byId(id)
        if (agent && forMcp(r) == "none") throw NotFound("No database connection with the id $id")
        return if (agent) mcpView(entry(r)) else entry(r)
    }

    fun createGlobal(body: DbConfigBody): DbEntry {
        val target = if (body.scope == "project") body.project?.also { projects.require(it) }
            ?: throw BadRequest("Name the project of a project connection") else DatabaseStore.GLOBAL
        return entry(save(null, body, target, body.project))
    }

    fun createIn(pid: String, body: DbConfigBody): DbEntry {
        projects.require(pid)
        if (body.scope == "global") throw Forbidden("A connection for every project is added in Control › Databases", "There it needs the admin level.")
        return entry(save(null, body, pid, pid))
    }

    /** Any connection by id (Control › Databases); a move between "all projects" and one project too. */
    fun updateById(id: String, body: DbConfigBody, pid: String? = null): DbEntry {
        val r = byId(id)
        val target = when (body.scope) {
            "global" -> DatabaseStore.GLOBAL
            "project" -> (body.project ?: r.projectId.takeUnless { r.global } ?: pid)?.also { projects.require(it) }
                ?: throw BadRequest("Name the project this connection moves to")
            null -> r.projectId
            else -> throw BadRequest("Pick global or project")
        }
        return entry(save(r, body, target, pid ?: target.takeUnless { it == DatabaseStore.GLOBAL }))
    }

    /** A project's own connection by id (its Databases view); a global one is changed in Control › Databases. */
    fun updateIn(pid: String, id: String, body: DbConfigBody): DbEntry {
        val r = byId(id)
        if (r.projectId != pid) throw Conflict("${r.name} is shared with every project", "Change it in Control › Databases.")
        if (body.scope == "global") throw Forbidden("Sharing a connection with every project is done in Control › Databases", "There it needs the admin level.")
        return entry(save(r, body.copy(scope = null), pid, pid))
    }

    fun deleteById(id: String) = remove(byId(id))

    fun deleteIn(pid: String, id: String) {
        val r = byId(id)
        if (r.projectId != pid) throw Conflict("${r.name} is shared with every project", "Remove it in Control › Databases, or hide it here.")
        remove(r)
    }

    private fun remove(r: DbRow) {
        store.forget(r.id)
        jdbc.update("DELETE FROM db_connections WHERE id = ?", r.id)
    }

    /** A project turns a global connection off for itself (or on again). */
    fun hide(pid: String, id: String, hidden: Boolean): List<DbEntry> {
        projects.require(pid)
        val r = byId(id)
        if (!r.global) throw BadRequest("Only a connection for every project can be hidden in one", "Remove a project's own connection instead.")
        if (!store.allowedFor(r, pid)) throw NotFound("${r.name} is not for this project")
        store.setHidden(id, pid, hidden)
        return effective(pid)
    }

    fun testById(id: String, pid: String?): Map<String, Any?> {
        val r = byId(id)
        pid?.let { projects.require(it) }
        val t = testRow(r, pid ?: r.projectId.takeUnless { r.global })
        return mapOf("connection" to entry(byId(id)), "test" to t)
    }

    /** A project tests one of the connections it sees (its own, or a global one for it). */
    fun testIn(pid: String, id: String): Map<String, Any?> {
        val r = byId(id)
        if (r.projectId != pid && !(r.global && store.allowedFor(r, pid))) throw NotFound("${r.name} is not for this project")
        return testById(id, pid)
    }

    /** The editor's Test connection before it saves: the typed fields, with the typed password or the kept one. */
    fun testDraft(body: DbConfigBody, pid: String?): DbTestResult {
        pid?.let { projects.require(it) }
        val p = draft(body, pid)
        val url = connectUrl(p, pid) ?: return DbTestResult(false, error = p.unavailable?.first ?: unavailable(p, pid)?.first,
            hint = p.unavailable?.second ?: unavailable(p, pid)?.second)
        return runTest(p.row, url, pid)
    }

    /** The databases on the server (the editor's Default database), with the draft's fields. */
    fun serverDatabases(body: DbConfigBody, pid: String?): JsonNode {
        pid?.let { projects.require(it) }
        val p = draft(body, pid)
        val url = connectUrl(p, pid) ?: (p.unavailable ?: unavailable(p, pid)).let { throw Conflict(it?.first ?: "keel cannot connect", it?.second) }
        val root = pid?.let { projects.root(it).toString() } ?: ""
        return scrubbed(url) {
            engine.post("/plugins/db/databases", mapOf("root" to root, "connection" to mapOf("name" to p.row.name, "kind" to p.row.kind, "url" to url,
                "env" to p.row.env)), long = true)
        }
    }

    /** The editor's unsaved fields, on top of the saved connection they edit (its kept password), named or not. */
    private fun draft(body: DbConfigBody, pid: String?): Prepared {
        val old = body.id?.let { store.byId(it) }
        return prepare(old, body.copy(name = body.name?.takeIf { it.isNotBlank() } ?: old?.name ?: "draft"), DatabaseStore.GLOBAL, pid)
    }

    // ---------------------------------------------------------------- saving

    /** A connection about to be saved or tested: its row, its full address, whether its address changed. */
    private class Prepared(val row: DbRow, val full: String?, val changed: Boolean, val allow: List<String>?, val unavailable: Pair<String, String?>?)

    private fun connectUrl(p: Prepared, pid: String?): String? =
        if (p.row.connectMode == "env") store.connect(p.row, pid).url else p.full

    private fun unavailable(p: Prepared, pid: String?): Pair<String, String?>? =
        store.connect(p.row, pid).let { c -> c.unavailable?.let { it to c.hint } }

    private fun prepare(old: DbRow?, body: DbConfigBody, target: String, pid: String?): Prepared {
        var driver = body.driver ?: old?.driver
        var mode = body.connectMode ?: old?.connectMode ?: "host"
        var host = body.host ?: old?.host
        var port = body.port ?: old?.port
        var user = body.username ?: old?.username
        var db = body.defaultDb ?: old?.defaultDb
        var override = body.urlOverride ?: old?.urlOverride
        var socket = body.socketPath ?: old?.socketPath
        var password = body.password?.takeIf { it.isNotEmpty() }
        var exact: String? = null          // a whole address given: kept as it is
        var typed: DbAddress? = null       // an address typed in "URL only": its secret parameters too

        body.url?.trim()?.takeIf { it.isNotBlank() }?.let { given ->
            val url = resolveUrl(pid, given)
            val a = DbUrls.parse(url) ?: throw BadRequest("keel cannot read this database address", DatabaseStore.ADDRESS_HINT)
            driver = a.driver; host = a.host; port = a.port; user = a.username; db = a.database; socket = a.socket
            val plain = a.params == null && a.secretParams == null
            mode = when {
                a.driver == "sqlite" -> "host"
                a.socket != null && plain -> "socket"
                plain -> "host"
                else -> "url"
            }
            override = if (mode == "url") DbUrls.withoutPassword(url) else null
            password = password ?: a.password
            exact = url
        }
        if (exact == null && mode == "url" && body.urlOverride != null) {
            val given = body.urlOverride.trim()
            if (given.contains(MASK) && old != null && given == old.urlOverride) {
                override = old.urlOverride      // unchanged: the kept address stays as it is
            } else if (old == null || given != old.urlOverride || password != null) {
                val url = resolveUrl(pid, given.ifBlank { throw BadRequest("Give the database's address") })
                val a = DbUrls.parse(url) ?: throw BadRequest("keel cannot read this database address", DatabaseStore.ADDRESS_HINT)
                driver = a.driver; host = a.host; port = a.port; user = a.username; db = a.database; socket = a.socket
                password = password ?: a.password
                override = DbUrls.withoutPassword(url)
                typed = a
            }
        }

        val name = (body.name ?: old?.name).orEmpty().trim()
        if (!NAME.matches(name)) throw BadRequest("Give the connection a short name", "Letters, digits, spaces, '.', '-' and '_', for example local or LOC 8.4.")
        val env = (body.env ?: old?.env ?: "local").lowercase()
        if (env !in ENVS) throw BadRequest("Pick local, test, staging or prod")
        val drv = driver?.lowercase() ?: throw BadRequest("Pick the kind of database", "MySQL, MariaDB, PostgreSQL or SQLite.")
        if (drv !in DbUrls.DRIVERS) throw BadRequest("keel does not know the database $drv", "MySQL, MariaDB, PostgreSQL or SQLite.")
        if (drv == "sqlite" && mode != "env") mode = "host"
        if (drv == "sqlite" && mode != "env") checkSqlite(db, target, body.projects ?: old?.takeIf { it.global }?.let { store.allowed(it.id) }, pid)
        if (mode !in MODES) throw BadRequest("Connect with host, socket, url or env")
        if (port != null && port !in 1..65535) throw BadRequest("A port is a number from 1 to 65535")
        val envVar = (body.envVar ?: old?.envVar)?.trim()?.ifBlank { null }
        when (mode) {
            "env" -> if (envVar == null || !ENV_NAME.matches(envVar)) throw BadRequest("Name the variable in the project's .env", "For example DATABASE_URL.")
            "socket" -> if (socket.isNullOrBlank()) throw BadRequest("Give the socket's path", "For example /var/run/postgresql or /tmp/mysql.sock.")
            "url" -> if (override.isNullOrBlank()) throw BadRequest("Give the database's address")
            else -> if (drv == "sqlite") {
                if (db.isNullOrBlank()) throw BadRequest("Give the SQLite file", "A path in the project, for example data/app.db.")
            } else if (host.isNullOrBlank()) throw BadRequest("Give the database's host", "localhost is your computer.")
        }
        val access = (body.agentAccess ?: old?.agentAccess ?: "read").lowercase()
        if (access !in ACCESS) throw BadRequest("Agents may do nothing, run reads whose results only you see, read, or change after approval")
        val keep = if (drv == "sqlite") "keel" else (body.keepPassword ?: old?.keepPassword ?: "keel").lowercase()
        if (keep !in KEEPS) throw BadRequest("Keep the password in keel, for this session, or never")
        val note = (body.note ?: old?.note)?.trim()?.ifBlank { null }
        if ((note?.length ?: 0) > 500) throw BadRequest("The note is too long", "500 characters at most.")

        val draft = DbRow(
            id = old?.id ?: "", projectId = target, name = name, kind = DbUrls.kindOf(drv), driver = drv, env = env, shown = "",
            source = body.source?.take(200) ?: old?.source, ok = old?.ok, server = old?.server, tables = old?.tables, error = old?.error,
            checkedAt = old?.checkedAt, connectMode = mode, host = host?.trim()?.ifBlank { null }, port = port,
            username = user?.trim()?.ifBlank { null }, defaultDb = db?.trim()?.ifBlank { null }, urlOverride = override?.trim()?.ifBlank { null },
            socketPath = socket?.trim()?.ifBlank { null }, envVar = envVar, note = note, agentAccess = access,
            askFirst = body.askFirst ?: old?.askFirst ?: false, keepPassword = keep,
        )
        val address = store.address(draft)
        val oldFull = old?.let { store.kept(it) }
        val same = old != null && exact == null && typed == null && address == store.address(old) && password == null && !body.clearPassword
        val full: String? = when {
            mode == "env" -> null
            exact != null -> exact
            same -> oldFull ?: address
            else -> {
                val pw = when {
                    body.clearPassword -> null
                    password != null -> password
                    else -> DbUrls.passwordOf(oldFull)
                }
                typed?.let { DbUrls.build(it.copy(password = pw)) } ?: address?.let { DbUrls.withPassword(it, pw) }
            }
        }
        val shown = if (mode == "env") "$envVar from .env" else shownOf(full ?: address.orEmpty())
        val unavailable = if (mode != "env" && full == null) "keel has no address for $name" to "Fill in the connection's fields." else null
        return Prepared(draft.copy(shown = shown), full, old == null || address != store.address(old) || exact != null || typed != null,
            body.projects, unavailable)
    }

    /**
     * keel 0.18.55: a SQLite file lies inside a project folder it is for (a project's own: its folder; one for every
     * project: the folders of the projects it names, else of any project), never in keel's own data folder (/data,
     * KEEL_DATA: keel's own database). Symlinks are followed where the file exists; the engine checks again when it opens it.
     */
    private fun checkSqlite(file: String?, target: String, allow: List<String>?, pid: String?) {
        val raw = file?.trim()?.removePrefix("file:").orEmpty()
        if (raw.isEmpty()) return
        val hint = "Use a SQLite file inside the project folder, for example data/app.db."
        val p = try { Paths.get(raw) } catch (e: InvalidPathException) { throw BadRequest("keel cannot read this SQLite path", hint) }
        val roots = when {
            target != DatabaseStore.GLOBAL -> listOf(target)
            !allow.isNullOrEmpty() -> allow + listOfNotNull(pid)
            pid != null -> listOf(pid)
            else -> projects.idsByRoot().values.toList()
        }.mapNotNull { id -> projects.find(id)?.root?.let { Paths.get(it) } }.map(::real)
        val data = listOf(Paths.get("/data"), props.dataDir).map(::real)
        val files = if (p.isAbsolute) listOf(real(p)) else {
            if (p.normalize().startsWith("..")) throw BadRequest("The SQLite file must be inside the project folder", hint)
            roots.map { real(it.resolve(p)) }
        }
        if (files.any { f -> data.any { f.startsWith(it) } }) throw BadRequest("keel never opens its own data folder as a SQLite connection", hint)
        if (p.isAbsolute && roots.none { files[0].startsWith(it) }) throw BadRequest("The SQLite file must be inside the project folder", hint)
    }

    /** The path with its symlinks resolved as far as it exists (a file not made yet keeps its name under its real folder). */
    private fun real(p: Path): Path {
        val abs = p.toAbsolutePath().normalize()
        var base: Path? = abs
        while (base != null && !Files.exists(base)) base = base.parent
        if (base == null) return abs
        return runCatching { base.toRealPath().resolve(base.relativize(abs)) }.getOrDefault(abs)
    }

    private fun save(old: DbRow?, body: DbConfigBody, target: String, pid: String?): DbRow {
        val p = prepare(old, body, target, pid)
        val r = p.row
        val clash = jdbc.queryForObject("SELECT COUNT(*) FROM db_connections WHERE project_id = ? AND name = ? AND id <> ?", Int::class.java,
            target, r.name, old?.id ?: "")!!
        if (clash > 0) {
            throw Conflict("There is a connection called ${r.name} ${if (target == DatabaseStore.GLOBAL) "for every project" else "in this project"} already",
                "Pick another name, or edit that one.")
        }
        p.allow?.forEach { if (projects.find(it) == null) throw BadRequest("There is no project $it") }
        val id = old?.id ?: UUID.randomUUID().toString().replace("-", "").take(16)
        if (old == null) {
            jdbc.update("INSERT INTO db_connections(id, project_id, name, kind, env, shown, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                id, target, r.name, r.kind, r.env, r.shown, r.source, Time.now())
        }
        jdbc.update(
            "UPDATE db_connections SET project_id = ?, name = ?, kind = ?, driver = ?, env = ?, shown = ?, source = ?, connect_mode = ?, host = ?, " +
                "port = ?, username = ?, default_db = ?, url_override = ?, socket_path = ?, env_var = ?, note = ?, agent_access = ?, ask_first = ?, " +
                "keep_password = ?, updated_at = ? WHERE id = ?",
            target, r.name, r.kind, r.driver, r.env, r.shown, r.source, r.connectMode, r.host, r.port, r.username, r.defaultDb, r.urlOverride,
            r.socketPath, r.envVar, r.note, r.agentAccess, if (r.askFirst) 1 else 0, r.keepPassword, Time.now(), id,
        )
        if (p.changed && old != null) jdbc.update("UPDATE db_connections SET ok = NULL, error = NULL, server = NULL, tables = NULL WHERE id = ?", id)
        store.keep(id, r.keepPassword, p.full)
        if (target == DatabaseStore.GLOBAL) {
            p.allow?.let { store.setAllowed(id, it) }
        } else if (old?.global == true) {
            store.setAllowed(id, emptyList())
            jdbc.update("DELETE FROM db_connection_hidden WHERE connection_id = ?", id)
        }
        return byId(id)
    }

    // ---------------------------------------------------------------- testing

    private fun testRow(r: DbRow, pid: String?): DbTestResult {
        val c = store.connect(r, pid)
        val t = if (c.url == null) DbTestResult(false, error = c.unavailable, hint = c.hint) else runTest(r, c.url, pid)
        jdbc.update("UPDATE db_connections SET ok = ?, server = ?, tables = ?, error = ?, checked_at = ? WHERE id = ?",
            if (t.ok) 1 else 0, t.server, if (t.ok) t.tables else null,
            if (t.ok) null else listOfNotNull(t.error, t.hint).joinToString(" ").trim().take(500), Time.now(), r.id)
        return t
    }

    private fun runTest(r: DbRow, url: String, pid: String?): DbTestResult {
        val root = pid?.let { runCatching { projects.root(it).toString() }.getOrNull() } ?: ""
        val started = System.nanoTime()
        val t = scrubbed(url) {
            engine.post("/plugins/db/test", mapOf("root" to root, "connection" to mapOf("name" to r.name,
                "kind" to (DbUrls.driverOf(url)?.let(DbUrls::kindOf) ?: r.kind), "url" to url, "env" to r.env)), long = true)
        }
        val ms = t.path("ms").takeIf { it.isNumber }?.asLong() ?: ((System.nanoTime() - started) / 1_000_000)
        val ok = t.path("ok").asBoolean(false)
        val server = t.path("server").asText(null)?.let { s -> if (r.driver == "mariadb") s.replace("MySQL", "MariaDB") else s }
        return if (ok) DbTestResult(true, server, t.path("tables").asInt(), ms)
        else DbTestResult(false, error = scrub(t.path("error").asText(""), url), hint = scrub(t.path("hint").asText(null), url), ms = ms)
    }

    /** An engine call whose refusal may quote the address: its text comes back without the password. */
    private fun scrubbed(url: String, call: () -> JsonNode): JsonNode = try {
        call()
    } catch (e: ApiException) {
        throw ApiException(e.status, scrub(e.message, url) ?: "keel could not connect", scrub(e.hint, url))
    }

    companion object {
        val ENVS = setOf("local", "test", "staging", "prod")
        val CHANGE_ENVS = setOf("local", "test")
        val MODES = setOf("host", "socket", "url", "env")
        val ACCESS = setOf("none", "private", "read", "write_approve")
        /** none < private < read < write_approve */
        val ACCESS_ORDER = listOf("none", "private", "read", "write_approve")
        val MCP_ACCESS = setOf("connection", "private", "none")
        const val MCP_KEY = "db.mcp"
        val KEEPS = setOf("keel", "session", "never")
        val NAME = Regex("^[A-Za-z0-9][A-Za-z0-9 _.-]{0,47}$")
        private val ENV_NAME = Regex("^[A-Za-z_][A-Za-z0-9_]{0,127}$")

        fun kindOf(url: String): String {
            val driver = DbUrls.driverOf(url) ?: throw BadRequest("keel cannot read this database address", DatabaseStore.ADDRESS_HINT)
            return DbUrls.kindOf(driver)
        }

        const val MASK = "•••"
        private val SCHEME = Regex("^((?:jdbc:)?[A-Za-z][A-Za-z0-9+.\\-]*://)(.*)$", RegexOption.DOT_MATCHES_ALL)
        private val SECRET_PARAM = Regex("([?&;])(password|passwd|pwd|pass|secret|token|api_?key|access_?token|sslpassword)=([^&;#]*)", RegexOption.IGNORE_CASE)
        private val URL_IN_TEXT = Regex("""(?:jdbc:)?[A-Za-z][A-Za-z0-9+.\-]*://\S+""")

        /** The address with every secret as •••: the password of user:pw@host (it may hold any character, up to the last @
         *  before the host: [DbUrls.userinfoEnd]) and the value of ?password=, &pwd=, ;secret=, token= … (any case). Same
         *  rules as the engine's masked_url. */
        fun shownOf(url: String): String {
            var out = url.trim()
            SCHEME.matchEntire(out)?.let { m ->
                val head = m.groupValues[1]
                val rest = m.groupValues[2]
                val at = DbUrls.userinfoEnd(rest)
                if (at >= 0 && ':' in rest.substring(0, at)) out = head + rest.substring(0, at).substringBefore(':') + ":$MASK@" + rest.substring(at + 1)
            }
            return SECRET_PARAM.replace(out) { "${it.groupValues[1]}${it.groupValues[2]}=$MASK" }
        }

        /** A text (an error, a hint) without the address's password: every address in it masked, the password itself as •••. */
        fun scrub(text: String?, url: String?): String? {
            if (text == null) return null
            var out = URL_IN_TEXT.replace(text) { shownOf(it.value) }
            val pw = DbUrls.passwordOf(url)
            if (pw != null && pw.length >= 3) {
                out = out.replace(pw, MASK).replace(DbUrls.enc(pw), MASK)
            }
            return out
        }
    }
}
