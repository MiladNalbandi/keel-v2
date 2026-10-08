package keel.api.support

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.net.URLDecoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

/**
 * v0.14.0 a small GitHub (REST + the GraphQL review threads) and GitLab (REST v4 under /api/v4) for the Code Review
 * plugin's tests. Pull requests and merge requests are what a test puts in; every call is recorded.
 */
class StubCodeHost private constructor(private val server: HttpServer) {
    private val mapper = jacksonObjectMapper()
    val url: String get() = "http://127.0.0.1:${server.address.port}"
    val githubToken = "gh-review-token-1" // test fixture, keel:allow-secret
    val gitlabToken = "gl-review-token-2" // test fixture, keel:allow-secret
    var login = "me"

    data class Call(val method: String, val path: String, val query: String, val body: JsonNode?)
    val calls = CopyOnWriteArrayList<Call>()
    private val ids = AtomicInteger(1000)

    data class Comment(val id: Int, val author: String, val body: String)
    data class Thread(val id: String, val path: String, val line: Int, val side: String, var resolved: Boolean = false,
                      val comments: MutableList<Comment> = CopyOnWriteArrayList())
    data class Pr(
        val number: Int, var title: String, val author: String, val branch: String, val base: String, var headSha: String,
        val reviewers: List<String> = emptyList(), val body: String = "", var state: String = "open", val fork: Boolean = false,
        val threads: MutableList<Thread> = CopyOnWriteArrayList(), val conversation: MutableList<Comment> = CopyOnWriteArrayList(),
        val reviews: MutableList<Map<String, Any?>> = CopyOnWriteArrayList(), val checks: MutableList<Map<String, Any?>> = CopyOnWriteArrayList(),
        val approvedBy: MutableList<String> = CopyOnWriteArrayList(),
    )

    /** "owner/repo" or "group/project" → its pull requests (GitHub) or merge requests (GitLab). */
    val github = ConcurrentHashMap<String, MutableMap<Int, Pr>>()
    val gitlab = ConcurrentHashMap<String, MutableMap<Int, Pr>>()

    fun reset() {
        calls.clear(); github.clear(); gitlab.clear(); login = "me"
    }

    fun thread(pr: Pr, path: String, line: Int, author: String, body: String): Thread =
        Thread("PRRT_${ids.incrementAndGet()}", path, line, "RIGHT").also { it.comments += Comment(ids.incrementAndGet(), author, body); pr.threads += it }

    fun calls(method: String, pathEnd: String) = calls.filter { it.method == method && it.path.endsWith(pathEnd) }

    private fun handle(ex: HttpExchange) {
        val text = ex.requestBody.readAllBytes().toString(Charsets.UTF_8)
        val body = if (text.isBlank()) null else runCatching { mapper.readTree(text) }.getOrNull()
        val path = ex.requestURI.rawPath
        val query = ex.requestURI.rawQuery.orEmpty()
        calls += Call(ex.requestMethod, path, query, body)
        val (code, res) = if (path.startsWith("/api/v4/")) {
            if (ex.requestHeaders.getFirst("PRIVATE-TOKEN") != gitlabToken) 401 to mapOf("message" to "401 Unauthorized")
            else gitlab(ex.requestMethod, path.removePrefix("/api/v4"), params(query), body)
        } else {
            if (ex.requestHeaders.getFirst("Authorization") != "Bearer $githubToken") 401 to mapOf("message" to "Bad credentials")
            else github(ex.requestMethod, path, params(query), body)
        }
        val bytes = mapper.writeValueAsBytes(res)
        ex.responseHeaders.add("Content-Type", "application/json")
        ex.sendResponseHeaders(code, bytes.size.toLong())
        ex.responseBody.use { it.write(bytes) }
    }

    private fun params(q: String) = q.split('&').filter { it.contains('=') }.associate {
        URLDecoder.decode(it.substringBefore('='), Charsets.UTF_8) to URLDecoder.decode(it.substringAfter('='), Charsets.UTF_8)
    }

    // ---------------------------------------------------------------- GitHub

    private fun ghPr(repo: String, p: Pr) = mapOf(
        "number" to p.number, "title" to p.title, "user" to mapOf("login" to p.author), "body" to p.body, "state" to if (p.state == "merged") "closed" else p.state,
        "merged" to (p.state == "merged"), "draft" to false, "updated_at" to "2026-10-08T10:00:00Z", "html_url" to "https://github.com/$repo/pull/${p.number}",
        "head" to mapOf("ref" to p.branch, "sha" to p.headSha, "repo" to mapOf("full_name" to if (p.fork) "someone/fork" else repo)),
        "base" to mapOf("ref" to p.base), "requested_reviewers" to p.reviewers.map { mapOf("login" to it) },
    )

    private fun github(method: String, path: String, q: Map<String, String>, body: JsonNode?): Pair<Int, Any?> {
        if (path == "/user") return 200 to mapOf("login" to login)
        if (path == "/graphql") return graphql(body)
        val m = Regex("^/repos/([^/]+)/([^/]+)(/.*)$").find(path) ?: return 404 to mapOf("message" to "Not Found")
        val repo = "${m.groupValues[1]}/${m.groupValues[2]}"
        val rest = m.groupValues[3]
        val prs = github[repo] ?: return 404 to mapOf("message" to "Not Found")
        fun pr(n: String) = prs[n.toInt()]
        Regex("^/pulls$").find(rest)?.let {
            val head = q["head"]?.substringAfter(':')
            return 200 to prs.values.filter { it.state == "open" && (head == null || it.branch == head) }.sortedByDescending { it.number }.map { ghPr(repo, it) }
        }
        Regex("^/pulls/(\\d+)$").find(rest)?.let { r -> return pr(r.groupValues[1])?.let { 200 to ghPr(repo, it) } ?: (404 to mapOf("message" to "Not Found")) }
        Regex("^/commits/([0-9a-f]+)/check-runs$").find(rest)?.let { r ->
            val p = prs.values.firstOrNull { it.headSha == r.groupValues[1] }
            return 200 to mapOf("check_runs" to p?.checks.orEmpty())
        }
        Regex("^/commits/([0-9a-f]+)/status$").find(rest)?.let { return 200 to mapOf("statuses" to emptyList<Any>()) }
        Regex("^/pulls/(\\d+)/reviews$").find(rest)?.let { r ->
            val p = pr(r.groupValues[1]) ?: return 404 to mapOf("message" to "Not Found")
            if (method == "GET") return 200 to p.reviews
            val comments = body?.path("comments")?.toList().orEmpty()
            for (c in comments) {
                val line = c.path("line").asInt()
                if (line <= 0) return 422 to mapOf("message" to "Unprocessable Entity: line must be part of the diff")
                p.threads += Thread("PRRT_${ids.incrementAndGet()}", c.path("path").asText(), line, c.path("side").asText("RIGHT"))
                    .also { it.comments += Comment(ids.incrementAndGet(), login, c.path("body").asText()) }
            }
            val event = body?.path("event")?.asText().orEmpty()
            p.reviews += mapOf("user" to mapOf("login" to login), "state" to when (event) { "APPROVE" -> "APPROVED"; "REQUEST_CHANGES" -> "CHANGES_REQUESTED"; else -> "COMMENTED" })
            return 200 to mapOf("id" to ids.incrementAndGet(), "html_url" to "https://github.com/$repo/pull/${p.number}#pullrequestreview-1")
        }
        Regex("^/issues/(\\d+)/comments$").find(rest)?.let { r ->
            val p = pr(r.groupValues[1]) ?: return 404 to mapOf("message" to "Not Found")
            return 200 to p.conversation.map { mapOf("id" to it.id, "user" to mapOf("login" to it.author), "body" to it.body, "created_at" to "2026-10-08T09:00:00Z") }
        }
        Regex("^/pulls/(\\d+)/comments/(\\d+)/replies$").find(rest)?.let { r ->
            val p = pr(r.groupValues[1]) ?: return 404 to mapOf("message" to "Not Found")
            val t = p.threads.firstOrNull { it.comments.firstOrNull()?.id == r.groupValues[2].toInt() } ?: return 404 to mapOf("message" to "Not Found")
            t.comments += Comment(ids.incrementAndGet(), login, body?.path("body")?.asText().orEmpty())
            return 201 to mapOf("id" to t.comments.last().id)
        }
        return 404 to mapOf("message" to "Not Found")
    }

    private fun graphql(body: JsonNode?): Pair<Int, Any?> {
        val q = body?.path("query")?.asText().orEmpty()
        val v = body?.path("variables")
        if (q.contains("resolveReviewThread") || q.contains("unresolveReviewThread")) {
            val id = v?.path("id")?.asText()
            val t = github.values.flatMap { it.values }.flatMap { it.threads }.firstOrNull { it.id == id }
                ?: return 200 to mapOf("errors" to listOf(mapOf("message" to "Could not resolve to a node with the global id of '$id'")))
            t.resolved = !q.contains("unresolve")
            return 200 to mapOf("data" to mapOf("x" to mapOf("thread" to mapOf("id" to t.id, "isResolved" to t.resolved))))
        }
        val repo = "${v?.path("o")?.asText()}/${v?.path("r")?.asText()}"
        val p = github[repo]?.get(v?.path("n")?.asInt() ?: 0) ?: return 200 to mapOf("data" to mapOf("repository" to null))
        val nodes = p.threads.map { t ->
            mapOf("id" to t.id, "isResolved" to t.resolved, "isOutdated" to false, "path" to t.path, "line" to t.line, "originalLine" to t.line,
                "diffSide" to t.side, "comments" to mapOf("nodes" to t.comments.map {
                    mapOf("id" to "PRRC_${it.id}", "databaseId" to it.id, "body" to it.body, "createdAt" to "2026-10-08T09:00:00Z", "url" to null,
                        "author" to mapOf("login" to it.author))
                }))
        }
        return 200 to mapOf("data" to mapOf("repository" to mapOf("pullRequest" to mapOf("reviewThreads" to mapOf("nodes" to nodes)))))
    }

    // ---------------------------------------------------------------- GitLab

    private fun glMr(project: String, p: Pr) = mapOf(
        "iid" to p.number, "title" to p.title, "author" to mapOf("username" to p.author), "description" to p.body,
        "state" to if (p.state == "open") "opened" else p.state, "draft" to false, "updated_at" to "2026-10-08T10:00:00Z",
        "web_url" to "https://gitlab.test/$project/-/merge_requests/${p.number}", "source_branch" to p.branch, "target_branch" to p.base,
        "sha" to p.headSha, "source_project_id" to if (p.fork) 2 else 1, "target_project_id" to 1,
        "reviewers" to p.reviewers.map { mapOf("username" to it) },
        "diff_refs" to mapOf("base_sha" to "b".repeat(40), "start_sha" to "s".repeat(40), "head_sha" to p.headSha),
    )

    private fun gitlab(method: String, path: String, q: Map<String, String>, body: JsonNode?): Pair<Int, Any?> {
        if (path == "/user") return 200 to mapOf("username" to login)
        val m = Regex("^/projects/([^/]+)(/.*)$").find(path) ?: return 404 to mapOf("message" to "404 Not Found")
        val project = URLDecoder.decode(m.groupValues[1], Charsets.UTF_8)
        val rest = m.groupValues[2]
        val mrs = gitlab[project] ?: return 404 to mapOf("message" to "404 Project Not Found")
        fun mr(n: String) = mrs[n.toInt()]
        if (rest == "/merge_requests") {
            val src = q["source_branch"]
            return 200 to mrs.values.filter { it.state == "open" && (src == null || it.branch == src) }.sortedByDescending { it.number }.map { glMr(project, it) }
        }
        Regex("^/merge_requests/(\\d+)$").find(rest)?.let { r -> return mr(r.groupValues[1])?.let { 200 to glMr(project, it) } ?: (404 to mapOf("message" to "404 Not found")) }
        Regex("^/merge_requests/(\\d+)/pipelines$").find(rest)?.let { return 200 to listOf(mapOf("id" to 55, "status" to "success", "web_url" to null)) }
        Regex("^/pipelines/(\\d+)/jobs$").find(rest)?.let { return 200 to listOf(mapOf("name" to "test", "status" to "success")) }
        Regex("^/merge_requests/(\\d+)/approvals$").find(rest)?.let { r ->
            return 200 to mapOf("approved_by" to mr(r.groupValues[1])?.approvedBy.orEmpty().map { mapOf("user" to mapOf("username" to it)) })
        }
        Regex("^/merge_requests/(\\d+)/(approve|unapprove)$").find(rest)?.let { r ->
            val p = mr(r.groupValues[1]) ?: return 404 to mapOf("message" to "404 Not found")
            if (r.groupValues[2] == "approve") p.approvedBy += login else p.approvedBy.remove(login)
            return 201 to mapOf("ok" to true)
        }
        Regex("^/merge_requests/(\\d+)/notes$").find(rest)?.let { r ->
            val p = mr(r.groupValues[1]) ?: return 404 to mapOf("message" to "404 Not found")
            p.conversation += Comment(ids.incrementAndGet(), login, body?.path("body")?.asText().orEmpty())
            return 201 to mapOf("id" to p.conversation.last().id)
        }
        Regex("^/merge_requests/(\\d+)/discussions$").find(rest)?.let { r ->
            val p = mr(r.groupValues[1]) ?: return 404 to mapOf("message" to "404 Not found")
            if (method == "POST") {
                val pos = body?.path("position")
                val line = pos?.path("new_line")?.takeIf { it.isInt }?.asInt() ?: pos?.path("old_line")?.asInt() ?: 0
                if (pos?.path("head_sha")?.asText() != p.headSha) return 400 to mapOf("message" to mapOf("base" to listOf("position is invalid")))
                val t = Thread(ids.incrementAndGet().toString(), pos.path("new_path").asText(), line, if (pos.has("old_line")) "LEFT" else "RIGHT")
                t.comments += Comment(ids.incrementAndGet(), login, body.path("body").asText())
                p.threads += t
                return 201 to mapOf("id" to t.id)
            }
            val ds = p.threads.map { t ->
                mapOf("id" to t.id, "notes" to t.comments.mapIndexed { i, c ->
                    mapOf("id" to c.id, "body" to c.body, "author" to mapOf("username" to c.author), "created_at" to "2026-10-08T09:00:00Z", "system" to false,
                        "resolvable" to true, "resolved" to t.resolved,
                        "position" to if (i == 0) mapOf("new_path" to t.path, "old_path" to t.path, "new_line" to t.line.takeIf { t.side == "RIGHT" },
                            "old_line" to t.line.takeIf { t.side == "LEFT" }) else null)
                })
            } + p.conversation.map { c ->
                mapOf("id" to "c${c.id}", "notes" to listOf(mapOf("id" to c.id, "body" to c.body, "author" to mapOf("username" to c.author),
                    "created_at" to "2026-10-08T09:00:00Z", "system" to false, "resolvable" to false)))
            }
            return 200 to ds
        }
        Regex("^/merge_requests/(\\d+)/discussions/([^/]+)/notes$").find(rest)?.let { r ->
            val t = mr(r.groupValues[1])?.threads?.firstOrNull { it.id == r.groupValues[2] } ?: return 404 to mapOf("message" to "404 Not found")
            t.comments += Comment(ids.incrementAndGet(), login, body?.path("body")?.asText().orEmpty())
            return 201 to mapOf("id" to t.comments.last().id)
        }
        Regex("^/merge_requests/(\\d+)/discussions/([^/]+)$").find(rest)?.let { r ->
            val t = mr(r.groupValues[1])?.threads?.firstOrNull { it.id == r.groupValues[2] } ?: return 404 to mapOf("message" to "404 Not found")
            t.resolved = q["resolved"] == "true"
            return 200 to mapOf("id" to t.id)
        }
        return 404 to mapOf("message" to "404 Not found")
    }

    companion object {
        fun start(): StubCodeHost {
            val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
            val stub = StubCodeHost(server)
            server.createContext("/") { stub.handle(it) }
            server.start()
            return stub
        }
    }
}
