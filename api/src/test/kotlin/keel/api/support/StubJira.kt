package keel.api.support

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.net.URLDecoder
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A small Jira on 127.0.0.1, enough for keel's client: Cloud (Basic email:token, /rest/api/3/search/jql with ADF
 * descriptions, accountId users) or Server/Data Center (Bearer PAT, /rest/api/2/search, user names). No real Jira call
 * is ever made by the tests.
 */
class StubJira private constructor(private val server: HttpServer, val kind: String) {
    private val mapper = jacksonObjectMapper()
    val url: String get() = "http://127.0.0.1:${server.address.port}"
    val cloud get() = kind == "cloud"
    val email = "dev@example.com"
    val token = if (kind == "cloud") "cloud-token-abc123XYZ" else "server-pat-def456UVW" // test fixture, keel:allow-secret

    data class Call(val method: String, val path: String, val query: String, val body: JsonNode?, val auth: String?)
    val calls = CopyOnWriteArrayList<Call>()

    /** status name → category key */
    val statuses = linkedMapOf("To Do" to "new", "In Progress" to "indeterminate", "In Review" to "indeterminate",
        "Testing in PP" to "indeterminate", "Ready for Production" to "indeterminate", "Done" to "done")

    data class Issue(var summary: String, var status: String, var type: String = "Story", var assignee: String? = "Dev One",
                     var priority: String? = "High", var description: String = "", val comments: MutableList<String> = CopyOnWriteArrayList(),
                     val fields: MutableMap<String, Any?> = ConcurrentHashMap())
    val issues = ConcurrentHashMap<String, Issue>()

    /** path (exact) → (status, Jira's error text); the text may echo the Authorization header, the client must hide it. */
    val failures = ConcurrentHashMap<String, Pair<Int, String>>()

    /** Transitions refused for these target statuses (the workflow has no way there). */
    val noWayTo = ConcurrentHashMap.newKeySet<String>()

    val users = mapOf("rev@example.com" to "acc-rev-1")

    /** Cloud only: false = an older site without /rest/api/3/search/jql (404); /rest/api/2/search answers instead. */
    @Volatile var enhancedSearch = true

    /** v0.13.0 issue types this Jira has not (POST /rest/api/2/issue answers 400 for them), and the links made. */
    val missingTypes = ConcurrentHashMap.newKeySet<String>()
    val links = CopyOnWriteArrayList<Triple<String, String, String>>()
    private val created = java.util.concurrent.atomic.AtomicInteger(100)

    fun reset() {
        calls.clear(); issues.clear(); failures.clear(); noWayTo.clear(); enhancedSearch = true; missingTypes.clear(); links.clear()
    }

    fun add(key: String, summary: String, status: String = "To Do", type: String = "Story", description: String = "", assignee: String? = "Dev One") {
        issues[key] = Issue(summary, status, type, assignee, "High", description)
    }

    fun calls(method: String, path: String) = calls.filter { it.method == method && it.path == path }

    private val expectedAuth: String
        get() = if (cloud) "Basic " + Base64.getEncoder().encodeToString("$email:$token".toByteArray()) else "Bearer $token"

    private fun status(name: String) = mapOf("name" to name, "id" to name.hashCode().toString(), "statusCategory" to mapOf("key" to statuses[name]))

    private fun adf(text: String) = mapOf("type" to "doc", "version" to 1, "content" to text.split("\n\n").map { p ->
        if (p.startsWith("- ")) mapOf("type" to "bulletList", "content" to p.lines().map { li ->
            mapOf("type" to "listItem", "content" to listOf(mapOf("type" to "paragraph", "content" to listOf(mapOf("type" to "text", "text" to li.removePrefix("- "))))))
        })
        else mapOf("type" to "paragraph", "content" to listOf(mapOf("type" to "text", "text" to p)))
    })

    private fun issueJson(key: String, i: Issue, v3: Boolean) = mapOf(
        "key" to key, "id" to key.hashCode().toString(),
        "fields" to mapOf(
            "summary" to i.summary, "status" to status(i.status), "issuetype" to mapOf("name" to i.type),
            "assignee" to i.assignee?.let { mapOf("displayName" to it, if (cloud) "accountId" to "acc-dev-1" else "name" to "dev1") },
            "priority" to i.priority?.let { mapOf("name" to it) },
            "description" to if (i.description.isEmpty()) null else if (v3) adf(i.description) else i.description,
            "updated" to "2026-10-06T10:00:00.000+0000",
        ),
    )

    /** A tiny JQL: `key in (A, B)`, else every issue (minus Done when the query says statusCategory != Done). */
    private fun query(jql: String): List<Pair<String, Issue>> {
        val keys = Regex("key in \\(([^)]*)\\)", RegexOption.IGNORE_CASE).find(jql)?.groupValues?.get(1)?.split(',')?.map { it.trim() }
        return issues.entries.sortedBy { it.key }.map { it.key to it.value }.filter { (k, i) ->
            if (keys != null) k in keys else !(jql.contains("statusCategory != Done") && statuses[i.status] == "done")
        }
    }

    private fun params(q: String?): Map<String, String> = q.orEmpty().split('&').filter { it.contains('=') }
        .associate { it.substringBefore('=') to URLDecoder.decode(it.substringAfter('='), Charsets.UTF_8) }

    private fun route(method: String, path: String, q: Map<String, String>, body: JsonNode?): Pair<Int, Any?> {
        failures[path]?.let { (code, text) -> return code to mapOf("errorMessages" to listOf(text)) }
        val issueKey = Regex("^/rest/api/2/issue/([^/]+)").find(path)?.groupValues?.get(1)
        if (issueKey != null && issues[issueKey] == null) return 404 to mapOf("errorMessages" to listOf("Issue does not exist or you do not have permission to see it."))
        return when {
            path == "/rest/api/2/myself" -> 200 to (if (cloud) mapOf("accountId" to "acc-dev-1", "displayName" to "Dev One", "emailAddress" to email)
                else mapOf("name" to "dev1", "key" to "dev1", "displayName" to "Dev One", "emailAddress" to email))
            path == "/rest/api/3/search/jql" && cloud && enhancedSearch -> {
                val all = query(q["jql"].orEmpty())
                val start = q["nextPageToken"]?.toInt() ?: 0
                val size = q["maxResults"]?.toInt() ?: 50
                val page = all.drop(start).take(size)
                val last = start + page.size >= all.size
                200 to (mapOf("issues" to page.map { issueJson(it.first, it.second, true) }, "isLast" to last) +
                    (if (last) emptyMap() else mapOf("nextPageToken" to "${start + page.size}")))
            }
            path == "/rest/api/2/search" && cloud && enhancedSearch -> 410 to mapOf("errorMessages" to listOf("The requested API has been removed. Please migrate to /rest/api/3/search/jql."))
            path == "/rest/api/2/search" || Regex("^/rest/agile/1.0/board/[^/]+/issue$").matches(path) -> {
                val board = Regex("^/rest/agile/1.0/board/([^/]+)/issue$").find(path)?.groupValues?.get(1)
                if (board != null && board != "7") return 404 to mapOf("errorMessages" to listOf("The requested board cannot be viewed because it either does not exist or you do not have permission to view it."))
                val all = query(q["jql"].orEmpty())
                val start = q["startAt"]?.toInt() ?: 0
                val size = q["maxResults"]?.toInt() ?: 50
                200 to mapOf("startAt" to start, "maxResults" to size, "total" to all.size,
                    "issues" to all.drop(start).take(size).map { issueJson(it.first, it.second, false) })
            }
            path == "/rest/api/2/status" -> 200 to statuses.keys.map { status(it) }
            Regex("^/rest/api/2/project/[^/]+/statuses$").matches(path) -> {
                if (!path.contains("/ABC/")) 404 to mapOf("errorMessages" to listOf("No project could be found with key 'X'."))
                else 200 to listOf(mapOf("name" to "Story", "statuses" to statuses.keys.map { status(it) }),
                    mapOf("name" to "Bug", "statuses" to statuses.keys.take(3).map { status(it) }))
            }
            path == "/rest/api/2/field" -> 200 to listOf(
                mapOf("id" to "summary", "name" to "Summary", "custom" to false, "schema" to mapOf("type" to "string")),
                mapOf("id" to "customfield_10010", "name" to "Reviewers", "custom" to true, "schema" to mapOf("type" to "array", "items" to "user")),
                mapOf("id" to "customfield_10020", "name" to "Tech lead", "custom" to true, "schema" to mapOf("type" to "user")),
                mapOf("id" to "customfield_10030", "name" to "Story points", "custom" to true, "schema" to mapOf("type" to "number")),
            )
            path == "/rest/api/2/user/search" -> 200 to (if (cloud) listOfNotNull(users[q["query"]]?.let { mapOf("accountId" to it) }) else emptyList())
            path == "/rest/api/2/issue" && method == "POST" -> {
                val f = body?.get("fields")
                val type = f?.get("issuetype")?.get("name")?.asText().orEmpty()
                val project = f?.get("project")?.get("key")?.asText().orEmpty()
                if (type in missingTypes || project.isEmpty()) return 400 to mapOf("errors" to mapOf("issuetype" to "Specify a valid issue type"))
                val parent = f?.get("parent")?.get("key")?.asText()
                if (parent != null && issues[parent] == null) return 400 to mapOf("errors" to mapOf("parent" to "Could not find issue by id or key."))
                val key = "$project-${created.incrementAndGet()}"
                issues[key] = Issue(f?.get("summary")?.asText().orEmpty(), "To Do", type, null, null, f?.get("description")?.asText().orEmpty())
                parent?.let { issues.getValue(key).fields["parent"] = it }
                201 to mapOf("id" to "1${created.get()}", "key" to key)
            }
            path == "/rest/api/2/issueLink" && method == "POST" -> {
                links += Triple(body?.get("type")?.get("name")?.asText().orEmpty(), body?.get("inwardIssue")?.get("key")?.asText().orEmpty(),
                    body?.get("outwardIssue")?.get("key")?.asText().orEmpty())
                201 to null
            }
            path.endsWith("/transitions") && method == "GET" -> {
                val i = issues.getValue(issueKey!!)
                200 to mapOf("transitions" to statuses.keys.filter { it != i.status && it !in noWayTo }.map { s ->
                    mapOf("id" to "t-${s.replace(' ', '-')}", "name" to "Go $s", "to" to status(s))
                })
            }
            path.endsWith("/transitions") && method == "POST" -> {
                val id = body?.get("transition")?.get("id")?.asText().orEmpty()
                val target = statuses.keys.firstOrNull { "t-${it.replace(' ', '-')}" == id } ?: return 400 to mapOf("errorMessages" to listOf("Transition id '$id' is not valid for this issue."))
                issues.getValue(issueKey!!).status = target
                204 to null
            }
            path.endsWith("/comment") && method == "POST" -> {
                val b = body?.get("body")
                if (b == null || !b.isTextual) return 400 to mapOf("errors" to mapOf("comment" to "Comment body can not be empty!"))
                issues.getValue(issueKey!!).comments += b.asText()
                201 to mapOf("id" to "c1", "body" to b.asText())
            }
            issueKey != null && method == "PUT" -> {
                body?.get("fields")?.fields()?.forEach { (k, v) -> issues.getValue(issueKey).fields[k] = mapper.convertValue(v, Any::class.java) }
                204 to null
            }
            issueKey != null && method == "GET" -> 200 to issueJson(issueKey, issues.getValue(issueKey), false)
            else -> 404 to mapOf("errorMessages" to listOf("no stub route $method $path"))
        }
    }

    private fun handle(ex: HttpExchange) {
        val text = ex.requestBody.readAllBytes().toString(Charsets.UTF_8)
        val body = if (text.isBlank()) null else runCatching { mapper.readTree(text) }.getOrNull()
        val path = ex.requestURI.rawPath
        val auth = ex.requestHeaders.getFirst("Authorization")
        calls += Call(ex.requestMethod, path, ex.requestURI.rawQuery.orEmpty(), body, auth)
        val (code, res) = if (auth != expectedAuth) {
            401 to "<html><body>Basic authentication with passwords is deprecated. ${auth ?: ""}</body></html>"
        } else {
            val (c, r) = route(ex.requestMethod, path, params(ex.requestURI.rawQuery), body)
            // the failure text may hold "{auth}": Jira echoing what it got (keel must never show it)
            c to (r as? Map<*, *>)?.let { m -> mapper.readValue(mapper.writeValueAsString(m).replace("{auth}", auth), Map::class.java) }.let { it ?: r }
        }
        if (code == 204 || res == null) {
            ex.sendResponseHeaders(code, -1)
            ex.close()
            return
        }
        val bytes = if (res is String) res.toByteArray() else mapper.writeValueAsBytes(res)
        ex.responseHeaders.add("Content-Type", if (res is String) "text/html" else "application/json")
        ex.sendResponseHeaders(code, bytes.size.toLong())
        ex.responseBody.use { it.write(bytes) }
    }

    companion object {
        fun start(kind: String): StubJira {
            val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
            val stub = StubJira(server, kind)
            server.createContext("/") { stub.handle(it) }
            server.start()
            return stub
        }
    }
}

/** GitHub's two pull-request calls tasks make: request reviewers, read reviews (Bearer [token]). */
class StubGitHub private constructor(private val server: HttpServer) {
    private val mapper = jacksonObjectMapper()
    val url: String get() = "http://127.0.0.1:${server.address.port}"
    val token = "gh-test-token-0099" // test fixture, keel:allow-secret

    data class Call(val method: String, val path: String, val body: JsonNode?)
    val calls = CopyOnWriteArrayList<Call>()

    /** "owner/repo#n" → reviews as GitHub lists them (oldest first). */
    val reviews = ConcurrentHashMap<String, List<Map<String, Any?>>>()

    fun review(login: String, state: String) = mapOf("user" to mapOf("login" to login), "state" to state)

    fun reset() {
        calls.clear(); reviews.clear()
    }

    private fun handle(ex: HttpExchange) {
        val text = ex.requestBody.readAllBytes().toString(Charsets.UTF_8)
        val body = if (text.isBlank()) null else runCatching { mapper.readTree(text) }.getOrNull()
        val path = ex.requestURI.path
        calls += Call(ex.requestMethod, path, body)
        val m = Regex("^/repos/([^/]+)/([^/]+)/pulls/(\\d+)/(requested_reviewers|reviews)$").find(path)
        val (code, res) = when {
            ex.requestHeaders.getFirst("Authorization") != "Bearer $token" -> 401 to mapOf("message" to "Bad credentials")
            m == null -> 404 to mapOf("message" to "Not Found")
            m.groupValues[4] == "requested_reviewers" && ex.requestMethod == "POST" -> 201 to mapOf("number" to m.groupValues[3].toInt())
            m.groupValues[4] == "reviews" -> 200 to reviews["${m.groupValues[1]}/${m.groupValues[2]}#${m.groupValues[3]}"].orEmpty()
            else -> 404 to mapOf("message" to "Not Found")
        }
        val bytes = mapper.writeValueAsBytes(res)
        ex.responseHeaders.add("Content-Type", "application/json")
        ex.sendResponseHeaders(code, bytes.size.toLong())
        ex.responseBody.use { it.write(bytes) }
    }

    companion object {
        fun start(): StubGitHub {
            val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
            val stub = StubGitHub(server)
            server.createContext("/") { stub.handle(it) }
            server.start()
            return stub
        }
    }
}
