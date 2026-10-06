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

    private fun git(root: java.nio.file.Path, vararg args: String): Boolean {
        val p = ProcessBuilder(listOf("git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args))
            .directory(root.toFile()).redirectErrorStream(true).start()
        p.inputStream.readAllBytes()
        return p.waitFor() == 0
    }

    val featureTemplate = mapOf(
        "id" to "feature", "name" to "feature (keel)", "based_on" to null, "keel_rules" to true, "version" to 1,
        "steps" to listOf(
            mapOf("id" to "spec", "kind" to "agent", "name" to "spec", "agent" to "explorer", "model" to "default", "phase" to "spec"),
            mapOf("id" to "g-spec", "kind" to "gate", "name" to "spec approval", "lock" to true, "phase" to "spec", "back" to "spec"),
            mapOf("id" to "red", "kind" to "agent", "name" to "red", "agent" to "test-author", "per_ac" to true, "phase" to "red"),
            mapOf("id" to "vred", "kind" to "code", "name" to "verify_red", "action" to "verify_red", "per_ac" to true, "lock" to true, "phase" to "red"),
            mapOf("id" to "green", "kind" to "agent", "name" to "green", "agent" to "implementer", "per_ac" to true, "phase" to "green"),
            mapOf("id" to "ac_gate", "kind" to "gate", "name" to "AC gate", "per_ac" to true, "phase" to "gate", "back" to "green"),
            mapOf("id" to "ship_verify", "kind" to "code", "name" to "verify fast + module", "action" to "verify_fast", "phase" to "ship",
                "included_from" to "ship"),
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

    /** What POST /providers/test answers; null = OK. */
    @Volatile var providerTestAnswer: Map<String, Any?>? = null

    /** What POST /providers/usage answers per provider; missing = an error answer. */
    val usageAnswers = java.util.concurrent.ConcurrentHashMap<String, Map<String, Any?>>()

    /** What POST /agents/ask answers (the Doctor's model call); null = "fake" (rules only). */
    @Volatile var askAnswer: Map<String, Any?>? = null

    /** Extra ThreadState fields per thread id (for example a "fix" wait), merged over the default. */
    val overrides = java.util.concurrent.ConcurrentHashMap<String, Map<String, Any?>>()

    /** The index status per project (GET /projects/{p}/index); POST /projects/{p}/scan sets it to "indexing". */
    val index = java.util.concurrent.ConcurrentHashMap<String, Map<String, Any?>>()

    /** Maps built per project (POST /projects/{p}/map), like the engine stores them. */
    val maps = java.util.concurrent.ConcurrentHashMap<String, Map<String, Any?>>()

    /** Run modes set per thread (POST /threads/{id}/mode), shown as run_mode in the thread state. */
    val modes = java.util.concurrent.ConcurrentHashMap<String, String>()

    /** Thread ids the next POST /threads answer, in order (empty: "t-stub-1"). */
    val nextThreadIds = java.util.concurrent.ConcurrentLinkedQueue<String>()

    /** Helper sessions (POST /helper/sessions), like the engine keeps them; a test can set "project" to another one. */
    val helperSessions = java.util.concurrent.ConcurrentHashMap<String, MutableMap<String, Any?>>()

    /** The Helper's commands waiting for the person (GET /helper/permissions); a test adds them. */
    val helperQuestions = CopyOnWriteArrayList<Map<String, Any?>>()
    /** What GET /helper/sessions/{sid}/handover (and POST .../release) answer for a side session; tests set it. */
    @Volatile var helperHandover: Map<String, Any?> = mapOf("branch" to "keel/helper/abc", "base" to "0000000", "commits" to emptyList<Any>(),
        "uncommitted" to emptyList<Any>(), "asked" to listOf("Add a price helper"), "answer" to "Added it.", "title" to "Add a price helper")

    /** Unlocks posted per thread (POST /threads/{id}/unlocks), like the engine keeps them. */
    val unlocks = java.util.concurrent.ConcurrentHashMap<String, MutableList<Map<String, Any?>>>()

    private fun state(id: String, status: String = "running"): Map<String, Any?> = mapOf(
        "thread_id" to id, "project_id" to "p", "workflow_id" to "feature", "title" to "t", "status" to status,
        "current" to "spec", "phase" to "spec", "ac" to null,
        "acs" to listOf(mapOf("id" to "AC-001", "layer" to "API", "title" to "a", "status" to "todo")),
        "usage" to mapOf("tokens_in" to 0, "tokens_out" to 0, "cost_usd" to 0, "premium_requests" to 0, "cap_tokens" to 500000),
        "checkpoints" to 1, "updated_at" to "2026-10-03T00:00:00Z",
        "blockers" to listOf(mapOf("gate" to "coverage", "why" to "Coverage is 61%", "fix" to "Add tests for Score.kt")),
        "ladder" to listOf(mapOf("n" to 1, "name" to "build", "cmd" to "./gradlew build", "status" to "pass")),
        "run_mode" to (modes[id] ?: "manual"),
    ) + overrides[id].orEmpty()

    private fun route(method: String, path: String, body: JsonNode?): Pair<Int, Any?> = when {
        path == "/health" -> 200 to mapOf("ok" to true, "version" to "stub", "fake" to true)
        path == "/agents/ask" -> 200 to (askAnswer ?: mapOf("ok" to true, "fake" to true, "text" to ""))
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
        path == "/helper/sessions" && method == "POST" -> {
            val id = "h_stub_${helperSessions.size + 1}"
            val sess = mutableMapOf<String, Any?>("id" to id, "project" to body?.get("project_id")?.asText(), "root" to body?.get("root")?.asText(),
                "mode" to (body?.get("mode")?.asText() ?: "ask"), "title" to (body?.get("title")?.asText()?.ifBlank { null } ?: "New chat"),
                "model" to body?.get("model")?.let { mapper.convertValue(it, Map::class.java) }, "status" to "idle", "turns" to 0,
                "thread_id" to body?.get("thread_id")?.asText(),
                "messages" to emptyList<Any>())
            helperSessions[id] = sess
            200 to sess
        }
        path == "/helper/sessions" -> 200 to helperSessions.values.toList()
        path == "/helper/permissions" -> 200 to helperQuestions.toList()
        path.matches(Regex("/helper/permissions/[^/]+")) && method == "POST" -> {
            val id = path.split('/')[3]
            val q = helperQuestions.firstOrNull { it["id"] == id }
            if (q == null) 404 to mapOf("error" to "That question was answered already.")
            else { helperQuestions.remove(q); 200 to mapOf("id" to id, "decision" to body?.get("decision")?.asText()) }
        }
        path.matches(Regex("/helper/sessions/[^/]+/changes")) -> 200 to listOf(mapOf("path" to "src/a.kt", "status" to "modified",
            "added" to 2, "removed" to 1, "diff" to "@@ -1 +1,2 @@"))
        path.matches(Regex("/helper/sessions/[^/]+/undo")) -> 200 to emptyList<Any>()
        path.matches(Regex("/helper/sessions/[^/]+/(handover|release)")) -> 200 to helperHandover
        path.matches(Regex("/helper/sessions/[^/]+/done")) -> 200 to mapOf("ok" to true, "sha" to "abc1234", "message" to "green: helper: x",
            "files" to listOf("src/a.kt"))
        path == "/helper/commands" -> 200 to listOf(mapOf("name" to "explain", "description" to "Explain", "plugin" to "core", "source" to "keel"))
        path.matches(Regex("/helper/sessions/[^/]+/turn")) -> {
            val id = path.split('/')[3]
            if (!helperSessions.containsKey(id)) 404 to mapOf("error" to "No Helper session $id.")
            else 200 to mapOf("session" to id, "call_id" to "call-$id", "n" to 1, "command" to null)
        }
        path.matches(Regex("/helper/sessions/[^/]+/stop")) -> 200 to helperSessions[path.split('/')[3]]
        path.matches(Regex("/helper/sessions/[^/]+")) -> {
            val id = path.split('/')[3]
            val sess = helperSessions[id]
            when {
                sess == null -> 404 to mapOf("error" to "No Helper session $id.")
                method == "DELETE" -> { helperSessions.remove(id); 200 to mapOf("ok" to true) }
                method == "PATCH" -> {
                    body?.get("title")?.asText()?.let { sess["title"] = it }
                    body?.get("model")?.let { sess["model"] = mapper.convertValue(it, Map::class.java) }
                    200 to sess
                }
                else -> 200 to sess
            }
        }
        // v0.7.x: a flow's worktree, made for real in the test project (the board reads its git)
        path == "/worktrees" && method == "POST" -> {
            val root = java.nio.file.Paths.get(body!!["root"].asText())
            val name = body["name"].asText()
            val branch = body["branch"].asText()
            val dir = root.resolve(".keel/worktrees").resolve(name)
            val start = body.get("start")?.takeIf { !it.isNull }?.asText() ?: "HEAD"
            val ok = git(root, "worktree", "add", "-q", "-b", branch, dir.toString(), start)
            if (!ok) 409 to mapOf("error" to "git could not make the worktree $name.")
            else 200 to mapOf("path" to dir.toString(), "branch" to branch, "base" to start)
        }
        path == "/worktrees/remove" -> {
            val root = java.nio.file.Paths.get(body!!["root"].asText())
            git(root, "worktree", "remove", "--force", root.resolve(".keel/worktrees").resolve(body["name"].asText()).toString())
            200 to mapOf("ok" to true)
        }
        path == "/threads" && method == "POST" -> 200 to mapOf("thread_id" to (nextThreadIds.poll() ?: "t-stub-1"))
        path.matches(Regex("/threads/[^/]+")) -> 200 to state(path.removePrefix("/threads/"))
        path.endsWith("/mode") && method == "POST" -> {
            val id = path.split('/')[2]
            val mode = body?.get("mode")?.asText() ?: ""
            if (mode !in setOf("manual", "important", "auto", "readonly")) 400 to mapOf("error" to "\"$mode\" is not a run mode.")
            else { modes[id] = mode; 200 to state(id) }
        }
        path.endsWith("/resume") -> {
            val id = path.split('/')[2]
            overrides.remove(id)
            val unlock = body?.get("payload")?.get("unlock")?.let { mapper.convertValue(it, Map::class.java) }
            200 to (state(id, "running") + (if (unlock != null) mapOf("unlocks" to listOf(unlock)) else emptyMap()))
        }
        path.endsWith("/unlocks") && method == "POST" -> {
            val id = path.split('/')[2]
            val phase = body?.get("phase")?.asText() ?: state(id)["phase"]
            val item = mapOf("path" to body?.get("path")?.asText(), "phase" to phase, "by" to "api", "reason" to body?.get("reason")?.asText())
            val list = unlocks.getOrPut(id) { java.util.concurrent.CopyOnWriteArrayList() }
            if (list.none { it["path"] == item["path"] && it["phase"] == item["phase"] }) list += item
            200 to list
        }
        path.endsWith("/unlocks") -> 200 to unlocks[path.split('/')[2]].orEmpty()
        path.endsWith("/stop") -> 200 to state(path.split('/')[2], "stopped")
        path.endsWith("/history") -> 200 to listOf(mapOf("id" to "c1", "n" to 1, "step" to "spec", "at" to "2026-10-03T00:00:00Z", "note" to "start"))
        path == "/providers/usage" -> 200 to (usageAnswers[body?.get("provider")?.asText()]
            ?: mapOf("ok" to false, "windows" to emptyList<Any>(), "error" to "no answer"))
        path == "/providers/test" -> 200 to (providerTestAnswer ?: mapOf("ok" to true, "text" to "OK", "ms" to 3))
        path.matches(Regex("/projects/[^/]+/scan")) -> {
            val pid = path.split('/')[2]
            index[pid] = mapOf("project" to pid, "root" to body?.get("root")?.asText(), "status" to "indexing", "files" to 0, "symbols" to 0,
                "rebuild" to (body?.get("rebuild")?.asBoolean() ?: false))
            200 to index[pid]
        }
        path.matches(Regex("/projects/[^/]+/index")) -> {
            val pid = path.split('/')[2]
            200 to (index[pid] ?: mapOf("project" to pid, "status" to "idle", "files" to 0, "symbols" to 0))
        }
        path.matches(Regex("/projects/[^/]+/map")) && method == "POST" -> {
            val pid = path.split('/')[2]
            maps[pid] = mapOf("sha" to "abc1234", "at" to "2026-10-05T00:00:00Z", "counts" to mapOf("tables" to 1),
                "levels" to mapOf("er" to mapOf("nodes" to emptyList<Any>(), "edges" to emptyList<Any>(), "width" to 100, "height" to 100)),
                "root" to body?.get("root")?.asText())
            200 to maps[pid]
        }
        path.matches(Regex("/projects/[^/]+/map")) -> 200 to (maps[path.split('/')[2]] ?: mapOf("missing" to "No map yet. Build it to draw one."))
        path.matches(Regex("/projects/[^/]+/graph")) -> 200 to mapOf("available" to true, "counts" to mapOf("units" to 2),
            "groups" to listOf(mapOf("id" to "package:com.x.app", "kind" to "package", "name" to "com.x.app", "label" to "app", "path" to listOf("app"))),
            "units" to listOf(mapOf("id" to "class:svc", "name" to "Svc", "kind" to "class", "group" to "package:com.x.app")), "links" to emptyList<Any>())
        path.matches(Regex("/projects/[^/]+/graph/search")) -> 200 to mapOf("available" to true,
            "results" to listOf(mapOf("id" to "class:svc", "name" to "Svc", "q" to body?.get("q")?.asText())))
        path.matches(Regex("/projects/[^/]+/graph/node")) -> 200 to mapOf("available" to true, "level" to "unit",
            "focus" to mapOf("id" to body?.get("id")?.asText(), "name" to "Svc"), "depth" to body?.get("depth")?.asInt(), "nodes" to emptyList<Any>(), "edges" to emptyList<Any>())
        path.matches(Regex("/projects/[^/]+/hunts")) -> 200 to listOf(mapOf("run" to "2026-10-05-01", "mode" to "semi", "open" to 1,
            "counts" to mapOf("candidate" to 0, "proven" to 1, "unproven" to 0, "false" to 0)))
        path.matches(Regex("/projects/[^/]+/hunts/[^/]+/close")) -> {
            val id = body?.get("id")?.asText()
            200 to mapOf("run" to path.split('/')[4], "candidates" to listOf(mapOf("id" to id, "status" to "proven",
                "close" to mapOf("as" to body?.get("as")?.asText(), "note" to body?.get("note")?.asText()))))
        }
        path.matches(Regex("/projects/[^/]+/hunts/[^/]+")) -> {
            val run = path.split('/')[4]
            if (run == "nope") 404 to mapOf("error" to "No hunt nope in project ${path.split('/')[2]}.")
            else 200 to mapOf("run" to run, "candidates" to listOf(mapOf("id" to "F-001", "status" to "proven", "severity" to "high")),
                "groups" to emptyList<Any>(), "report_markdown" to "# Bug hunt")
        }
        path == "/steps/explain" -> {
            val step = body?.get("step_id")?.asText()
            if (step == "nope") 404 to mapOf("error" to "No step 'nope' in workflow feature.")
            else 200 to mapOf("id" to step, "name" to step, "kind" to "agent", "phase" to "red", "thread" to (body?.has("thread_id") == true),
                "rules" to mapOf("buckets" to emptyList<Any>(), "shell_refused" to emptyList<Any>()), "next" to emptyList<Any>())
        }
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
