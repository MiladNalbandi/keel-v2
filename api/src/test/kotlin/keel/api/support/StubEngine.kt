package keel.api.support

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList

/** A tiny fake of the Python engine's HTTP API, enough for the api's tests. */
class StubEngine private constructor(private val server: HttpServer) {
    private val mapper = jacksonObjectMapper()
    val url: String get() = "http://127.0.0.1:${server.address.port}"

    data class Call(val method: String, val path: String, val body: JsonNode?)
    val calls = CopyOnWriteArrayList<Call>()

    fun lastBody(path: String): JsonNode? = calls.lastOrNull { it.path == path }?.body

    val featureTemplate = mapOf(
        "id" to "feature", "name" to "feature (keel)", "based_on" to null, "keel_rules" to true, "version" to 1,
        "steps" to listOf(
            mapOf("id" to "spec", "kind" to "agent", "name" to "spec", "agent" to "explorer", "model" to "default", "phase" to "spec"),
            mapOf("id" to "g-spec", "kind" to "gate", "name" to "spec approval", "lock" to true, "phase" to "spec", "back" to "spec"),
            mapOf("id" to "red", "kind" to "agent", "name" to "red", "agent" to "test-author", "per_ac" to true, "phase" to "red"),
            mapOf("id" to "vred", "kind" to "code", "name" to "verify_red", "action" to "verify_red", "per_ac" to true, "lock" to true, "phase" to "red"),
            mapOf("id" to "green", "kind" to "agent", "name" to "green", "agent" to "implementer", "per_ac" to true, "phase" to "green"),
        ),
        "yaml" to "",
    )

    val knowledgeTemplate = mapOf(
        "id" to "knowledge-refresh", "name" to "knowledge refresh (keel)", "based_on" to null, "keel_rules" to true, "version" to 1,
        "steps" to listOf(
            mapOf("id" to "write", "kind" to "parallel", "name" to "librarians", "agent" to "librarian", "per_ac" to true, "phase" to "memory"),
            mapOf("id" to "check", "kind" to "code", "name" to "memory_check", "action" to "memory_check", "phase" to "memory"),
            mapOf("id" to "commit", "kind" to "code", "name" to "commit", "action" to "commit", "phase" to "memory"),
        ),
        "yaml" to "",
    )

    /** Extra ThreadState fields per thread id (for example a "fix" wait), merged over the default. */
    val overrides = java.util.concurrent.ConcurrentHashMap<String, Map<String, Any?>>()

    private fun state(id: String, status: String = "running"): Map<String, Any?> = mapOf(
        "thread_id" to id, "project_id" to "p", "workflow_id" to "feature", "title" to "t", "status" to status,
        "current" to "spec", "phase" to "spec", "ac" to null,
        "acs" to listOf(mapOf("id" to "AC-001", "layer" to "API", "title" to "a", "status" to "todo")),
        "usage" to mapOf("tokens_in" to 0, "tokens_out" to 0, "cost_usd" to 0, "premium_requests" to 0, "cap_tokens" to 500000),
        "checkpoints" to 1, "updated_at" to "2026-10-03T00:00:00Z",
        "blockers" to listOf(mapOf("gate" to "coverage", "why" to "Coverage is 61%", "fix" to "Add tests for Score.kt")),
        "ladder" to listOf(mapOf("n" to 1, "name" to "build", "cmd" to "./gradlew build", "status" to "pass")),
    ) + overrides[id].orEmpty()

    private fun route(method: String, path: String, body: JsonNode?): Pair<Int, Any?> = when {
        path == "/health" -> 200 to mapOf("ok" to true, "version" to "stub", "fake" to true)
        path == "/templates" -> 200 to listOf(featureTemplate, knowledgeTemplate)
        path == "/providers/models" -> 200 to mapOf(
            "fake" to listOf(mapOf("id" to "fake", "label" to "Fake model")),
            "claude" to listOf(mapOf("id" to "claude-sonnet", "label" to "Sonnet")),
        )
        path == "/workflows/validate" -> {
            val yaml = body?.get("yaml")?.asText() ?: ""
            if (yaml.contains("INVALID")) 200 to mapOf("ok" to false, "errors" to listOf("step INVALID is not allowed"))
            else 200 to mapOf("ok" to true, "errors" to emptyList<String>())
        }
        path == "/workflows/estimate" -> 200 to mapOf(
            "tokens" to 1000, "low" to 800, "high" to 1500, "cost_usd" to 0.0, "premium_requests" to 0,
            "by_provider" to mapOf("fake" to 1000), "per_step" to emptyList<Any>(),
        )
        path == "/threads" && method == "POST" -> 200 to mapOf("thread_id" to "t-stub-1")
        path.matches(Regex("/threads/[^/]+")) -> 200 to state(path.removePrefix("/threads/"))
        path.endsWith("/resume") -> {
            val id = path.split('/')[2]
            overrides.remove(id)
            val unlock = body?.get("payload")?.get("unlock")?.let { mapper.convertValue(it, Map::class.java) }
            200 to (state(id, "running") + (if (unlock != null) mapOf("unlocks" to listOf(unlock)) else emptyMap()))
        }
        path.endsWith("/stop") -> 200 to state(path.split('/')[2], "stopped")
        path.endsWith("/history") -> 200 to listOf(mapOf("id" to "c1", "n" to 1, "step" to "spec", "at" to "2026-10-03T00:00:00Z", "note" to "start"))
        path == "/providers/test" -> 200 to mapOf("ok" to true, "text" to "OK", "ms" to 3)
        path == "/mcp/tools" -> 200 to mapOf("ok" to true, "tools" to listOf(mapOf("name" to "keel_next", "description" to "next step")))
        else -> 404 to mapOf("error" to "no route $path")
    }

    private fun handle(ex: HttpExchange) {
        val text = ex.requestBody.readAllBytes().toString(Charsets.UTF_8)
        val body = if (text.isBlank()) null else runCatching { mapper.readTree(text) }.getOrNull()
        val path = ex.requestURI.path
        calls += Call(ex.requestMethod, path, body)
        val (code, res) = route(ex.requestMethod, path, body)
        val bytes = mapper.writeValueAsBytes(res)
        ex.responseHeaders.add("Content-Type", "application/json")
        ex.sendResponseHeaders(code, bytes.size.toLong())
        ex.responseBody.use { it.write(bytes) }
    }

    companion object {
        fun start(): StubEngine {
            val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
            val stub = StubEngine(server)
            server.createContext("/") { stub.handle(it) }
            server.start()
            return stub
        }
    }
}
