package keel.api.engine

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.KeelProperties
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.client.JdkClientHttpRequestFactory
import org.springframework.stereotype.Component
import org.springframework.web.client.ResourceAccessException
import org.springframework.web.client.RestClient
import org.springframework.web.client.RestClientResponseException
import java.net.http.HttpClient
import java.time.Duration

class EngineDown : ApiException(
    HttpStatus.SERVICE_UNAVAILABLE,
    "The engine is not running",
    "Start it with `cd engine && uv run keel-engine`, or check KEEL_ENGINE_URL.",
)

/** Talks to the Python engine (FastAPI). Every call maps "cannot connect" to a 503 with a hint. */
@Component
class EngineClient(private val props: KeelProperties, private val mapper: ObjectMapper) {

    private fun client(readTimeout: Duration): RestClient {
        val http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(3)).build()
        val factory = JdkClientHttpRequestFactory(http).apply { setReadTimeout(readTimeout) }
        return RestClient.builder()
            .baseUrl(props.engineUrl.trimEnd('/'))
            .requestFactory(factory)
            .defaultHeader("X-Keel-Token", props.internalToken)
            .build()
    }

    private val fast by lazy { client(Duration.ofSeconds(30)) }
    private val slow by lazy { client(Duration.ofSeconds(180)) }

    fun get(path: String, long: Boolean = false): JsonNode = call {
        (if (long) slow else fast).get().uri(path).retrieve().body(JsonNode::class.java) ?: mapper.nullNode()
    }

    fun post(path: String, body: Any?, long: Boolean = false): JsonNode = call {
        (if (long) slow else fast).post().uri(path)
            .contentType(MediaType.APPLICATION_JSON)
            .body(mapper.valueToTree<JsonNode>(body ?: emptyMap<String, Any>()))
            .retrieve().body(JsonNode::class.java) ?: mapper.nullNode()
    }

    fun patch(path: String, body: Any?): JsonNode = call {
        fast.patch().uri(path)
            .contentType(MediaType.APPLICATION_JSON)
            .body(mapper.valueToTree<JsonNode>(body ?: emptyMap<String, Any>()))
            .retrieve().body(JsonNode::class.java) ?: mapper.nullNode()
    }

    fun delete(path: String): JsonNode = call {
        fast.delete().uri(path).retrieve().body(JsonNode::class.java) ?: mapper.nullNode()
    }

    /** True when GET /health answers within a short time. */
    fun health(): JsonNode? = try {
        client(Duration.ofSeconds(2)).get().uri("/health").retrieve().body(JsonNode::class.java)
    } catch (e: Exception) {
        null
    }

    private fun call(block: () -> JsonNode): JsonNode {
        try {
            return block()
        } catch (e: ResourceAccessException) {
            throw EngineDown()
        } catch (e: RestClientResponseException) {
            val body = runCatching { mapper.readTree(e.responseBodyAsString) }.getOrNull()
            val msg = body?.get("error")?.asText() ?: body?.get("detail")?.toString() ?: "The engine refused the request"
            val hint = body?.get("hint")?.asText()
            val status = HttpStatus.resolve(e.statusCode.value()) ?: HttpStatus.BAD_GATEWAY
            throw ApiException(if (status.is5xxServerError) HttpStatus.BAD_GATEWAY else status, msg, hint)
        }
    }

    // ---- typed helpers -------------------------------------------------------------------

    fun templates(): JsonNode = get("/templates")
    fun validate(yaml: String): JsonNode = post("/workflows/validate", mapOf("yaml" to yaml))
    fun estimate(body: Map<String, Any?>): JsonNode = post("/workflows/estimate", body)
    fun startThread(body: Any): JsonNode = post("/threads", body, long = true)
    fun thread(id: String): JsonNode = get("/threads/$id")
    fun resume(id: String, body: Map<String, Any?>): JsonNode = post("/threads/$id/resume", body, long = true)
    fun stop(id: String): JsonNode = post("/threads/$id/stop", null)
    fun setMode(id: String, mode: String): JsonNode = post("/threads/$id/mode", mapOf("mode" to mode))
    fun continueThread(id: String, keys: Map<String, String>?, root: String?): JsonNode =
        post("/threads/$id/continue", mapOf("keys" to keys, "root" to root).filterValues { it != null })
    fun history(id: String): JsonNode = get("/threads/$id/history")
    fun unlocks(id: String): JsonNode = get("/threads/$id/unlocks")
    fun addUnlock(id: String, body: Map<String, Any?>): JsonNode = post("/threads/$id/unlocks", body)
    fun rewind(id: String, checkpointId: String, keys: Map<String, String>? = null, root: String? = null): JsonNode =
        post("/threads/$id/rewind", mapOf("checkpoint_id" to checkpointId, "keys" to keys, "root" to root).filterValues { it != null }, long = true)
    fun mcpTools(spec: Any): JsonNode = post("/mcp/tools", spec, long = true)
    fun providerTest(body: Map<String, Any?>): JsonNode = post("/providers/test", body, long = true)
    fun models(): JsonNode = get("/providers/models")

    // ---- project scan: code graph index (the Map plugin's map: its own controller, with get/post) ---
    fun scan(pid: String, root: String, rebuild: Boolean = false): JsonNode = post("/projects/$pid/scan", mapOf("root" to root, "rebuild" to rebuild))
    fun index(pid: String): JsonNode = get("/projects/$pid/index")

    // ---- the code graph for people (engine runtime/codegraph_view.py) ----------------------
    fun graph(pid: String): JsonNode = get("/projects/$pid/graph")
    fun graphSearch(pid: String, q: String): JsonNode = post("/projects/$pid/graph/search", mapOf("q" to q))
    fun graphNode(pid: String, id: String, depth: Int): JsonNode = post("/projects/$pid/graph/node", mapOf("id" to id, "depth" to depth))

    // ---- bug hunt backlog (engine runtime/hunt.py) ------------------------------------------
    fun hunts(pid: String): JsonNode = get("/projects/$pid/hunts")
    fun hunt(pid: String, run: String): JsonNode = get("/projects/$pid/hunts/$run")
    fun closeHunt(pid: String, run: String, body: Map<String, Any?>): JsonNode = post("/projects/$pid/hunts/$run/close", body)

    // ---- explain a step (engine runtime/explain.py) -----------------------------------------
    fun explainStep(body: Map<String, Any?>): JsonNode = post("/steps/explain", body)
}
