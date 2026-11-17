package keel.api.jira

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.JsonNodeFactory
import keel.api.common.RestClients
import keel.api.tasks.TicketClient
import keel.api.tasks.TicketException
import keel.api.tasks.TicketMove
import org.springframework.http.HttpStatusCode
import org.springframework.http.MediaType
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
 * The Tasks plugin sees it as a [TicketException].
 */
class JiraException(val kind: String, val status: Int?, message: String, hint: String? = null) : TicketException(message, hint)

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
    /** 0.18.52 the assignee's account id (Cloud) or user name (Server), and the ticket's labels (KeelBot ownership). */
    val assigneeId: String? = null,
    val labels: List<String> = emptyList(),
)

data class JiraStatus(val name: String, val category: String?)
data class JiraTransition(val id: String, val name: String, val to: String, val category: String?)
data class JiraIssueType(val name: String, val subtask: Boolean, val level: Int?)
data class JiraField(val id: String, val name: String, val type: String?, val items: String?, val custom: Boolean)

/** The result of moving a ticket: [moved] false when it already was in that status. */
data class JiraMove(override val moved: Boolean, override val from: String?, override val to: String, val transition: String?) : TicketMove

/**
 * Jira's plain REST API (the Tasks plugin's [TicketClient]), for Jira Cloud (`https://<site>.atlassian.net`, email + API token, Basic auth) and Jira
 * Server / Data Center (base URL + personal access token, Bearer). No SDK: Spring's RestClient on the JDK HTTP client.
 *
 * - test: GET /rest/api/2/myself (both)
 * - search: Cloud GET /rest/api/3/search/jql (the enhanced search, nextPageToken; falls back to /rest/api/2/search when
 *   a site answers 404), Server GET /rest/api/2/search (startAt/total). Cloud's v3 returns the description as ADF,
 *   which [adfText] turns into plain text.
 * - board: GET /rest/agile/1.0/board/{id}/issue (both). It (and every v2 read) gives the description as wiki markup,
 *   which [wikiText] turns into the same plain text.
 * - transitions: GET/POST /rest/api/2/issue/{key}/transitions (both)
 * - comment: POST /rest/api/2/issue/{key}/comment with a plain-text body. v2 takes plain text (wiki markup) on Cloud
 *   and Server alike; Cloud's v3 would need ADF, so keel always uses v2 for comments.
 * - issue types: GET /rest/api/2/project/{key} (its issueTypes, both)
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
) : TicketClient {
    val base = baseUrl.trim().trimEnd('/')
    val cloud get() = kind == "cloud"

    private val auth: String = if (cloud) {
        "Basic " + Base64.getEncoder().encodeToString("${email.orEmpty()}:$token".toByteArray())
    } else "Bearer $token"

    private val rest: RestClient by lazy {
        RestClients.builder(Duration.ofSeconds(8), timeout, HttpClient.Redirect.NEVER)
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

    /** The issue types a project can create (GET /rest/api/2/project/{key}, both Cloud and Server); [JiraIssueType.level] is
     *  Cloud's hierarchy level (1 = epic, 2+ = above epics, like an Initiative), null on Server. */
    fun projectIssueTypes(projectKey: String): List<JiraIssueType> =
        get("/rest/api/2/project/${enc(projectKey)}", "project $projectKey").get("issueTypes")?.mapNotNull { t ->
            t.text("name")?.let { JiraIssueType(it, t.get("subtask")?.asBoolean() == true, t.get("hierarchyLevel")?.takeIf { l -> l.isInt }?.asInt()) }
        }.orEmpty()

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

    /** 0.18.52 the ticket's newest comments, oldest first, as "<author>: <text>" (REST v2: plain text on Cloud and Server). */
    override fun comments(key: String, max: Int): List<String> {
        val n = get("/rest/api/2/issue/${enc(key)}/comment?maxResults=${max.coerceIn(1, 50)}&orderBy=-created", "the comments of $key")
        return n.get("comments")?.mapNotNull { c ->
            val body = c.get("body")?.let { if (it.isTextual) it.asText() else adfText(it) }?.trim()?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
            val who = c.get("author")?.let { it.text("displayName") ?: it.text("name") } ?: "?"
            "$who: ${body.take(1000)}"
        }.orEmpty().take(max).reversed()
    }

    /** 0.18.52 users whose name or email matches [query] (the KeelBot account picker). */
    fun findUsers(query: String, max: Int = 20): List<JiraUser> {
        val q = if (cloud) "query=${enc(query)}" else "username=${enc(query)}"
        return get("/rest/api/2/user/search?$q&maxResults=${max.coerceIn(1, 50)}", "the user search").mapNotNull { u ->
            val name = u.text("displayName") ?: u.text("name") ?: return@mapNotNull null
            JiraUser(name, accountId = u.text("accountId"), username = u.text("name"), email = u.text("emailAddress"))
        }
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
    override fun transitionTo(key: String, target: String): JiraMove {
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

    /** 0.34 a sub-task under [parentKey], with the project's sub-task issue type (Sub-task, Subtask, ...). Returns its key. */
    override fun createSubtask(parentKey: String, title: String, description: String): String {
        val project = parentKey.substringBeforeLast('-')
        val type = projectIssueTypes(project).firstOrNull { it.subtask }?.name
            ?: throw JiraException("bad_request", null, "The Jira project $project has no sub-task issue type.",
                "Add one in Jira (Project settings › Issue types), or keep the parts in keel only.")
        return createIssue(mapOf("project" to mapOf("key" to project), "parent" to mapOf("key" to parentKey),
            "issuetype" to mapOf("name" to type), "summary" to title.take(250), "description" to description.take(30_000)))
    }

    /** v0.13.0 a link between two tickets, by Jira's link names: type "Blocks" = [inward] is blocked by [outward]. */
    fun linkIssues(type: String, inward: String, outward: String) {
        send("POST", "/rest/api/2/issueLink", mapOf("type" to mapOf("name" to type), "inwardIssue" to mapOf("key" to inward),
            "outwardIssue" to mapOf("key" to outward)), "a link between $inward and $outward")
    }

    /** A plain-text comment (REST v2 on Cloud and Server). */
    override fun comment(key: String, text: String) {
        send("POST", "/rest/api/2/issue/${enc(key)}/comment", mapOf("body" to text), "ticket $key")
    }

    /** Sets one field: users for a user picker (single or multi), else the names joined as text. */
    override fun setUsers(key: String, fieldId: String, users: List<String>) {
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
                // REST v2 and the agile API (board) give Jira wiki markup: the same readable text as the ADF path
                desc.isTextual -> wikiText(desc.asText())
                else -> adfText(desc)
            }.trim().take(20_000),
            status = f.get("status")?.text("name") ?: "",
            category = f.get("status")?.get("statusCategory")?.text("key"),
            type = f.get("issuetype")?.text("name"),
            assignee = f.get("assignee")?.let { it.text("displayName") ?: it.text("name") },
            priority = f.get("priority")?.text("name"),
            updated = f.text("updated"),
            assigneeId = f.get("assignee")?.let { it.text("accountId") ?: it.text("name") },
            labels = f.get("labels")?.mapNotNull { l -> l.asText().takeIf { it.isNotBlank() } }.orEmpty(),
        )
    }

    companion object {
        const val FIELDS = "summary,status,issuetype,assignee,priority,description,updated,labels"

        private fun enc(s: String) = URLEncoder.encode(s, Charsets.UTF_8).replace("+", "%20")
        private fun JsonNode.text(k: String): String? = get(k)?.takeIf { !it.isNull }?.asText()?.takeIf { it.isNotEmpty() }

        private val WIKI_BLOCK = Regex("""\{(code|noformat)(?::[^}]*)?\}([\s\S]*?)\{\1\}""")
        private val WIKI_HEADING = Regex("""^(?:h[1-6]|bq)\.\s+(.*)$""")
        private val WIKI_LIST = Regex("""^([*#-]+)\s+(.*)$""")
        private val WIKI_RULE = Regex("""^-{4,}$""")
        private val WIKI_MACRO = Regex("""\{(?:quote|panel|color|section|column)(?::[^}]*)?\}""")
        private val WIKI_LINK = Regex("""\[([^|\[\]]+)\|([^\[\]]+)]""")
        private val WIKI_URL = Regex("""\[((?:https?|mailto|ftp):[^\[\]|]+)]""")
        private val WIKI_MENTION = Regex("""\[~([^\[\]]+)]""")
        private val WIKI_MONO = Regex("""\{\{(.+?)}}""")
        private val WIKI_BOLD = Regex("""(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])""")
        private val WIKI_ITALIC = Regex("""(?<![\w_])_(\S(?:.*?\S)?)_(?![\w_])""")
        private val WIKI_UNDER = Regex("""(?<![\w+])\+(\S(?:.*?\S)?)\+(?![\w+])""")

        /**
         * Jira wiki markup (REST v2 and the agile board API) → the same plain text [adfText] gives: no `*bold*`/`_italic_`
         * marks, headings (`h1.`–`h6.`) as plain lines, list items (`*`, `#`, `-`) as "- " (two spaces per extra level),
         * `{code}`/`{noformat}` blocks as their text, links `[text|url]` as "text (url)", tables as "a | b".
         */
        fun wikiText(wiki: String): String {
            val blocks = mutableListOf<String>()
            val text = WIKI_BLOCK.replace(wiki.replace("\r\n", "\n")) { m ->
                blocks += m.groupValues[2].trim('\n')
                "\uE000${blocks.size - 1}\uE000"
            }
            fun inline(s: String): String {
                var t = s
                t = WIKI_MACRO.replace(t, "")
                t = WIKI_MENTION.replace(t) { "@" + it.groupValues[1].removePrefix("accountid:") }
                t = WIKI_LINK.replace(t) { m ->
                    val (label, url) = m.groupValues[1].trim() to m.groupValues[2].trim()
                    if (label == url) url else "$label ($url)"
                }
                t = WIKI_URL.replace(t) { it.groupValues[1] }
                t = WIKI_MONO.replace(t) { it.groupValues[1] }
                t = WIKI_BOLD.replace(t) { it.groupValues[1] }
                t = WIKI_ITALIC.replace(t) { it.groupValues[1] }
                t = WIKI_UNDER.replace(t) { it.groupValues[1] }
                return t.replace("\\\\", "\n")
            }
            val lines = text.split('\n').map { raw ->
                val line = raw.trim()
                val heading = WIKI_HEADING.find(line)
                val item = WIKI_LIST.find(line)
                when {
                    WIKI_RULE.matches(line) -> ""
                    heading != null -> inline(heading.groupValues[1])
                    item != null -> "  ".repeat(item.groupValues[1].length - 1) + "- " + inline(item.groupValues[2])
                    line.startsWith("|") -> inline(line).trim('|').split(Regex("""\|\|?""")).joinToString(" | ") { it.trim() }
                    else -> inline(raw.trimEnd())
                }
            }
            val joined = lines.joinToString("\n").replace(Regex("\uE000(\\d+)\uE000")) { blocks[it.groupValues[1].toInt()] }
            return joined.replace(Regex("\n{3,}"), "\n\n").trim()
        }

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
