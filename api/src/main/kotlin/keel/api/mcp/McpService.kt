package keel.api.mcp

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Json
import keel.api.common.KeelHome
import keel.api.common.KvStore
import keel.api.common.NotFound
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import org.springframework.boot.ApplicationArguments
import org.springframework.boot.ApplicationRunner
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

@JsonInclude(JsonInclude.Include.NON_NULL)
data class McpServerSpec(
    val name: String = "",
    val command: String = "",
    val args: List<String> = emptyList(),
    val env: Map<String, String>? = null,
    val cwd: String? = null,
)

@JsonInclude(JsonInclude.Include.NON_NULL)
data class McpServer(
    val name: String,
    val command: String,
    val args: List<String>,
    val env: Map<String, String>?,
    val cwd: String?,
    val enabled: Boolean,
    val builtin: Boolean,
    val status: String,
    val tools: List<String>,
) {
    fun spec() = McpServerSpec(name, command, args, env, cwd)
}

data class McpUpdate(
    val command: String? = null,
    val args: List<String>? = null,
    val env: Map<String, String>? = null,
    val cwd: String? = null,
    val enabled: Boolean? = null,
)

data class McpTestResult(val ok: Boolean, val tools: List<Map<String, String?>>, val error: String? = null)

/** MCP servers (global) and the per-project allowlist (agent → tools). The `keel` server is built in. */
@Service
class McpService(
    private val jdbc: JdbcTemplate,
    private val home: KeelHome,
    private val engine: EngineClient,
    private val kv: KvStore,
    private val projects: ProjectService,
) : ApplicationRunner {

    override fun run(args: ApplicationArguments?) = seed()

    fun seed() {
        // The keel server always points at the current KEEL_HOME.
        jdbc.update(
            "INSERT INTO mcp_servers(name, command, args_json, enabled, builtin, status) VALUES ('keel', 'node', ?, 1, 1, 'off') " +
                "ON CONFLICT(name) DO UPDATE SET command = 'node', args_json = excluded.args_json, builtin = 1",
            Json.write(listOf(home.mcpServer().toString())),
        )
    }

    fun list(): List<McpServer> =
        jdbc.query("SELECT name, command, args_json, env_json, cwd, enabled, builtin, status, tools_json FROM mcp_servers ORDER BY builtin DESC, name") { rs, _ ->
            McpServer(
                rs.getString(1), rs.getString(2), Json.readList(rs.getString(3)),
                rs.getString(4)?.let { Json.readMap(it).mapValues { (_, v) -> v.toString() } }, rs.getString(5),
                rs.getInt(6) == 1, rs.getInt(7) == 1, rs.getString(8), Json.readList(rs.getString(9)),
            )
        }

    fun get(name: String): McpServer = list().firstOrNull { it.name == name } ?: throw NotFound("No MCP server called \"$name\"")

    fun create(spec: McpServerSpec): McpServer {
        if (!Regex("^[a-z0-9][a-z0-9_-]{0,40}$").matches(spec.name)) throw BadRequest("Name the server with lowercase letters, digits, - or _")
        if (spec.command.isBlank()) throw BadRequest("command is empty", "For example: npx, node, uvx, docker.")
        if (list().any { it.name == spec.name }) throw Conflict("A server called \"${spec.name}\" already exists")
        jdbc.update(
            "INSERT INTO mcp_servers(name, command, args_json, env_json, cwd, enabled, builtin, status) VALUES (?, ?, ?, ?, ?, 1, 0, 'off')",
            spec.name, spec.command, Json.write(spec.args), spec.env?.let { Json.write(it) }, spec.cwd,
        )
        return get(spec.name)
    }

    fun update(name: String, body: McpUpdate): McpServer {
        val cur = get(name)
        if (cur.builtin && (body.command != null || body.args != null)) {
            throw Conflict("The keel server's command is fixed", "You can turn it off or give it env values.")
        }
        jdbc.update(
            "UPDATE mcp_servers SET command = ?, args_json = ?, env_json = ?, cwd = ?, enabled = ? WHERE name = ?",
            body.command ?: cur.command, Json.write(body.args ?: cur.args), (body.env ?: cur.env)?.let { Json.write(it) },
            body.cwd ?: cur.cwd, if (body.enabled ?: cur.enabled) 1 else 0, name,
        )
        return get(name)
    }

    fun delete(name: String) {
        if (get(name).builtin) throw Conflict("The keel server is built in and cannot be deleted", "Turn it off instead.")
        jdbc.update("DELETE FROM mcp_servers WHERE name = ?", name)
    }

    /** tools/list through the engine; remembers the status and tool names. */
    fun test(name: String): McpTestResult {
        val server = get(name)
        val res = try { engine.mcpTools(server.spec()) } catch (e: EngineDown) { throw e }
        val ok = res.get("ok")?.asBoolean() == true
        val tools = res.get("tools")?.map { mapOf("name" to it.get("name")?.asText(), "description" to it.get("description")?.asText()) } ?: emptyList()
        val error = res.get("error")?.takeIf { !it.isNull }?.asText()
        jdbc.update(
            "UPDATE mcp_servers SET status = ?, tools_json = ? WHERE name = ?",
            if (ok) "ok" else "error", Json.write(tools.mapNotNull { it["name"] }), name,
        )
        return McpTestResult(ok, tools, error)
    }

    /** Enabled servers whose name is in [names] — what a flow may use. */
    fun specsFor(names: List<String>): List<McpServerSpec> = list().filter { it.enabled && it.name in names }.map { it.spec() }

    fun allow(pid: String): Map<String, List<String>> {
        projects.require(pid)
        return kv.get<Map<String, List<String>>>("mcp-allow:$pid") ?: emptyMap()
    }

    fun saveAllow(pid: String, value: Map<String, List<String>>): Map<String, List<String>> {
        projects.require(pid)
        kv.put("mcp-allow:$pid", value)
        return value
    }
}
