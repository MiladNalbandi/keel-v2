package keel.api.cluster

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Forbidden
import keel.api.common.KvStore
import keel.api.connections.ModelServerService
import keel.api.connections.SecretService
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

/** google: how GKE contexts sign in, `vertex` (the service account of Connections › Vertex AI), `own` (a key for clusters only) or
 *  `off` (the kubeconfig's own sign-in, gcloud's gke-gcloud-auth-plugin). */
data class ClusterSettings(val clusters: Map<String, ClusterRule> = emptyMap(), val google: String = "vertex")

/** PUT /api/plugins/cluster/google. credentialsJson: a service-account key for clusters only (blank keeps the saved one). */
data class GoogleBody(val source: String? = null, val credentialsJson: String? = null, val clearCredentials: Boolean = false)

/** The GKE sign-in for the web: the source and the accounts' emails, never a key. */
data class GoogleView(val source: String, val email: String?, val ownKeySet: Boolean, val ownEmail: String?, val vertexEmail: String?)

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
 *     GET  /api/plugins/cluster/google      how GKE contexts sign in (GoogleView)
 *     PUT  /api/plugins/cluster/google      {source: vertex|own|off, credentialsJson?, clearCredentials?}
 *     POST /api/plugins/cluster/test        {context}
 *     GET  /api/plugins/cluster/snapshot    ?context=&ns=
 *     GET  /api/plugins/cluster/logs        ?context=&ns=&name=&container=&previous=&tail=
 *     GET  /api/plugins/cluster/describe    ?context=&kind=&ns=&name=
 *     POST /api/plugins/cluster/exec        {context, ns, name, container, command}
 *     POST /api/plugins/cluster/change      {context, action, kind, ns, name, replicas}
 *     GET  /api/plugins/cluster/runs        ?pid=   what runs where, against the project's git
 *     GET  /api/plugins/cluster/history     every exec and change, newest first
 *
 * Each call that runs kubectl on a context carries the Google service-account key GKE contexts sign in with (`google`, none when it
 * is off or no key is saved); the engine part makes a short-lived token from it and keeps nothing.
 */
@Service
class ClusterService(
    private val engine: EngineClient,
    private val kv: KvStore,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
    private val secrets: SecretService,
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
        (out as? ObjectNode)?.set<JsonNode>("google", mapper.valueToTree(googleView()))
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
        kv.put(KEY, settings().copy(clusters = rules))
        return contexts()
    }

    private fun emailOf(json: String?): String? =
        json?.let { runCatching { mapper.readTree(it).path("client_email").asText("") }.getOrNull() }?.takeIf { it.isNotBlank() }

    /** The key GKE contexts sign in with, or null: the kubeconfig's own sign-in. */
    fun googleKey(): String? = when (settings().google) {
        "vertex" -> secrets.get(ModelServerService.VERTEX_SECRET)
        "own" -> secrets.get(GOOGLE_SECRET)
        else -> null
    }

    fun googleView(): GoogleView {
        val own = emailOf(secrets.get(GOOGLE_SECRET))
        return GoogleView(settings().google, emailOf(googleKey()), secrets.has(GOOGLE_SECRET), own,
            emailOf(secrets.get(ModelServerService.VERTEX_SECRET)))
    }

    fun putGoogle(body: GoogleBody): GoogleView {
        val source = body.source?.trim()?.takeIf { it.isNotEmpty() } ?: settings().google
        if (source !in GOOGLE) throw BadRequest("source is vertex, own or off, not \"$source\"")
        val json = body.credentialsJson?.trim()?.takeIf { it.isNotEmpty() }
        if (json != null) {
            val n = runCatching { mapper.readTree(json) }.getOrNull()
            if (n == null || n.path("type").asText() != "service_account" || n.path("client_email").asText().isBlank() ||
                n.path("private_key").asText().isBlank()) {
                throw BadRequest("This JSON is not a service-account key",
                    "In Google Cloud: IAM › Service accounts › Keys › Add key › JSON. The account needs a GKE role, for example Kubernetes Engine Viewer.")
            }
            secrets.put(GOOGLE_SECRET, mapper.writeValueAsString(n))
        } else if (body.clearCredentials && secrets.has(GOOGLE_SECRET)) {
            secrets.delete(GOOGLE_SECRET)
        }
        if (source == "own" && !secrets.has(GOOGLE_SECRET)) throw BadRequest("Paste the service account's JSON key first")
        kv.put(KEY, settings().copy(google = source))
        return googleView()
    }

    /** One engine call that may run kubectl on a context: with the Google key, when GKE sign-in has one. */
    private fun kubectl(op: String, body: Map<String, Any?>): JsonNode =
        engine.post("$PATH/$op", body + ("google" to googleKey()), long = true)

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
    fun test(body: ContextBody): JsonNode = kubectl("test", mapOf("context" to name(body.context)))

    fun snapshot(context: String?, ns: String?): JsonNode =
        kubectl("snapshot", mapOf("context" to seen(context), "ns" to ns.orEmpty()))

    fun logs(context: String?, ns: String?, name: String?, container: String?, previous: Boolean?, tail: Int?): JsonNode =
        kubectl("logs", mapOf("context" to seen(context), "ns" to ns.orEmpty(), "name" to name.orEmpty(),
            "container" to container.orEmpty(), "previous" to (previous ?: false), "tail" to (tail ?: 300)))

    fun describe(context: String?, kind: String?, ns: String?, name: String?): JsonNode =
        kubectl("describe", mapOf("context" to seen(context), "kind" to kind.orEmpty(), "ns" to ns.orEmpty(),
            "name" to name.orEmpty()))

    fun exec(body: ExecBody): JsonNode {
        val (c, access) = changing(body.context)
        if (body.command.isNullOrBlank()) throw BadRequest("Say which command to run")
        return kubectl("exec", mapOf("context" to c, "ns" to body.ns.orEmpty(), "name" to body.name.orEmpty(),
            "container" to body.container.orEmpty(), "command" to body.command, "access" to access, "who" to "person"))
    }

    fun change(body: ChangeBody): JsonNode {
        val (c, access) = changing(body.context)
        if (body.action.isNullOrBlank()) throw BadRequest("Say what to do: delete, restart or scale")
        return kubectl("change", mapOf("context" to c, "action" to body.action, "kind" to body.kind.orEmpty(),
            "ns" to body.ns.orEmpty(), "name" to body.name.orEmpty(), "replicas" to body.replicas, "access" to access, "who" to "person"))
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
        return kubectl("runs", mapOf("root" to root, "contexts" to onContexts()))
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
        const val GOOGLE_SECRET = "CLUSTER_GOOGLE_CREDENTIALS"
        val ACCESS = setOf("read", "ask", "free")
        val GOOGLE = setOf("vertex", "own", "off")
    }
}

/** The rules, and the Google key of GKE sign-in, reach the engine only with an agent call of a project that has the plugin on
 *  (Control › Plugins). */
@Component
class ClusterKeys(private val cluster: ClusterService, private val plugins: PluginService) : FlowContributor {
    override fun keys(pid: String): Map<String, String> =
        if (plugins.on(pid, "cluster")) {
            try {
                mapOf("cluster:access" to cluster.accessJson()) + (cluster.googleKey()?.let { mapOf("cluster:google" to it) } ?: emptyMap())
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

    @GetMapping("/google")
    fun google(): GoogleView = cluster.googleView()

    @PutMapping("/google")
    fun putGoogle(@RequestBody body: GoogleBody): GoogleView = cluster.putGoogle(body)

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
