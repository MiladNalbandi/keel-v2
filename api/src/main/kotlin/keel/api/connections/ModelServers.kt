package keel.api.connections

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Hosts
import keel.api.common.Json
import keel.api.common.KvStore
import keel.api.common.NotFound
import keel.api.engine.EngineClient
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** One local model server as keel keeps it (kv `local_models`); its key is the secret `local.<name>`. */
data class LocalEndpoint(
    val name: String = "",
    val kind: String = "ollama",
    val url: String = "",
    /** The context window keel packs prompts for; null: what the server says. */
    val context: Int? = null,
    /** USD per million tokens; null: $0. */
    val priceIn: Double? = null,
    val priceOut: Double? = null,
)

data class LocalModelsConfig(val enabled: Boolean = false, val endpoints: List<LocalEndpoint> = emptyList())

/** A server for the web: never its key, only whether it has one and its last characters. */
data class LocalEndpointView(
    val name: String, val kind: String, val url: String, val context: Int?, val priceIn: Double?, val priceOut: Double?,
    val keySet: Boolean, val keyHint: String?,
)

/** [defaults]: the URL each kind usually has from where keel runs (Docker: host.docker.internal, native: 127.0.0.1). */
data class LocalModelsView(val enabled: Boolean, val endpoints: List<LocalEndpointView>, val kinds: List<String>, val defaults: Map<String, String>)

/** PUT /api/providers/local/endpoints/{name}. key: a new key (blank or null keeps the saved one); clearKey removes it. */
data class LocalEndpointBody(
    val kind: String = "ollama",
    val url: String = "",
    val key: String? = null,
    val clearKey: Boolean = false,
    val context: Int? = null,
    val priceIn: Double? = null,
    val priceOut: Double? = null,
)

data class LocalSwitch(val enabled: Boolean = false)

data class VertexConfig(val project: String = "", val region: String = "")

/** Vertex AI for the web: the project and region, and how keel signs in (a key's service account, never the key). */
data class VertexView(val project: String, val region: String, val credentials: String, val keySet: Boolean, val clientEmail: String?, val ready: Boolean)

/** PUT /api/providers/vertex. credentialsJson: a service-account key file (blank keeps the saved one); clearCredentials: use ADC. */
data class VertexBody(val project: String = "", val region: String = "", val credentialsJson: String? = null, val clearCredentials: Boolean = false)

/**
 * keel 0.18.46 the two providers whose connection is more than one key: local model servers (`local`: Ollama, LM Studio,
 * vLLM, llama.cpp, any OpenAI-compatible URL) and Google Vertex AI (`vertex`). Their settings live in kv, their keys in
 * the secret store. The engine gets each as the provider's key, a JSON ([engineConfig], via SecretService.keyForProvider),
 * so every place that sends provider keys (flows, KeelBot, the review check, tests) sends these too.
 */
@Service
class ModelServerService(
    private val kv: KvStore,
    private val secrets: SecretService,
    private val engine: EngineClient,
    private val mapper: ObjectMapper,
) {
    private val native: Boolean get() = System.getenv("KEEL_NATIVE") == "1"

    fun local(): LocalModelsConfig = kv.get<LocalModelsConfig>(LOCAL_KEY) ?: LocalModelsConfig()

    fun defaults(): Map<String, String> {
        val host = if (native) "127.0.0.1" else "host.docker.internal"
        return mapOf("ollama" to "http://$host:11434", "lmstudio" to "http://$host:1234/v1", "vllm" to "http://$host:8000/v1",
            "llamacpp" to "http://$host:8080/v1", "openai-compatible" to "")
    }

    fun localView(): LocalModelsView {
        val c = local()
        return LocalModelsView(c.enabled, c.endpoints.map { e ->
            val secret = secretOf(e.name)
            LocalEndpointView(e.name, e.kind, e.url, e.context, e.priceIn, e.priceOut, secrets.has(secret), secrets.hintOf(secret))
        }, KINDS, defaults())
    }

    fun setLocalEnabled(on: Boolean): LocalModelsView {
        kv.put(LOCAL_KEY, local().copy(enabled = on))
        return localView()
    }

    fun putEndpoint(name: String, body: LocalEndpointBody): LocalModelsView {
        checkName(name)
        if (body.kind !in KINDS) throw BadRequest("kind cannot be \"${body.kind}\"", "Pick one of: ${KINDS.joinToString()}")
        val url = body.url.trim().trimEnd('/')
        if (!URL.matches(url)) throw BadRequest("The URL must start with http:// or https://", "For example ${defaults()["ollama"]}")
        // 0.18.64 a key never goes over plain http to a server outside this machine and the local network
        if (!Hosts.tokenSafe(url, lan = true)) throw BadRequest("Use https:// for a model server outside your network",
            "Plain http:// is only for this machine, host.docker.internal, a *.local name or a private address (192.168.x.x, 10.x.x.x).")
        body.context?.let { if (it !in 256..10_000_000) throw BadRequest("context must be from 256 to 10,000,000 tokens", "Leave it empty to use what the server says.") }
        listOfNotNull(body.priceIn, body.priceOut).forEach { if (it < 0 || it > 10_000) throw BadRequest("A price must be from 0 to 10,000 USD per million tokens") }
        val c = local()
        val e = LocalEndpoint(name, body.kind, url, body.context, body.priceIn, body.priceOut)
        val list = c.endpoints.filterNot { it.name == name }
        if (list.size >= MAX_ENDPOINTS && c.endpoints.none { it.name == name }) throw BadRequest("keel takes up to $MAX_ENDPOINTS local model servers")
        // a new server keeps its place; the first one ever turns local models on
        val next = c.endpoints.map { if (it.name == name) e else it }.let { if (c.endpoints.any { x -> x.name == name }) it else it + e }
        kv.put(LOCAL_KEY, c.copy(endpoints = next, enabled = c.enabled || c.endpoints.isEmpty()))
        // 0.18.64 the saved key belongs to the saved server: a new host drops it (type it again if the new one needs it)
        val moved = c.endpoints.firstOrNull { it.name == name }?.let { !Hosts.sameSite(it.url, url) } ?: false
        when {
            !body.key.isNullOrBlank() -> secrets.put(secretOf(name), body.key.trim())
            body.clearKey || moved -> if (secrets.has(secretOf(name))) secrets.delete(secretOf(name))
        }
        return localView()
    }

    fun deleteEndpoint(name: String): LocalModelsView {
        checkName(name)
        val c = local()
        if (c.endpoints.none { it.name == name }) throw NotFound("No local model server called $name")
        kv.put(LOCAL_KEY, c.copy(endpoints = c.endpoints.filterNot { it.name == name }))
        if (secrets.has(secretOf(name))) secrets.delete(secretOf(name))
        return localView()
    }

    /** Asks the engine whether it reaches the server and which models it has: {ok, models?, error?, hint?, ms}. */
    fun testEndpoint(name: String): JsonNode {
        val e = local().endpoints.firstOrNull { it.name == name } ?: throw NotFound("No local model server called $name")
        return engine.post("/providers/local/test", endpointForEngine(e), long = true)
    }

    private fun endpointForEngine(e: LocalEndpoint): Map<String, Any?> = linkedMapOf<String, Any?>(
        "name" to e.name, "kind" to e.kind, "url" to e.url, "key" to secrets.get(secretOf(e.name)), "context" to e.context,
        "price_in" to e.priceIn, "price_out" to e.priceOut,
    ).filterValues { it != null }

    // ---- Vertex AI

    fun vertex(): VertexConfig = kv.get<VertexConfig>(VERTEX_KEY) ?: VertexConfig()

    fun vertexView(): VertexView {
        val c = vertex()
        val email = secrets.get(VERTEX_SECRET)?.let { runCatching { mapper.readTree(it).path("client_email").asText("") }.getOrNull() }
        val keySet = secrets.has(VERTEX_SECRET)
        return VertexView(c.project, c.region, if (keySet) "service-account" else "adc", keySet, email?.takeIf { it.isNotBlank() },
            c.project.isNotBlank() && c.region.isNotBlank())
    }

    fun putVertex(body: VertexBody): VertexView {
        val project = body.project.trim()
        val region = body.region.trim().lowercase()
        if (!PROJECT.matches(project)) throw BadRequest("\"$project\" is not a Google Cloud project id",
            "The project id has 6 to 30 lower-case letters, digits and dashes (not the project's name or number).")
        if (!REGION.matches(region)) throw BadRequest("\"$region\" is not a Vertex AI region", "For example global, us-east5 or europe-west1.")
        val json = body.credentialsJson?.trim()?.takeIf { it.isNotEmpty() }
        if (json != null) {
            val node = runCatching { mapper.readTree(json) }.getOrNull()
            if (node == null || !node.isObject) throw BadRequest("The service-account key is not JSON", "Paste the whole key file: it starts with { and ends with }.")
            if (node.path("type").asText() != "service_account" || node.path("client_email").asText().isBlank() || node.path("private_key").asText().isBlank()) {
                throw BadRequest("This JSON is not a service-account key", "In Google Cloud: IAM › Service accounts › Keys › Add key › JSON. The account needs the Vertex AI User role.")
            }
        }
        kv.put(VERTEX_KEY, VertexConfig(project, region))
        when {
            body.clearCredentials -> if (secrets.has(VERTEX_SECRET)) secrets.delete(VERTEX_SECRET)
            json != null -> secrets.put(VERTEX_SECRET, json)
        }
        return vertexView()
    }

    // ---- what the engine gets

    /** The provider's connection as the engine reads it (its "key"), or null when it is not set up. Never for the web. */
    fun engineConfig(provider: String): String? = when (provider) {
        "local" -> local().takeIf { it.enabled && it.endpoints.isNotEmpty() }?.let { c ->
            Json.write(mapOf("enabled" to true, "endpoints" to c.endpoints.map(::endpointForEngine)))
        }
        "vertex" -> vertex().takeIf { it.project.isNotBlank() && it.region.isNotBlank() }?.let { c ->
            val creds = secrets.get(VERTEX_SECRET)?.let { runCatching { mapper.readTree(it) }.getOrNull() }
            Json.write(linkedMapOf<String, Any?>("project" to c.project, "region" to c.region, "credentials" to creds).filterValues { it != null })
        }
        else -> null
    }

    /** What Connections shows for the provider's api mode: (ready, one line). */
    fun summary(provider: String): Pair<Boolean, String> = when (provider) {
        "local" -> {
            val c = local()
            when {
                c.endpoints.isEmpty() -> false to "Add a model server (Ollama, LM Studio, vLLM, llama.cpp)."
                !c.enabled -> false to "Local models are off."
                else -> true to c.endpoints.joinToString { "${it.name} (${it.kind})" }
            }
        }
        "vertex" -> {
            val v = vertexView()
            if (!v.ready) false to "Set the project and region."
            else true to "${v.project} · ${v.region} · ${v.clientEmail ?: "Application Default Credentials"}"
        }
        else -> false to ""
    }

    /** The connections the model catalog needs (GET /api/providers/models): only the providers that are set up. */
    fun catalogKeys(): Map<String, String> = PROVIDERS.mapNotNull { p -> engineConfig(p)?.let { p to it } }.toMap()

    private fun checkName(name: String) {
        if (!NAME.matches(name)) throw BadRequest("\"$name\" cannot name a model server", "Lower-case letters, digits and dashes, for example ollama or lab-gpu.")
    }

    companion object {
        const val LOCAL_KEY = "local_models"
        const val VERTEX_KEY = "vertex"
        const val VERTEX_SECRET = "VERTEX_CREDENTIALS"
        const val MAX_ENDPOINTS = 20
        /** The providers whose connection this service keeps. */
        val PROVIDERS = listOf("local", "vertex")
        val KINDS = listOf("ollama", "lmstudio", "vllm", "llamacpp", "openai-compatible")
        val NAME = Regex("^[a-z0-9][a-z0-9-]{0,31}$")
        val URL = Regex("^https?://\\S{1,490}$")
        val PROJECT = Regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
        val REGION = Regex("^[a-z][a-z0-9-]{1,39}$")
        fun secretOf(name: String) = "local.$name"
    }
}

@RestController
@RequestMapping("/api/providers")
class ModelServerController(private val servers: ModelServerService) {
    @GetMapping("/local")
    fun local(): LocalModelsView = servers.localView()

    @PutMapping("/local")
    fun switch(@RequestBody body: LocalSwitch): LocalModelsView = servers.setLocalEnabled(body.enabled)

    @PutMapping("/local/endpoints/{name}")
    fun put(@PathVariable name: String, @RequestBody body: LocalEndpointBody): LocalModelsView = servers.putEndpoint(name, body)

    @DeleteMapping("/local/endpoints/{name}")
    fun delete(@PathVariable name: String): LocalModelsView = servers.deleteEndpoint(name)

    @PostMapping("/local/endpoints/{name}/test")
    fun test(@PathVariable name: String): JsonNode = servers.testEndpoint(name)

    @GetMapping("/vertex")
    fun vertex(): VertexView = servers.vertexView()

    @PutMapping("/vertex")
    fun putVertex(@RequestBody body: VertexBody): VertexView = servers.putVertex(body)
}
