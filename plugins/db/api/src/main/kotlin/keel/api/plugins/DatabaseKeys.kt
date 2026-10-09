package keel.api.plugins

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.connections.SecretService
import keel.api.flow.FlowContributor
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component

/**
 * The Database plugin's secrets for a project's flows and KeelBot turns, while the plugin is on for the project: each
 * connection as the engine's key `db:<name>` → {name, kind, url, env} as JSON, in name order (the engine keeps them in
 * memory). Before step 3 core's PluginService.keysFor read them; the keys are the same.
 */
@Component
class DatabaseKeys(
    private val jdbc: JdbcTemplate,
    private val secrets: SecretService,
    private val plugins: PluginService,
    private val mapper: ObjectMapper,
) : FlowContributor {
    override fun keys(pid: String): Map<String, String> {
        if (!plugins.on(pid, "db")) return emptyMap()
        val out = linkedMapOf<String, String>()
        jdbc.query("SELECT id, name, kind, env FROM db_connections WHERE project_id = ? ORDER BY name",
            { rs, _ -> listOf(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4)) }, pid,
        ).forEach { (id, name, kind, env) ->
            secrets.get("db.$id")?.let { url ->
                out["db:$name"] = mapper.writeValueAsString(mapOf("name" to name, "kind" to kind, "url" to url, "env" to env))
            }
        }
        return out
    }
}
