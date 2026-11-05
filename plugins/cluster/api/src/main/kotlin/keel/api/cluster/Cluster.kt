package keel.api.cluster

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Forbidden
import keel.api.common.KvStore
import keel.api.engine.EngineClient
import keel.api.flow.FlowContributor
import keel.api.plugins.PluginService
import keel.api.projects.ProjectService
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

/** What keel may do on one kubeconfig context: seen in keel (`on`), and `read` | `ask` | `free` for a change. */
data class ClusterRule(val on: Boolean = true, val access: String = "read")
data class ClusterSettings(val clusters: Map<String, ClusterRule> = emptyMap())

/** A change of the settings: only the contexts named, and only the fields given. */
data class RuleBody(val on: Boolean? = null, val access: String? = null)
data class SettingsBody(val clusters: Map<String, RuleBody?>? = null)
data class ContextBody(val context: String? = null)
data class ExecBody(val context: String? = null, val ns: String? = null, val name: String? = null, val container: String? = null, val command: String? = null)
data class ChangeBody(
    val context: String? = null,
    val action: String? = null,
    val kind: String? = null,
    val ns: String? = null,
    val name: String? = null,
    val replicas: Int? = null,
)

/**
 * The Cluster plugin's api part: the rule for each kubeconfig context, kept here (KvStore), and every call to the engine part
 * (keel_plugin_cluster), which runs kubectl. A context with no rule is on and read only. Reading needs only that it is on; an exec or a
 * change needs `ask` or `free` (the person confirms in the page; an agent asks in the Inbox, which the engine part does).
 *
 *     GET  /api/plugins/cluster/contexts    the kubeconfig's contexts, each with its on and access
 *     PUT  /api/plugins/cluster/settings    {clusters: {name: {on, access}}}
 *     POST /api/plugins/cluster/test        {context}
 *     GET  /api/plugins/cluster/snapshot    ?context=&ns=
 *     GET  /api/plugins/cluster/logs        ?context=&ns=&name=&container=&previous=&tail=
 *     GET  /api/plugins/cluster/describe    ?context=&kind=&ns=&name=
 *     POST /api/plugins/cluster/exec        {context, ns, name, container, command}
 *     POST /api/plugins/cluster/change      {context, action, kind, ns, name, replicas}
 *     GET  /api/plugins/cluster/runs        ?pid=   what runs where, against the project's git
 *     GET  /api/plugins/cluster/history     every exec and change, newest first
 */
@Service
class ClusterService(
    private val engine: EngineClient,
    private val kv: KvStore,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
) {
    fun settings(): ClusterSettings = kv.get<ClusterSettings>(KEY) ?: ClusterSettings()

    fun rule(context: String): ClusterRule = settings().clusters[context] ?: ClusterRule()

    private fun engineContexts(): JsonNode = engine.post("$PATH/contexts", emptyMap<String, Any>(), long = true)

    /** The engine's view of the kubeconfig, each context with its rule. */
    fun contexts(): JsonNode {
        val out = engineContexts().deepCopy<JsonNode>()
        val rules = settings().clusters
        (out as? ObjectNode)?.path("contexts")?.forEach { c ->
            val r = rules[c.path("name").asText()] ?: ClusterRule()
            (c as ObjectNode).put("on", r.on).put("access", r.access)
        }
        return out
    }

    fun save(body: SettingsBody): JsonNode {
        val asked = body.clusters ?: throw BadRequest("Say which contexts to change", "{\"clusters\": {\"<context>\": {\"on\": true, \"access\": \"read\"}}}")
        asked.values.forEach { r ->
            val a = r?.access
            if (a != null && a !in ACCESS) throw BadRequest("access is read, ask or free, not \"$a\"")
        }
        asked.keys.forEach { name(it) }
        val local = runCatching {
            engineContexts().path("contexts").filter { it.path("local").asBoolean(false) }.map { it.path("name").asText() }.toSet()
        }.getOrDefault(emptySet())
        val rules = settings().clusters.toMutableMap()
        asked.forEach { (ctx, r) ->
            val now = rules[ctx] ?: ClusterRule()
            var access = r?.access ?: now.access
            // "change without asking" only for a cluster on this computer
            if (access == "free" && ctx !in local) access = "ask"
            rules[ctx] = ClusterRule(r?.on ?: now.on, access)
        }
        kv.put(KEY, ClusterSettings(rules))
        return contexts()
    }

    private fun name(context: String?): String {
        val c = context?.trim().orEmpty()
        if (c.isEmpty()) throw BadRequest("Say which cluster (its kubeconfig context)")
        if (c.length > 253 || c.any { it.isISOControl() }) throw BadRequest("That is not a kubeconfig context name")
        return c
    }

    /** A context that is on, or a 403. */
    private fun seen(context: String?): String {
        val c = name(context)
        if (!rule(c).on) throw Forbidden("$c is turned off in Cluster › Connect", "Turn it on there to see it in keel.")
        return c
    }

    /** A context that may change: on, and not read only. */
    private fun changing(context: String?): Pair<String, String> {
        val c = seen(context)
        val access = rule(c).access
        if (access == "read") throw Forbidden("$c is read only in keel", "Change it in Cluster › Connect.")
        return c to access
    }

    /** A test works on a context that is off too: Connect tests every context before the person turns one on. */
    fun test(body: ContextBody): JsonNode = engine.post("$PATH/test", mapOf("context" to name(body.context)), long = true)

    fun snapshot(context: String?, ns: String?): JsonNode =
        engine.post("$PATH/snapshot", mapOf("context" to seen(context), "ns" to ns.orEmpty()), long = true)

    fun logs(context: String?, ns: String?, name: String?, container: String?, previous: Boolean?, tail: Int?): JsonNode =
        engine.post("$PATH/logs", mapOf("context" to seen(context), "ns" to ns.orEmpty(), "name" to name.orEmpty(),
            "container" to container.orEmpty(), "previous" to (previous ?: false), "tail" to (tail ?: 300)), long = true)

    fun describe(context: String?, kind: String?, ns: String?, name: String?): JsonNode =
        engine.post("$PATH/describe", mapOf("context" to seen(context), "kind" to kind.orEmpty(), "ns" to ns.orEmpty(),
            "name" to name.orEmpty()), long = true)

    fun exec(body: ExecBody): JsonNode {
        val (c, access) = changing(body.context)
        if (body.command.isNullOrBlank()) throw BadRequest("Say which command to run")
        return engine.post("$PATH/exec", mapOf("context" to c, "ns" to body.ns.orEmpty(), "name" to body.name.orEmpty(),
            "container" to body.container.orEmpty(), "command" to body.command, "access" to access, "who" to "person"), long = true)
    }

    fun change(body: ChangeBody): JsonNode {
        val (c, access) = changing(body.context)
        if (body.action.isNullOrBlank()) throw BadRequest("Say what to do: delete, restart or scale")
        return engine.post("$PATH/change", mapOf("context" to c, "action" to body.action, "kind" to body.kind.orEmpty(),
            "ns" to body.ns.orEmpty(), "name" to body.name.orEmpty(), "replicas" to body.replicas, "access" to access, "who" to "person"),
            long = true)
    }

    /** The contexts that are on: the kubeconfig's, by their rules (only the rules when the engine cannot say). */
    fun onContexts(): List<String> {
        val rules = settings().clusters
        val known = runCatching { engineContexts().path("contexts").map { it.path("name").asText() } }.getOrNull()
            ?: return rules.filterValues { it.on }.keys.sorted()
        return known.filter { (rules[it] ?: ClusterRule()).on }
    }

    fun runs(pid: String?): JsonNode {
        if (pid.isNullOrBlank()) throw BadRequest("Say which project (pid)")
        val root = projects.root(pid).toString()
        return engine.post("$PATH/runs", mapOf("root" to root, "contexts" to onContexts()), long = true)
    }

    fun history(): JsonNode = engine.post("$PATH/history", emptyMap<String, Any>(), long = true)

    /** What an agent call carries: `{context: access}` of the contexts that are on (a context not in it is off for agents). */
    fun accessJson(): String {
        val rules = settings().clusters
        return mapper.writeValueAsString(onContexts().associateWith { (rules[it] ?: ClusterRule()).access })
    }

    companion object {
        const val KEY = "plugin.cluster.settings"
        const val PATH = "/plugins/cluster"
        val ACCESS = setOf("read", "ask", "free")
    }
}

/** The rules reach the engine only with an agent call of a project that has the plugin on (Control › Plugins). */
@Component
class ClusterKeys(private val cluster: ClusterService, private val plugins: PluginService) : FlowContributor {
    override fun keys(pid: String): Map<String, String> =
        if (plugins.on(pid, "cluster")) {
            try {
                mapOf("cluster:access" to cluster.accessJson())
            } catch (e: ApiException) {
                emptyMap()
            }
        } else emptyMap()
}

@RestController
@RequestMapping("/api/plugins/cluster")
class ClusterController(private val cluster: ClusterService) {
    @GetMapping("/contexts")
    fun contexts(): JsonNode = cluster.contexts()

    @PutMapping("/settings")
    fun save(@RequestBody body: SettingsBody): JsonNode = cluster.save(body)

    @PostMapping("/test")
    fun test(@RequestBody body: ContextBody): JsonNode = cluster.test(body)

    @GetMapping("/snapshot")
    fun snapshot(@RequestParam(required = false) context: String?, @RequestParam(required = false) ns: String?): JsonNode =
        cluster.snapshot(context, ns)

    @GetMapping("/logs")
    fun logs(
        @RequestParam(required = false) context: String?,
        @RequestParam(required = false) ns: String?,
        @RequestParam(required = false) name: String?,
        @RequestParam(required = false) container: String?,
        @RequestParam(required = false) previous: Boolean?,
        @RequestParam(required = false) tail: Int?,
    ): JsonNode = cluster.logs(context, ns, name, container, previous, tail)

    @GetMapping("/describe")
    fun describe(
        @RequestParam(required = false) context: String?,
        @RequestParam(required = false) kind: String?,
        @RequestParam(required = false) ns: String?,
        @RequestParam(required = false) name: String?,
    ): JsonNode = cluster.describe(context, kind, ns, name)

    @PostMapping("/exec")
    fun exec(@RequestBody body: ExecBody): JsonNode = cluster.exec(body)

    @PostMapping("/change")
    fun change(@RequestBody body: ChangeBody): JsonNode = cluster.change(body)

    @GetMapping("/runs")
    fun runs(@RequestParam(required = false) pid: String?): JsonNode = cluster.runs(pid)

    @GetMapping("/history")
    fun history(): JsonNode = cluster.history()
}
