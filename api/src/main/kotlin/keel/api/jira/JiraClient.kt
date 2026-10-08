package keel.api.jira

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.JsonNodeFactory
import org.springframework.http.HttpStatusCode
import org.springframework.http.MediaType
import org.springframework.http.client.JdkClientHttpRequestFactory
import org.springframework.web.client.ResourceAccessException
import org.springframework.web.client.RestClient
import org.springframework.web.client.RestClientResponseException
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.time.Duration
import java.util.Base64

/**
 * Why a Jira call failed, in words a person can act on. [kind]: auth (401), forbidden (403), not_found (404),
 * bad_request (400/409/422), server (5xx and others), network (no answer). The message never holds the token.
 */
class JiraException(val kind: String, val status: Int?, override val message: String, val hint: String? = null) : RuntimeException(message)

data class JiraUser(val name: String, val accountId: String? = null, val username: String? = null, val email: String? = null)

data class JiraIssue(
    val key: String,
    val summary: String,
    val description: String,
    val status: String,
    /** new | indeterminate | done (Jira's status category). */
    val category: String?,
    val type: String?,
    val assignee: String?,
    val priority: String?,
    val updated: String?,
)

data class JiraStatus(val name: String, val category: String?)
data class JiraTransition(val id: String, val name: String, val to: String, val category: String?)
data class JiraField(val id: String, val name: String, val type: String?, val items: String?, val custom: Boolean)

/** The result of moving a ticket: [moved] false when it already was in that status. */
data class JiraMove(val moved: Boolean, val from: String?, val to: String, val transition: String?)

/**
 * Jira's plain REST API, for Jira Cloud (`https://<site>.atlassian.net`, email + API token, Basic auth) and Jira
 * Server / Data Center (base URL + personal access token, Bearer). No SDK: Spring's RestClient on the JDK HTTP client.
 *
 * - test: GET /rest/api/2/myself (both)
 * - search: Cloud GET /rest/api/3/search/jql (the enhanced search, nextPageToken; falls back to /rest/api/2/search when
 *   a site answers 404), Server GET /rest/api/2/search (startAt/total). Cloud's v3 returns the description as ADF,
 *   which [adfText] turns into plain text.
 * - board: GET /rest/agile/1.0/board/{id}/issue (both)
 * - transitions: GET/POST /rest/api/2/issue/{key}/transitions (both)
 * - comment: POST /rest/api/2/issue/{key}/comment with a plain-text body. v2 takes plain text (wiki markup) on Cloud
 *   and Server alike; Cloud's v3 would need ADF, so keel always uses v2 for comments.
 * - fields: GET /rest/api/2/field; a field is set with PUT /rest/api/2/issue/{key} (user pickers: accountId on Cloud,
 *   name on Server).
 */
class JiraClient(
    val kind: String,
    baseUrl: String,
    private val email: String?,
    private val token: String,
    private val mapper: ObjectMapper,
    timeout: Duration = Duration.ofSeconds(20),
) {
    val base = baseUrl.trim().trimEnd('/')
    val cloud get() = kind == "cloud"

    private val auth: String = if (cloud) {
        "Basic " + Base64.getEncoder().encodeToString("${email.orEmpty()}:$token".toByteArray())
    } else "Bearer $token"

    private val rest: RestClient by lazy {
        val http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(8))
            .followRedirects(HttpClient.Redirect.NEVER).build()
        RestClient.builder().requestFactory(JdkClientHttpRequestFactory(http).apply { setReadTimeout(timeout) })
            .defaultHeader("Authorization", auth)
            .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
            // Jira Server refuses some writes without it (XSRF check) when the call does not look like an API client.
            .defaultHeader("X-Atlassian-Token", "no-check")
            .build()
    }

    // ---- reads ---------------------------------------------------------------------------------

    fun myself(): JiraUser {
        val n = get("/rest/api/2/myself", "your Jira user")
        return JiraUser(
            name = n.text("displayName") ?: n.text("name") ?: n.text("emailAddress") ?: "?",
            accountId = n.text("accountId"), username = n.text("name"), email = n.text("emailAddress"),
        )
    }

    /** Issues for a JQL query, at most [max]. */
    fun search(jql: String, max: Int = 200): List<JiraIssue> {
        if (cloud) {
            try {
                return searchCloud(jql, max)
            } catch (e: JiraException) {
                // An older Cloud site (or a proxy) without the enhanced search: the classic endpoint still answers there.
                if (e.status != 404) throw e
            }
        }
        return searchClassic(jql, max)
    }

    private fun searchCloud(jql: String, max: Int): List<JiraIssue> {
        val out = mutableListOf<JiraIssue>()
        var next: String? = null
        do {
            val page = minOf(50, max - out.size)
            val q = "jql=${enc(jql)}&maxResults=$page&fields=$FIELDS" + (next?.let { "&nextPageToken=${enc(it)}" } ?: "")
            val n = get("/rest/api/3/search/jql?$q", "the search")
            n.get("issues")?.forEach { out += issue(it) }
            next = n.text("nextPageToken")?.takeIf { n.get("isLast")?.asBoolean() != true }
        } while (next != null && out.size < max)
        return out
    }

    private fun searchClassic(jql: String, max: Int): List<JiraIssue> = paged(max) { start, page ->
        get("/rest/api/2/search?jql=${enc(jql)}&startAt=$start&maxResults=$page&fields=$FIELDS", "the search")
    }

    /** A board's issues (Jira Software's agile API), optionally filtered by JQL. */
    fun boardIssues(boardId: String, jql: String?, max: Int = 200): List<JiraIssue> = paged(max) { start, page ->
        val filter = jql?.takeIf { it.isNotBlank() }?.let { "&jql=${enc(it)}" } ?: ""
        get("/rest/agile/1.0/board/${enc(boardId)}/issue?startAt=$start&maxResults=$page&fields=$FIELDS$filter", "board $boardId")
    }

    private fun paged(max: Int, page: (Int, Int) -> JsonNode): List<JiraIssue> {
        val out = mutableListOf<JiraIssue>()
        var start = 0
        while (out.size < max) {
            val size = minOf(50, max - out.size)
            val n = page(start, size)
            val issues = n.get("issues")?.toList().orEmpty()
            issues.forEach { out += issue(it) }
            start += issues.size
            val total = n.get("total")?.asInt() ?: start
            if (issues.isEmpty() || start >= total) break
        }
        return out
    }

    fun issue(key: String): JiraIssue = issue(get("/rest/api/2/issue/${enc(key)}?fields=$FIELDS", "ticket $key"))

    fun projectStatuses(projectKey: String): List<JiraStatus> {
        val n = get("/rest/api/2/project/${enc(projectKey)}/statuses", "project $projectKey")
        val seen = linkedMapOf<String, JiraStatus>()
        n.forEach { type ->
            type.get("statuses")?.forEach { s ->
                val name = s.text("name") ?: return@forEach
                seen.putIfAbsent(name.lowercase(), JiraStatus(name, s.get("statusCategory")?.text("key")))
            }
        }
        return seen.values.toList()
    }

    /** Every status of the site (when no project key is set). */
    fun allStatuses(): List<JiraStatus> =
        get("/rest/api/2/status", "the status list").mapNotNull { s ->
            s.text("name")?.let { JiraStatus(it, s.get("statusCategory")?.text("key")) }
        }.distinctBy { it.name.lowercase() }

    fun transitions(key: String): List<JiraTransition> =
        get("/rest/api/2/issue/${enc(key)}/transitions", "ticket $key").get("transitions")?.mapNotNull { t ->
            val id = t.text("id") ?: return@mapNotNull null
            JiraTransition(id, t.text("name") ?: id, t.get("to")?.text("name") ?: t.text("name") ?: id,
                t.get("to")?.get("statusCategory")?.text("key"))
        }.orEmpty()

    fun fields(): List<JiraField> = get("/rest/api/2/field", "the field list").mapNotNull { f ->
        val id = f.text("id") ?: return@mapNotNull null
        JiraField(id, f.text("name") ?: id, f.get("schema")?.text("type"), f.get("schema")?.text("items"), f.get("custom")?.asBoolean() == true)
    }

    /** Cloud user pickers want an accountId: an email address is looked up; anything else is taken as the id. */
    fun userRef(who: String): Map<String, String> {
        if (!cloud) return mapOf("name" to who)
        if ("@" !in who) return mapOf("accountId" to who)
        val found = get("/rest/api/2/user/search?query=${enc(who)}", "the user $who").firstOrNull()?.text("accountId")
            ?: throw JiraException("not_found", 404, "Jira has no user with the email $who.", "Use the person's Atlassian account id instead.")
        return mapOf("accountId" to found)
    }

    // ---- writes --------------------------------------------------------------------------------

    /**
     * Moves [key] to [target]: a status name or a transition name. Nothing happens when the ticket is already there.
     * Fails with the transitions it could take when none leads there.
     */
    fun transitionTo(key: String, target: String): JiraMove {
        val now = runCatching { issue(key).status }.getOrNull()
        if (now != null && now.equals(target, ignoreCase = true)) return JiraMove(false, now, now, null)
        val list = transitions(key)
        val t = list.firstOrNull { it.to.equals(target, ignoreCase = true) } ?: list.firstOrNull { it.name.equals(target, ignoreCase = true) }
            ?: throw JiraException("bad_request", null,
                "Jira offers no transition from ${now?.let { "\"$it\"" } ?: "the ticket's status"} to \"$target\".",
                if (list.isEmpty()) "This account may not move the ticket." else "It can go to: ${list.joinToString { it.to }}. Fix the status mapping in Connections › Jira.")
        send("POST", "/rest/api/2/issue/${enc(key)}/transitions", mapOf("transition" to mapOf("id" to t.id)), "ticket $key")
        return JiraMove(true, now, t.to, t.name)
    }

    /** v0.13.0 a new ticket (REST v2): `fields` as Jira wants them (project, issuetype, summary, ...). Returns its key. */
    fun createIssue(fields: Map<String, Any?>): String {
        val out = send("POST", "/rest/api/2/issue", mapOf("fields" to fields), "a new ticket")
        return out.get("key")?.asText()?.takeIf { it.isNotBlank() }
            ?: throw JiraException("bad_request", null, "Jira created no ticket.", "Check the project key and the issue type.")
    }

    /** v0.13.0 a link between two tickets, by Jira's link names: type "Blocks" = [inward] is blocked by [outward]. */
    fun linkIssues(type: String, inward: String, outward: String) {
        send("POST", "/rest/api/2/issueLink", mapOf("type" to mapOf("name" to type), "inwardIssue" to mapOf("key" to inward),
            "outwardIssue" to mapOf("key" to outward)), "a link between $inward and $outward")
    }

    /** A plain-text comment (REST v2 on Cloud and Server). */
    fun comment(key: String, text: String) {
        send("POST", "/rest/api/2/issue/${enc(key)}/comment", mapOf("body" to text), "ticket $key")
    }

    /** Sets one field: users for a user picker (single or multi), else the names joined as text. */
    fun setUsers(key: String, fieldId: String, users: List<String>) {
        val field = fields().firstOrNull { it.id == fieldId }
            ?: throw JiraException("not_found", 404, "Jira has no field $fieldId.", "Pick the reviewer field again in Connections › Jira.")
        val value: Any = when {
            field.type == "array" && field.items == "user" -> users.map { userRef(it) }
            field.type == "user" -> userRef(users.first())
            field.type == "array" -> users
            else -> users.joinToString(", ")
        }
        send("PUT", "/rest/api/2/issue/${enc(key)}", mapOf("fields" to mapOf(fieldId to value)), "ticket $key")
    }

    // ---- transport -----------------------------------------------------------------------------

    private fun get(path: String, what: String): JsonNode = call(what) {
        rest.get().uri(uri(path)).retrieve().body(JsonNode::class.java) ?: JsonNodeFactory.instance.nullNode()
    }

    private fun send(method: String, path: String, body: Any, what: String): JsonNode = call(what) {
        val spec = if (method == "PUT") rest.put() else rest.post()
        spec.uri(uri(path)).contentType(MediaType.APPLICATION_JSON).body(mapper.valueToTree<JsonNode>(body))
            .retrieve().body(JsonNode::class.java) ?: JsonNodeFactory.instance.nullNode()
    }

    /** The paths are encoded here already ([enc]); a URI keeps RestClient from encoding them again (%20 → %2520). */
    private fun uri(path: String): URI = URI.create(base + path)

    private fun call(what: String, block: () -> JsonNode): JsonNode {
        try {
            return block()
        } catch (e: RestClientResponseException) {
            throw failure(e.statusCode, e.responseBodyAsString, e.responseHeaders?.getFirst("X-Seraph-LoginReason"), what)
        } catch (e: ResourceAccessException) {
            throw JiraException("network", null, clean("Could not reach Jira at $base: ${netWhy(e.mostSpecificCause)}."),
                "Check the URL and that this machine can reach it.")
        } catch (e: JiraException) {
            throw e
        } catch (e: Exception) {
            throw JiraException("network", null, clean("Jira call failed: ${e.javaClass.simpleName}"), null)
        }
    }

    /** A network failure in words: refused, unknown host, timed out, TLS. */
    private fun netWhy(e: Throwable): String = when (e) {
        is java.net.ConnectException, is java.nio.channels.ClosedChannelException -> "nothing answered (connection refused)"
        is java.net.UnknownHostException -> "unknown host ${e.message.orEmpty()}".trim()
        is java.net.http.HttpTimeoutException, is java.net.SocketTimeoutException -> "no answer in time"
        is javax.net.ssl.SSLException -> "the TLS (https) handshake failed${e.message?.let { ": $it" } ?: ""}"
        else -> e.javaClass.simpleName + (e.message?.let { " ($it)" } ?: "")
    }

    private fun failure(code: HttpStatusCode, body: String?, loginReason: String?, what: String): JiraException {
        val said = jiraSaid(body)
        val n = code.value()
        return when {
            n == 401 -> JiraException("auth", n, "Jira refused the login (401).",
                if (cloud) "Check the email and the API token (id.atlassian.com › Security › API tokens)." else "Check the personal access token (Profile › Personal Access Tokens).")
            n == 403 && loginReason?.contains("DENIED", ignoreCase = true) == true -> JiraException("forbidden", n,
                "Jira blocked the login (403): it wants a CAPTCHA after failed logins.", "Log in once in the browser, then test again.")
            n == 403 -> JiraException("forbidden", n, clean("Jira says this account may not use $what (403)${said?.let { ": $it" } ?: "."}"),
                "Give the account access, or use another token.")
            n == 404 -> JiraException("not_found", n, clean("Jira did not find $what (404)${said?.let { ": $it" } ?: "."}"),
                "Check the URL, the project key, the board id or the ticket key.")
            n in setOf(400, 409, 422) -> JiraException("bad_request", n, clean("Jira refused the request for $what ($n)${said?.let { ": $it" } ?: "."}"))
            n == 429 -> JiraException("server", n, "Jira asks keel to slow down (429).", "keel tries again at the next sync.")
            else -> JiraException("server", n, clean("Jira answered $n for $what${said?.let { ": $it" } ?: "."}"))
        }
    }

    /** Jira's own words from an error body: errorMessages and errors (JSON); never an HTML page. */
    private fun jiraSaid(body: String?): String? {
        if (body.isNullOrBlank() || !body.trimStart().startsWith("{")) return null
        val n = runCatching { mapper.readTree(body) }.getOrNull() ?: return null
        val parts = n.get("errorMessages")?.map { it.asText() }.orEmpty() +
            n.get("errors")?.fields()?.asSequence()?.map { (k, v) -> "$k: ${v.asText()}" }?.toList().orEmpty()
        return parts.filter { it.isNotBlank() }.joinToString("; ").take(400).ifBlank { null }
    }

    /** Belt and braces: a message never carries the token or the Basic credentials, whatever Jira echoed. */
    fun clean(text: String): String {
        var s = text
        if (token.length >= 4) s = s.replace(token, "***")
        val basic = auth.removePrefix("Basic ").removePrefix("Bearer ")
        if (basic.length >= 4) s = s.replace(basic, "***")
        return s
    }

    private fun issue(n: JsonNode): JiraIssue {
        val f = n.get("fields") ?: JsonNodeFactory.instance.objectNode()
        val desc = f.get("description")
        return JiraIssue(
            key = n.text("key") ?: "",
            summary = f.text("summary") ?: "",
            description = when {
                desc == null || desc.isNull -> ""
                desc.isTextual -> desc.asText()
                else -> adfText(desc)
            }.trim().take(20_000),
            status = f.get("status")?.text("name") ?: "",
            category = f.get("status")?.get("statusCategory")?.text("key"),
            type = f.get("issuetype")?.text("name"),
            assignee = f.get("assignee")?.let { it.text("displayName") ?: it.text("name") },
            priority = f.get("priority")?.text("name"),
            updated = f.text("updated"),
        )
    }

    companion object {
        const val FIELDS = "summary,status,issuetype,assignee,priority,description,updated"

        private fun enc(s: String) = URLEncoder.encode(s, Charsets.UTF_8).replace("+", "%20")
        private fun JsonNode.text(k: String): String? = get(k)?.takeIf { !it.isNull }?.asText()?.takeIf { it.isNotEmpty() }

        /** Atlassian Document Format (Cloud v3) → plain text: paragraphs and headings on their own lines, list items "- ". */
        fun adfText(node: JsonNode): String {
            val out = StringBuilder()
            fun walk(n: JsonNode, depth: Int) {
                when (n.get("type")?.asText()) {
                    "text" -> out.append(n.get("text")?.asText().orEmpty())
                    "hardBreak" -> out.append('\n')
                    "mention" -> out.append(n.get("attrs")?.get("text")?.asText().orEmpty())
                    "emoji" -> out.append(n.get("attrs")?.get("text")?.asText() ?: n.get("attrs")?.get("shortName")?.asText().orEmpty())
                    "inlineCard" -> out.append(n.get("attrs")?.get("url")?.asText().orEmpty())
                    "listItem" -> {
                        out.append("  ".repeat((depth - 1).coerceAtLeast(0))).append("- ")
                        n.get("content")?.forEach { walk(it, depth) }
                        if (!out.endsWith("\n")) out.append('\n')
                    }
                    "bulletList", "orderedList" -> {
                        n.get("content")?.forEach { walk(it, depth + 1) }
                    }
                    "paragraph", "heading", "codeBlock", "blockquote", "panel" -> {
                        n.get("content")?.forEach { walk(it, depth) }
                        if (depth == 0 || !out.endsWith("\n")) out.append('\n')
                    }
                    else -> n.get("content")?.forEach { walk(it, depth) }
                }
            }
            walk(node, 0)
            return out.toString().replace(Regex("\n{3,}"), "\n\n").trim()
        }
    }
}
