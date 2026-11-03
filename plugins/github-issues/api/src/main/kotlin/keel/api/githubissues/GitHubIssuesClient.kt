package keel.api.githubissues

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.RestClients
import keel.api.tasks.TicketClient
import keel.api.tasks.TicketException
import keel.api.tasks.TicketMove
import org.springframework.http.HttpMethod
import org.springframework.http.MediaType
import org.springframework.web.client.ResourceAccessException
import org.springframework.web.client.RestClient
import org.springframework.web.client.RestClientResponseException
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.time.Duration

/** owner/repo on [host] (github.com, or the Enterprise host the GitHub token belongs to). */
data class RepoRef(val host: String, val owner: String, val repo: String) {
    val path get() = "$owner/$repo"
    val web get() = "https://$host/$owner/$repo"

    companion object {
        val PATH = Regex("^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})$")

        /** "owner/repo" (or its web address on [host]) → the repo; null when it is not one. */
        fun parse(raw: String?, host: String): RepoRef? {
            val s = raw?.trim()?.removeSuffix("/")?.removeSuffix(".git") ?: return null
            val path = Regex("^https?://([^/]+)/(.+)$").matchEntire(s)?.let { m ->
                if (!m.groupValues[1].equals(host, true)) return null
                m.groupValues[2]
            } ?: s
            val m = PATH.matchEntire(path) ?: return null
            if (m.groupValues[2].let { it == "." || it == ".." }) return null
            return RepoRef(host.lowercase(), m.groupValues[1], m.groupValues[2])
        }
    }
}

/** One issue as keel reads it (pull requests, which the issues API lists too, are left out). */
data class GhIssue(
    val number: Int,
    val title: String,
    val body: String,
    /** open | closed */
    val state: String,
    /** completed | not_planned | reopened | null */
    val stateReason: String?,
    val labels: List<String>,
    val assignees: List<String>,
    val url: String,
)

data class GhUser(val login: String, val name: String?)

data class GhRepo(val fullName: String, val hasIssues: Boolean, val private: Boolean, val canPush: Boolean)

/** GitHub said no, or could not be reached; [status] is GitHub's HTTP status (0 when there was no answer). */
class GitHubIssuesException(message: String, hint: String? = null, val status: Int = 0) : TicketException(message, hint)

/**
 * The GitHub REST calls the GitHub Issues plugin needs: who the token is, the repo, its issues and labels, and on one
 * issue its state, labels and comments. The token goes only to [repo]'s host ([apiBase] overrides the API root: tests,
 * or an Enterprise server; else github.com uses https://api.github.com and another host https://<host>/api/v3).
 */
class GitHubIssuesClient(
    private val token: String,
    private val repo: RepoRef,
    private val mapper: ObjectMapper,
    apiBase: String = "",
    /** keel status → "-" | open | closed | closed:not planned | label:<name> (the connection's full map) */
    private val statusMap: Map<String, String> = emptyMap(),
    /** false: [comment] does nothing (the connection's "comment on the issue" is off) */
    private val comments: Boolean = true,
    timeout: Duration = Duration.ofSeconds(20),
) : TicketClient {
    private val root = apiBase.trim().trimEnd('/').ifBlank { if (repo.host == "github.com") "https://api.github.com" else "https://${repo.host}/api/v3" }

    private val rest: RestClient by lazy {
        RestClients.builder(Duration.ofSeconds(8), timeout)
            .defaultHeader("Authorization", "Bearer $token")
            .defaultHeader("Accept", "application/vnd.github+json")
            .defaultHeader("X-GitHub-Api-Version", "2022-11-28")
            .build()
    }

    /** The labels keel uses for statuses (the map's label:<name> values). */
    private val statusLabels: List<String> = statusMap.values.mapNotNull { labelOf(it) }.distinct()

    // ---- reads ---------------------------------------------------------------------------------

    fun me(): GhUser {
        val n = call("reading who the token belongs to") { get("/user") } ?: throw GitHubIssuesException("GitHub did not say who the token belongs to.")
        return GhUser(n.path("login").asText(""), n.path("name").asText("").ifBlank { null })
    }

    fun repository(): GhRepo {
        val n = call("reading ${repo.path}") { get("/repos/${repo.path}") } ?: throw GitHubIssuesException("GitHub did not answer for ${repo.path}.")
        return GhRepo(n.path("full_name").asText(repo.path), n.path("has_issues").asBoolean(true), n.path("private").asBoolean(false),
            n.path("permissions").path("push").asBoolean(false) || n.path("permissions").path("triage").asBoolean(false))
    }

    /**
     * The open issues to follow: [assignee] (a login, or "*" for any), or with [label], or all; at most [max] (the
     * newest first). Pull requests are left out.
     */
    fun openIssues(assignee: String? = null, label: String? = null, max: Int = 300): List<GhIssue> {
        val q = buildList {
            add("state=open")
            add("sort=updated")
            add("direction=desc")
            add("per_page=100")
            assignee?.let { add("assignee=${enc(it)}") }
            label?.let { add("labels=${enc(it)}") }
        }.joinToString("&")
        val out = mutableListOf<GhIssue>()
        var page = 1
        while (out.size < max && page <= 5) {
            val list = call("listing the issues of ${repo.path}") { get("/repos/${repo.path}/issues?$q&page=$page") } ?: break
            if (!list.isArray || list.isEmpty) break
            list.forEach { n -> if (!n.has("pull_request")) out += issue(n) }
            if (list.size() < 100) break
            page++
        }
        return out.take(max)
    }

    fun issue(number: Int): GhIssue =
        issue(call("reading #$number") { get("/repos/${repo.path}/issues/$number") } ?: throw GitHubIssuesException("GitHub did not answer for #$number."))

    fun labels(): List<String> {
        val list = call("reading the labels of ${repo.path}") { get("/repos/${repo.path}/labels?per_page=100") } ?: return emptyList()
        return list.mapNotNull { it.path("name").asText("").ifBlank { null } }
    }

    // ---- the Tasks plugin's ticket client --------------------------------------------------------

    /**
     * Moves the issue to [target]: open, closed, closed:not planned, or label:<name> (open, with that one of keel's
     * status labels). The answer's from/to are [status] values, the same words a sync reads.
     */
    override fun transitionTo(key: String, target: String): TicketMove {
        val n = number(key)
        val before = issue(n)
        val from = status(before)
        val want = target.trim()
        val label = labelOf(want)
        when {
            label != null -> {
                if (before.state == "closed") patch(n, mapOf("state" to "open"))
                (statusLabels.filter { it != label && before.labels.any { l -> l.equals(it, true) } }).forEach { removeLabel(n, it) }
                if (before.labels.none { it.equals(label, true) }) addLabel(n, label)
            }
            want.equals(OPEN, true) -> {
                if (before.state == "closed") patch(n, mapOf("state" to "open"))
                statusLabels.filter { before.labels.any { l -> l.equals(it, true) } }.forEach { removeLabel(n, it) }
            }
            want.equals(CLOSED, true) || want.equals("$CLOSED:completed", true) -> {
                if (before.state != "closed" || before.stateReason == "not_planned") patch(n, mapOf("state" to "closed", "state_reason" to "completed"))
            }
            want.equals(NOT_PLANNED, true) -> {
                if (before.state != "closed" || before.stateReason != "not_planned") patch(n, mapOf("state" to "closed", "state_reason" to "not_planned"))
            }
            else -> throw GitHubIssuesException("\"$target\" is not a GitHub issue state",
                "Use open, closed, closed:not planned or label:<name> (Settings › Connections › GitHub Issues).")
        }
        val to = when {
            label != null -> "$LABEL$label"
            want.equals("$CLOSED:completed", true) -> CLOSED
            else -> want.lowercase()
        }
        return Move(from != to, from, to)
    }

    override fun comment(key: String, text: String) {
        if (!comments) return
        val n = number(key)
        call("commenting on #$n") { send(HttpMethod.POST, "/repos/${repo.path}/issues/$n/comments", mapOf("body" to text.take(60_000))) }
    }

    override fun setUsers(key: String, fieldId: String, users: List<String>) =
        throw GitHubIssuesException("A GitHub issue has no reviewer field: GitHub asks the reviewers on the pull request.")

    /** The issue's newest comments as "<login>: <text>", oldest first. */
    override fun comments(key: String, max: Int): List<String> {
        val n = number(key)
        val list = call("reading the comments of #$n") { get("/repos/${repo.path}/issues/$n/comments?per_page=100") } ?: return emptyList()
        return list.map { "${it.path("user").path("login").asText("?")}: ${it.path("body").asText("").trim().take(2000)}" }.takeLast(max)
    }

    /** The issue's state in the words keel keeps: closed, closed:not planned, label:<status label> or open. */
    fun status(i: GhIssue): String = when {
        i.state == "closed" -> if (i.stateReason == "not_planned") NOT_PLANNED else CLOSED
        else -> statusLabels.firstOrNull { s -> i.labels.any { it.equals(s, true) } }?.let { "$LABEL$it" } ?: OPEN
    }

    // ---- plumbing -------------------------------------------------------------------------------

    private data class Move(override val moved: Boolean, override val from: String?, override val to: String) : TicketMove

    private fun patch(n: Int, body: Map<String, Any?>) {
        call("changing #$n") { send(HttpMethod.PATCH, "/repos/${repo.path}/issues/$n", body) }
    }

    private fun addLabel(n: Int, label: String) {
        call("labelling #$n") { send(HttpMethod.POST, "/repos/${repo.path}/issues/$n/labels", mapOf("labels" to listOf(label))) }
    }

    private fun removeLabel(n: Int, label: String) {
        try {
            call("taking a label off #$n") { send(HttpMethod.DELETE, "/repos/${repo.path}/issues/$n/labels/${seg(label)}", null) }
        } catch (e: GitHubIssuesException) {
            if (e.status != 404) throw e               // it was not on the issue (any more)
        }
    }

    private fun issue(n: JsonNode) = GhIssue(
        number = n.path("number").asInt(),
        title = n.path("title").asText(""),
        body = n.path("body").asText("").let { if (n.path("body").isNull) "" else it },
        state = n.path("state").asText("open"),
        stateReason = n.path("state_reason").takeIf { !it.isNull && !it.isMissingNode }?.asText(),
        labels = n.path("labels").mapNotNull { l -> (if (l.isTextual) l.asText() else l.path("name").asText("")).ifBlank { null } },
        assignees = n.path("assignees").mapNotNull { it.path("login").asText("").ifBlank { null } }
            .ifEmpty { listOfNotNull(n.path("assignee").path("login").asText("").ifBlank { null }) },
        url = n.path("html_url").asText("${repo.web}/issues/${n.path("number").asInt()}"),
    )

    private fun get(path: String): JsonNode? = rest.get().uri(URI.create(root + path)).retrieve().body(JsonNode::class.java)

    private fun send(method: HttpMethod, path: String, body: Any?): JsonNode? {
        val r = rest.method(method).uri(URI.create(root + path))
        if (body != null) r.contentType(MediaType.APPLICATION_JSON).body(mapper.valueToTree<JsonNode>(body))
        return r.retrieve().body(JsonNode::class.java)
    }

    private fun call(what: String, block: () -> JsonNode?): JsonNode? = try {
        block()
    } catch (e: RestClientResponseException) {
        val n = e.statusCode.value()
        val said = runCatching { mapper.readTree(e.responseBodyAsString).get("message")?.asText() }.getOrNull()
        val (msg, hint) = when (n) {
            401 -> "GitHub refused the token (401) while $what." to "Sign in to GitHub again in Settings › Connections › GitHub."
            403 -> "GitHub did not allow $what (403)${said?.let { ": $it" } ?: "."}" to
                "The token needs access to ${repo.path} and its issues (a fine-grained token: Issues read and write)."
            404 -> "GitHub did not find it (404) while $what." to "Check the repository name, and that the token can see ${repo.path}."
            410 -> "Issues are turned off for ${repo.path} (410)." to "Turn them on in the repository's settings on GitHub."
            422 -> "GitHub refused it (422) while $what${said?.let { ": $it" } ?: "."}" to null
            else -> "GitHub answered $n while $what${said?.let { ": $it" } ?: "."}" to null
        }
        throw GitHubIssuesException(clean(msg), hint, n)
    } catch (e: ResourceAccessException) {
        throw GitHubIssuesException("Could not reach GitHub while $what: ${e.mostSpecificCause.javaClass.simpleName}", "Check the network, then Sync now.")
    }

    private fun clean(s: String) = if (token.length >= 4) s.replace(token, "***") else s

    companion object {
        const val OPEN = "open"
        const val CLOSED = "closed"
        const val NOT_PLANNED = "closed:not planned"
        const val LABEL = "label:"
        val KEY = Regex("^#(\\d{1,9})$")

        fun labelOf(target: String): String? =
            target.trim().takeIf { it.startsWith(LABEL, ignoreCase = true) }?.substring(LABEL.length)?.trim()?.ifEmpty { null }

        fun number(key: String): Int = KEY.matchEntire(key.trim())?.groupValues?.get(1)?.toInt()
            ?: throw GitHubIssuesException("\"$key\" is not a GitHub issue", "Like #12.")

        private fun enc(s: String) = URLEncoder.encode(s, StandardCharsets.UTF_8)
        private fun seg(s: String) = URLEncoder.encode(s, StandardCharsets.UTF_8).replace("+", "%20")
    }
}
