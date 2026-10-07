package keel.api.plugins

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.util.UUID

data class DbConnectionBody(val name: String = "", val url: String? = null, val env: String? = null, val source: String? = null)
/** mask: a model reads the rows (keel2 mcp), so columns named like a secret come back as •••. */
data class DbQueryBody(val connection: String = "", val sql: String = "", val change: Boolean = false, val confirm: Boolean = false,
                       val mask: Boolean = false)

/** One connection as the web sees it: never the password. */
data class DbConnection(
    val name: String, val kind: String, val env: String, val shown: String, val source: String?,
    val ok: Boolean?, val server: String?, val tables: Int?, val error: String?, val checkedAt: String?,
    /** a change of data may run here (local and test databases; staging and prod are read only, always) */
    val canChange: Boolean,
)

/**
 * The Database plugin's connections and the person's own queries (Connections › Databases, Map › Query, KeelBot's
 * buttons). The address with its password is a secret (`db.<id>`); the engine (keel_engine/plugins/db) connects, tests,
 * reads the schema and runs queries under its rules.
 */
@Service
class DatabaseService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val secrets: SecretService,
    private val plugins: PluginService,
) {
    private data class Row(val id: String, val conn: DbConnection)

    private fun rows(pid: String, name: String? = null): List<Row> = jdbc.query(
        "SELECT id, name, kind, env, shown, source, ok, server, tables, error, checked_at FROM db_connections " +
            "WHERE project_id = ?" + (if (name != null) " AND name = ?" else "") + " ORDER BY name",
        { rs, _ ->
            val ok = rs.getObject(7)?.let { (it as Number).toInt() == 1 }
            val tables = rs.getObject(9)?.let { (it as Number).toInt() }
            Row(rs.getString(1), DbConnection(rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6), ok,
                rs.getString(8), tables, rs.getString(10), rs.getString(11), rs.getString(4) in CHANGE_ENVS))
        },
        *listOfNotNull(pid, name).toTypedArray(),
    )

    private fun row(pid: String, name: String): Row = rows(pid, name).firstOrNull()
        ?: throw NotFound("No database connection called $name", "Add it in Connections › Databases.")

    private fun spec(pid: String, name: String): Map<String, Any?> {
        val r = row(pid, name)
        val url = secrets.get("db.${r.id}") ?: throw Conflict("The address of $name is gone", "Edit the connection and set it again.")
        return mapOf("name" to r.conn.name, "kind" to r.conn.kind, "url" to url, "env" to r.conn.env)
    }

    fun list(pid: String): List<DbConnection> {
        projects.require(pid)
        return rows(pid).map { it.conn }
    }

    /** The databases the project names (compose, .env.example, Spring's config, SQLite files) that are not saved yet. */
    fun suggest(pid: String): JsonNode {
        val root = projects.root(pid).toString()
        val saved = rows(pid).map { it.conn.shown }.toSet()
        val out = engine.post("/plugins/db/suggest", mapOf("root" to root))
        val arr = (out as? com.fasterxml.jackson.databind.node.ArrayNode)?.deepCopy() ?: return out
        val it = arr.elements()
        while (it.hasNext()) if (it.next().path("shown").asText() in saved) it.remove()
        return arr
    }

    fun create(pid: String, body: DbConnectionBody): Map<String, Any?> {
        projects.require(pid)
        val name = body.name.trim().ifBlank { "local" }
        if (!NAME.matches(name)) throw BadRequest("A connection name is a short word", "Letters, digits, - and _, for example local or test.")
        if (rows(pid, name).isNotEmpty()) throw Conflict("There is a connection called $name already", "Pick another name, or edit that one.")
        val url = body.url?.trim().orEmpty().ifBlank { throw BadRequest("Give the database's address") }
        val env = (body.env ?: "local").lowercase()
        if (env !in ENVS) throw BadRequest("Pick local, test, staging or prod")
        val kind = kindOf(url)
        val id = UUID.randomUUID().toString().replace("-", "").take(16)
        secrets.put("db.$id", url)
        jdbc.update("INSERT INTO db_connections(id, project_id, name, kind, env, shown, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            id, pid, name, kind, env, shownOf(url), body.source?.take(200), Time.now())
        return test(pid, name)        // saved either way: the person sees why a test failed, and fixes the address
    }

    fun update(pid: String, name: String, body: DbConnectionBody): DbConnection {
        val r = row(pid, name)
        body.url?.trim()?.takeIf { it.isNotBlank() }?.let { url ->
            kindOf(url)
            secrets.put("db.${r.id}", url)
            jdbc.update("UPDATE db_connections SET kind = ?, shown = ?, ok = NULL, error = NULL WHERE id = ?", kindOf(url), shownOf(url), r.id)
        }
        body.env?.lowercase()?.let { env ->
            if (env !in ENVS) throw BadRequest("Pick local, test, staging or prod")
            jdbc.update("UPDATE db_connections SET env = ? WHERE id = ?", env, r.id)
        }
        return row(pid, name).conn
    }

    fun delete(pid: String, name: String) {
        val r = row(pid, name)
        runCatching { secrets.delete("db.${r.id}") }
        jdbc.update("DELETE FROM db_connections WHERE id = ?", r.id)
    }

    fun test(pid: String, name: String): Map<String, Any?> {
        val r = row(pid, name)
        val t = engine.post("/plugins/db/test", mapOf("root" to projects.root(pid).toString(), "connection" to spec(pid, name)), long = true)
        val ok = t.path("ok").asBoolean(false)
        jdbc.update("UPDATE db_connections SET ok = ?, server = ?, tables = ?, error = ?, checked_at = ? WHERE id = ?",
            if (ok) 1 else 0, t.path("server").asText(null), if (ok) t.path("tables").asInt() else null,
            if (ok) null else (t.path("error").asText("") + " " + t.path("hint").asText("")).trim().take(500), Time.now(), r.id)
        return mapOf("connection" to row(pid, name).conn, "test" to t)
    }

    fun schema(pid: String, connection: String?): JsonNode {
        plugins.require(pid, "db")
        val name = connection?.takeIf { it.isNotBlank() } ?: default(pid)
        return engine.post("/plugins/db/schema", mapOf("root" to projects.root(pid).toString(), "connection" to spec(pid, name)), long = true)
    }

    /** The person's own query: a read, or a change on a local or test database (counted first, then `confirm`). */
    fun query(pid: String, body: DbQueryBody): JsonNode {
        plugins.require(pid, "db")
        if (body.sql.isBlank()) throw BadRequest("Write a query first")
        if (body.sql.length > 20_000) throw BadRequest("The query is too long")
        val name = body.connection.ifBlank { default(pid) }
        return engine.post("/plugins/db/query", mapOf("root" to projects.root(pid).toString(), "connection" to spec(pid, name),
            "sql" to body.sql, "change" to body.change, "confirm" to body.confirm, "mask" to body.mask), long = true)
    }

    /** The local connection, else the test one, else the first. */
    private fun default(pid: String): String {
        val all = rows(pid).map { it.conn }
        if (all.isEmpty()) throw Conflict("This project has no database connection", "Add one in Connections › Databases.")
        return (all.firstOrNull { it.env == "local" } ?: all.firstOrNull { it.env == "test" } ?: all.first()).name
    }

    companion object {
        val ENVS = setOf("local", "test", "staging", "prod")
        val CHANGE_ENVS = setOf("local", "test")
        val NAME = Regex("^[A-Za-z][A-Za-z0-9_-]{0,31}$")

        fun kindOf(url: String): String {
            val scheme = url.trim().removePrefix("jdbc:").substringBefore(":").substringBefore("+").lowercase()
            return when (scheme) {
                "postgres", "postgresql" -> "postgres"
                "mysql", "mariadb" -> "mysql"
                "sqlite", "sqlite3" -> "sqlite"
                else -> throw BadRequest("keel cannot read this database address",
                    "Use postgres://user:password@host:5432/db, mysql://user:password@host:3306/db or sqlite:path/to/file.db.") // keel:allow-secret
            }
        }

        /** The address with its password as •••. */
        fun shownOf(url: String): String = url.trim().replace(Regex("^([a-z0-9+:]+//[^:/@]+):[^@/]*@"), "$1:•••@")
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/db")
class DatabaseController(private val db: DatabaseService) {
    @GetMapping("/connections")
    fun list(@PathVariable pid: String): List<DbConnection> = db.list(pid)

    @PostMapping("/connections")
    fun create(@PathVariable pid: String, @RequestBody body: DbConnectionBody): Map<String, Any?> = db.create(pid, body)

    @PutMapping("/connections/{name}")
    fun update(@PathVariable pid: String, @PathVariable name: String, @RequestBody body: DbConnectionBody): DbConnection = db.update(pid, name, body)

    @DeleteMapping("/connections/{name}")
    fun delete(@PathVariable pid: String, @PathVariable name: String): Map<String, Boolean> {
        db.delete(pid, name)
        return mapOf("ok" to true)
    }

    @PostMapping("/connections/{name}/test")
    fun test(@PathVariable pid: String, @PathVariable name: String): Map<String, Any?> = db.test(pid, name)

    @GetMapping("/suggest")
    fun suggest(@PathVariable pid: String): JsonNode = db.suggest(pid)

    @GetMapping("/schema")
    fun schema(@PathVariable pid: String, @RequestParam(required = false) connection: String?): JsonNode = db.schema(pid, connection)

    @PostMapping("/query")
    fun query(@PathVariable pid: String, @RequestBody body: DbQueryBody): JsonNode = db.query(pid, body)
}
