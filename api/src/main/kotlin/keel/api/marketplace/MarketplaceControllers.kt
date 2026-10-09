package keel.api.marketplace

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ArrayNode
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.engine.EngineDown
import keel.api.pluginhost.PluginHost
import org.springframework.http.ResponseEntity
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

data class InstallBody(val name: String = "", val version: String? = null)
data class VersionBody(val version: String? = null)
data class SwitchBody(val on: Boolean? = null)
data class RestartBody(val now: Boolean? = null)
data class InstallFileBody(val path: String = "")

/** GET /api/marketplace?q=&category=, one plugin, refresh: the catalogs, as the engine reads them. */
@RestController
@RequestMapping("/api/marketplace")
class MarketplaceController(private val marketplace: Marketplace) {
    @GetMapping
    fun search(@RequestParam(required = false) q: String?, @RequestParam(required = false) category: String?): JsonNode =
        marketplace.search(q, category)

    @GetMapping("/{name}")
    fun plugin(@PathVariable name: String): JsonNode = marketplace.plugin(name)

    /** source: read only that catalog. */
    @PostMapping("/refresh")
    fun refresh(@RequestParam(required = false) source: String?): JsonNode = marketplace.refresh(source)
}

/**
 * The installed plugins and everything a person does with them (docs/plugins/13-step4-contract.md §6). GET
 * /api/plugins/installed is the engine's list plus `restart` {pending, scheduled, supervised, running} and the plugin
 * host's problems. GET /api/plugins itself stays what keel 0.15.4 answers: the per-project plugins' catalog
 * (keel.api.plugins).
 */
@RestController
@RequestMapping("/api/plugins")
class InstalledPluginsController(
    private val overview: PluginsOverview,
    private val marketplace: Marketplace,
    private val changes: PluginChanges,
    private val restarts: RestartPlan,
    private val requests: PluginRequests,
) {
    @GetMapping("/installed")
    fun installed(): JsonNode = overview.get()

    @PostMapping("/install")
    fun install(@RequestBody body: InstallBody): JsonNode = changes.install(body.name, body.version)

    @PostMapping("/install-file")
    fun installFile(@RequestBody body: InstallFileBody): JsonNode = changes.installFile(body.path)

    /** A new version that asks for more permissions is not installed: a request with the difference waits in the Inbox. */
    @PostMapping("/{name}/update")
    fun update(@PathVariable name: String, @RequestBody(required = false) body: VersionBody?): JsonNode = try {
        changes.update(name, body?.version)
    } catch (e: ApiException) {
        throw requests.forMorePermissions(name, e) ?: e
    }

    @PostMapping("/{name}/rollback")
    fun rollback(@PathVariable name: String): JsonNode = changes.rollback(name)

    @PutMapping("/{name}")
    fun switch(@PathVariable name: String, @RequestBody body: SwitchBody): JsonNode =
        changes.switch(name, body.on ?: throw BadRequest("Say on: true or on: false"))

    @DeleteMapping("/{name}")
    fun remove(@PathVariable name: String, @RequestParam(required = false, defaultValue = "keep") data: String): JsonNode =
        changes.remove(name, data)

    /** now: at once; else when no agent step runs. 202: keel restarts (now or later). */
    @PostMapping("/restart")
    fun restart(@RequestBody(required = false) body: RestartBody?): ResponseEntity<RestartAnswer> =
        ResponseEntity.accepted().body(if (body?.now == true) restarts.now() else restarts.whenIdle())

    @PostMapping("/requests")
    fun request(@RequestBody body: PluginRequestBody): PluginRequestAnswer = requests.ask(body)

    @GetMapping("/sources")
    fun sources(): JsonNode = marketplace.sources()

    @PutMapping("/sources")
    fun saveSources(@RequestBody body: JsonNode): JsonNode = marketplace.saveSources(body)

    @GetMapping("/rules")
    fun rules(): JsonNode = marketplace.rules()

    @PutMapping("/rules")
    fun saveRules(@RequestBody body: JsonNode): JsonNode = marketplace.saveRules(body)

    @GetMapping("/sets")
    fun sets(): JsonNode = marketplace.sets()
}

/**
 * GET /api/plugins: the engine's list of installed plugins (and the image's), with `restart` and the problems of this
 * run's plugin host (run/resolved.json). The engine's answer stays as it is; a bare list becomes {plugins: [...]}, and
 * an `installed` list is also given as `plugins`. With the engine down, the plugins this run loaded still show.
 */
@Service
class PluginsOverview(
    private val marketplace: Marketplace,
    private val host: PluginHost,
    private val restarts: RestartPlan,
    private val mapper: ObjectMapper,
) {
    fun get(): ObjectNode {
        val out: ObjectNode = try {
            when (val res = marketplace.installed()) {
                is ObjectNode -> res.deepCopy()
                is ArrayNode -> mapper.createObjectNode().set("plugins", res)
                else -> mapper.createObjectNode()
            }
        } catch (e: EngineDown) {
            loadedOnly()
        }
        if (!out.path("plugins").isArray) out.set<JsonNode>("plugins", out.path("installed").takeIf { it.isArray } ?: mapper.createArrayNode())
        out.set<JsonNode>("restart", mapper.valueToTree(restarts.state(pending(out.path("pending_restart")))))
        out.set<JsonNode>("problems", problems(out.path("problems")))
        if (!out.path("mode").isTextual) out.put("mode", host.mode)
        return out
    }

    /** The engine says a restart is due: {pending, changes} (or true, or a list of what differs from this run). */
    private fun pending(p: JsonNode): Boolean = when {
        p.isBoolean -> p.asBoolean()
        p.isObject && p.path("pending").isBoolean -> p.path("pending").asBoolean()
        p.isArray || p.isObject -> p.size() > 0
        else -> false
    }

    /** The engine's problems, then this run's plugin host's (a plugin left out at start), each once. */
    private fun problems(engine: JsonNode): ArrayNode {
        val all = mapper.createArrayNode()
        if (engine.isArray) all.addAll(engine as ArrayNode)
        val seen = all.map { it.path("name").asText() + "|" + it.path("error").asText() }.toMutableSet()
        for (p in host.problems) if (seen.add(p.name + "|" + p.error)) all.add(mapper.valueToTree<JsonNode>(p))
        return all
    }

    private fun loadedOnly(): ObjectNode {
        val out = mapper.createObjectNode()
        val list = out.putArray("plugins")
        for (p in host.plugins) {
            list.addObject().put("name", p.name).put("title", p.title).put("version", p.version).put("from", p.source)
                .put("on", true).put("status", "loaded").also { o -> o.putArray("parts").let { a -> p.parts().forEach(a::add) } }
        }
        out.put("engine", false)
        return out
    }
}
