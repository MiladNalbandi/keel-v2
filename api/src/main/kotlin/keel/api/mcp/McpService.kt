package keel.api.mcp

import com.fasterxml.jackson.annotation.JsonInclude
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Json
import keel.api.common.KeelProperties
import keel.api.common.KvStore
import keel.api.common.NotFound
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.projects.ProjectService
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.ApplicationArguments
import org.springframework.boot.ApplicationRunner
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

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
    /** What the server is, for the Tools page (keel's own and the optional keel v1 entry). */
    val label: String? = null,
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

/**
 * MCP servers (global) and the per-project allowlist (agent → tools). The `keel` server is built in: keel v2's own
 * MCP server, read-only (an agent never approves its own gate). keel v1's server is only an optional entry, offered
 * (off) when a keel v1 checkout is mounted at [KeelProperties.keelV1Optional] (`keel2 start --with-keel-v1 <path>`).
 */
@Service
class McpService(
    private val jdbc: JdbcTemplate,
    private val props: KeelProperties,
    private val engine: EngineClient,
    private val kv: KvStore,
    private val projects: ProjectService,
    private val secrets: SecretService,
    private val labels: ObjectProvider<McpServerLabel>,
) : ApplicationRunner {

    override fun run(args: ApplicationArguments?) = seed()

    fun seed() {
        // The keel server is always keel v2's own, read-only (an older install pointed it at keel v1's server.js).
        jdbc.update(
            "INSERT INTO mcp_servers(name, command, args_json, enabled, builtin, status) VALUES ('keel', ?, ?, 1, 1, 'off') " +
                "ON CONFLICT(name) DO UPDATE SET command = excluded.command, args_json = excluded.args_json, builtin = 1",
            props.mcpPythonCommand, Json.write(KEEL_ARGS),
        )
        seedKeelV1(Paths.get(props.keelV1Optional))
    }

    /** Offers keel v1's MCP server (off) when [dir] holds a keel v1 checkout; takes the untouched offer back when it is gone. */
    fun seedKeelV1(dir: Path) {
        val script = dir.resolve("mcp/server.js")
        if (Files.isRegularFile(script)) {
            if (kv.get<Boolean>(KEEL_V1_DISMISSED) == true) return
            jdbc.update(
                "INSERT INTO mcp_servers(name, command, args_json, enabled, builtin, status) VALUES (?, 'node', ?, 0, 0, 'off') ON CONFLICT(name) DO NOTHING",
                KEEL_V1, Json.write(listOf(script.toString())),
            )
        } else {
            jdbc.update("DELETE FROM mcp_servers WHERE name = ? AND enabled = 0 AND command = 'node' AND args_json = ?", KEEL_V1, Json.write(listOf(script.toString())))
        }
    }

    fun list(): List<McpServer> =
        jdbc.query("SELECT name, command, args_json, env_json, cwd, enabled, builtin, status, tools_json FROM mcp_servers ORDER BY builtin DESC, name") { rs, _ ->
            McpServer(
                rs.getString(1), rs.getString(2), Json.readList(rs.getString(3)),
                rs.getString(4)?.let { Json.readMap(it).mapValues { (_, v) -> v.toString() } }, rs.getString(5),
                rs.getInt(6) == 1, rs.getInt(7) == 1, rs.getString(8), Json.readList(rs.getString(9)), labelOf(rs.getString(1), rs.getString(2)),
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
            throw Conflict("The keel server's command is fixed: it is keel v2's own MCP server, read-only for agents", "You can turn it off or give it env values.")
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
        if (name == KEEL_V1) kv.put(KEEL_V1_DISMISSED, true)     // removed on purpose: do not offer it again
    }

    /** tools/list through the engine; remembers the status and tool names. */
    fun test(name: String): McpTestResult {
        val server = get(name)
        val res = try { engine.mcpTools(resolved(server.spec())) } catch (e: EngineDown) { throw e }
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
    fun specsFor(names: List<String>): List<McpServerSpec> = list().filter { it.enabled && it.name in names }.map { resolved(it.spec()) }

    /**
     * v0.5.0: an env value `secret:<name>` names a secret (Connections, AES-GCM); it is read only here, when a flow or a
     * test starts the server, so GET /api/mcp-servers never shows it (a plugin's catalog entry, like Jira's, keeps its
     * token this way).
     */
    private fun resolved(spec: McpServerSpec): McpServerSpec {
        val env = spec.env ?: return spec
        if (env.values.none { it.startsWith(SECRET_REF) }) return spec
        return spec.copy(env = env.mapValues { (_, v) -> if (v.startsWith(SECRET_REF)) secrets.get(v.removePrefix(SECRET_REF)).orEmpty() else v })
    }

    /** keel's own label, else the first a plugin gives ([McpServerLabel]). */
    private fun labelOf(name: String, command: String): String? =
        LABELS[name] ?: labels.orderedStream().map { it.label(name, command) }.filter { it != null }.findFirst().orElse(null)

    fun allow(pid: String): Map<String, List<String>> {
        projects.require(pid)
        return kv.get<Map<String, List<String>>>("mcp-allow:$pid") ?: emptyMap()
    }

    fun saveAllow(pid: String, value: Map<String, List<String>>): Map<String, List<String>> {
        projects.require(pid)
        kv.put("mcp-allow:$pid", value)
        return value
    }

    companion object {
        val KEEL_ARGS = listOf("-m", "keel_engine.mcp", "--read-only")
        const val KEEL_V1 = "keel-v1"
        const val SECRET_REF = "secret:"
        private const val KEEL_V1_DISMISSED = "mcp-keel-v1-dismissed"
        private val LABELS = mapOf("keel" to "keel v2 (read-only)", KEEL_V1 to "keel v1 (optional)")
    }
}
