package keel.api.marketplace

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.engine.EngineError
import keel.api.pluginhost.PluginHost
import org.springframework.stereotype.Service

/** The four rules of Sources and rules (docs/plugins/13-step4-contract.md §4, rules.json). */
data class MarketplaceRules(
    val agentsMayAsk: Boolean = true,
    val allowUnverified: Boolean = false,
    val checkDaily: Boolean = true,
    val restartWhenIdle: Boolean = false,
)

/**
 * The engine's marketplace (keel_engine/marketplace, docs/plugins/13-step4-contract.md §5): search the catalogs, one
 * plugin, what is installed, install, update, roll back, remove, sources and rules. The api checks names and passes the
 * engine's answers on as they are (JSON). A refusal keeps the engine's status, error, hint and every other field of its
 * answer (an update that asks for more permissions lists them).
 */
@Service
class Marketplace(private val engine: EngineClient, private val mapper: ObjectMapper) {

    fun search(q: String?, category: String?): JsonNode = pass {
        engine.get("/marketplace/search?q={q}&category={category}", mapOf("q" to q?.trim().orEmpty(), "category" to category?.trim().orEmpty()))
    }

    fun plugin(name: String): JsonNode = pass { engine.get("/marketplace/plugins/${checkName(name)}") }

    fun refresh(): JsonNode = pass { engine.post("/marketplace/refresh", null, long = true) }

    fun installed(): JsonNode = pass { engine.get("/marketplace/installed") }

    fun install(name: String, version: String?): JsonNode =
        pass { engine.post("/marketplace/install", body(name, version), long = true) }

    fun update(name: String, version: String?): JsonNode =
        pass { engine.post("/marketplace/installed/${checkName(name)}/update", versionBody(version), long = true) }

    fun rollback(name: String): JsonNode = pass { engine.post("/marketplace/installed/${checkName(name)}/rollback", null, long = true) }

    fun switch(name: String, on: Boolean): JsonNode = pass { engine.put("/marketplace/installed/${checkName(name)}", mapOf("on" to on)) }

    fun remove(name: String, data: String): JsonNode {
        if (data !in setOf("keep", "delete")) throw BadRequest("data is keep or delete", "keep: its data stays; delete: its folder in /data goes too.")
        return pass { engine.delete("/marketplace/installed/${checkName(name)}?data=$data") }
    }

    fun installFile(path: String): JsonNode {
        if (path.isBlank()) throw BadRequest("Say which file to install", "A .kplug file in /data, for example /data/hello-1.0.0.kplug.")
        return pass { engine.post("/marketplace/install-file", mapOf("path" to path.trim()), long = true) }
    }

    fun sources(): JsonNode = pass { engine.get("/marketplace/sources") }

    fun saveSources(body: JsonNode): JsonNode = pass { engine.put("/marketplace/sources", body) }

    fun rules(): JsonNode = pass { engine.get("/marketplace/rules") }

    fun saveRules(body: JsonNode): JsonNode = pass { engine.put("/marketplace/rules", body) }

    fun sets(): JsonNode = pass { engine.get("/marketplace/sets") }

    /** The rules as values; a rule the engine does not send keeps its default. */
    fun ruleValues(): MarketplaceRules {
        val r = rules()
        val d = MarketplaceRules()
        fun flag(key: String, default: Boolean) = r.path(key).takeIf { it.isBoolean }?.asBoolean() ?: default
        return MarketplaceRules(
            agentsMayAsk = flag("agents_may_ask", d.agentsMayAsk),
            allowUnverified = flag("allow_unverified", d.allowUnverified),
            checkDaily = flag("check_daily", d.checkDaily),
            restartWhenIdle = flag("restart_when_idle", d.restartWhenIdle),
        )
    }

    private fun body(name: String, version: String?) = mapOf("name" to checkName(name)) + versionBody(version)

    private fun versionBody(version: String?): Map<String, Any?> {
        val v = version?.trim()?.ifBlank { null } ?: return emptyMap()
        if (!PluginHost.VERSION.matches(v)) throw BadRequest("$v is not a version", "A version looks like 1.4.0.")
        return mapOf("version" to v)
    }

    /**
     * The engine's refusal as the api's answer: the same status, error and hint, and the rest of its answer next to them.
     */
    private fun pass(call: () -> JsonNode): JsonNode = try {
        call()
    } catch (e: EngineError) {
        val extra = e.body?.takeIf { it.isObject }?.let { b ->
            @Suppress("UNCHECKED_CAST")
            (mapper.convertValue(b, Map::class.java) as Map<String, Any?>) - setOf("error", "hint")
        }
        throw ApiException(e.status, e.message, e.hint, extra?.takeIf { it.isNotEmpty() })
    }

    companion object {
        /** A plugin's name (keel's plugin name rule); anything else never reaches the engine. */
        fun checkName(name: String): String {
            val n = name.trim()
            if (!PluginHost.NAME.matches(n)) throw BadRequest("$n is not a plugin name", "A plugin's name is a-z, 0-9 and '-', 32 at most.")
            return n
        }
    }
}
