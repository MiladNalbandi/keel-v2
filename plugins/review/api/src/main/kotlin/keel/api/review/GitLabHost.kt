package keel.api.review

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
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
 * GitLab (gitlab.com or a self-hosted server) for the review window, REST v4: merge requests, their discussions (a
 * thread on a line has a position), replies, resolve, approve. A review goes out as one discussion per line comment
 * and a note with your text; Approve approves; Request changes is a note (and takes back your approval), because
 * GitLab's own "request changes" state is not in its REST API everywhere. [apiBase] overrides `<server>/api/v4`.
 */
class GitLabHost(override val ref: HostRef, private val token: String, private val mapper: ObjectMapper, apiBase: String = "") : CodeHost {

    private val root: String = apiBase.trim().trimEnd('/').ifBlank { "https://${ref.host}/api/v4" }
    private val project = "$root/projects/${enc(ref.path)}"

    private val rest: RestClient by lazy {
        val http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(8)).build()
        RestClient.builder().requestFactory(JdkClientHttpRequestFactory(http).apply { setReadTimeout(Duration.ofSeconds(30)) })
            .defaultHeader("PRIVATE-TOKEN", token)
            .build()
    }

    override fun me(): String = get("$root/user", "your GitLab user").path("username").asText().ifBlank {
        throw HostException("GitLab did not say who the token belongs to.")
    }

    override fun list(me: String): List<PrSummary> =
        get("$project/merge_requests?state=opened&order_by=updated_at&sort=desc&per_page=100", "the merge requests").map { summary(it, me) }

    override fun forBranch(branch: String, me: String): PrSummary? =
        get("$project/merge_requests?state=opened&source_branch=${enc(branch)}&per_page=5", "the merge request of $branch").firstOrNull()?.let { summary(it, me) }

    private fun summary(m: JsonNode, me: String) = PrSummary(
        number = m.path("iid").asInt(),
        title = m.path("title").asText(),
        author = m.path("author").path("username").asText(),
        branch = m.path("source_branch").asText(),
        base = m.path("target_branch").asText(),
        draft = m.path("draft").asBoolean(false) || m.path("work_in_progress").asBoolean(false),
        updatedAt = m.path("updated_at").asText(),
        url = m.path("web_url").asText(),
        reviewRequested = m.path("reviewers").any { it.path("username").asText().equals(me, true) },
        mine = m.path("author").path("username").asText().equals(me, true),
        headSha = m.path("sha").asText(null),
        assigned = m.path("assignees").any { it.path("username").asText().equals(me, true) } ||
            m.path("assignee").path("username").asText().equals(me, true),
    )

    override fun pr(number: Int): PrDetail {
        val m = get("$project/merge_requests/$number", "merge request !$number")
        return PrDetail(
            number = number,
            title = m.path("title").asText(),
            author = m.path("author").path("username").asText(),
            body = m.path("description").asText(""),
            branch = m.path("source_branch").asText(),
            base = m.path("target_branch").asText(),
            headSha = m.path("diff_refs").path("head_sha").asText(m.path("sha").asText()),
            state = when (val s = m.path("state").asText()) { "opened" -> "open"; else -> s },
            draft = m.path("draft").asBoolean(false),
            url = m.path("web_url").asText(),
            sameRepo = m.path("source_project_id").asLong() == m.path("target_project_id").asLong(),
            mergeable = (m.path("detailed_merge_status").asText(null) ?: m.path("merge_status").asText(null))?.let { it == "mergeable" || it == "can_be_merged" },
            mergeState = m.path("detailed_merge_status").asText(null) ?: m.path("merge_status").asText(null),
        )
    }

    override fun checks(pr: PrDetail): List<CheckRun> {
        val pipes = get("$project/merge_requests/${pr.number}/pipelines?per_page=1", "the pipelines")
        val p = pipes.firstOrNull() ?: return emptyList()
        val jobs = runCatching { get("$project/pipelines/${p.path("id").asLong()}/jobs?per_page=100", "the jobs") }.getOrNull()
        val list = jobs?.map { j -> CheckRun(j.path("name").asText(), state(j.path("status").asText()), j.path("web_url").asText(null)) }.orEmpty()
        return list.ifEmpty { listOf(CheckRun("pipeline", state(p.path("status").asText()), p.path("web_url").asText(null))) }
    }

    private fun state(s: String) = when (s) { "success" -> "success"; "failed" -> "failure"; "canceled" -> "cancelled"; "skipped" -> "skipped"; else -> "pending" }

    override fun decisions(number: Int): Decisions {
        val a = get("$project/merge_requests/$number/approvals", "the approvals")
        return Decisions(a.path("approved_by").map { it.path("user").path("username").asText() }, emptyList())
    }

    private fun discussions(number: Int): JsonNode = get("$project/merge_requests/$number/discussions?per_page=100", "the discussions")

    override fun threads(number: Int): List<ReviewThread> = discussions(number).mapNotNull { d ->
        val notes = d.path("notes").filter { !it.path("system").asBoolean(false) }
        val first = notes.firstOrNull() ?: return@mapNotNull null
        val pos = first.path("position")
        if (pos.isMissingNode || pos.isNull) return@mapNotNull null
        val newLine = pos.path("new_line").takeIf { it.isInt }?.asInt()
        val oldLine = pos.path("old_line").takeIf { it.isInt }?.asInt()
        ReviewThread(
            id = d.path("id").asText(),
            path = pos.path("new_path").asText(null) ?: pos.path("old_path").asText(null),
            line = newLine ?: oldLine,
            side = if (newLine == null && oldLine != null) "LEFT" else "RIGHT",
            resolved = notes.all { !it.path("resolvable").asBoolean(false) || it.path("resolved").asBoolean(false) },
            outdated = false,
            comments = notes.map { comment(it) },
            replyTo = d.path("id").asText(),
        )
    }

    override fun conversation(number: Int): List<ReviewComment> = discussions(number).flatMap { d ->
        val notes = d.path("notes").filter { !it.path("system").asBoolean(false) }
        val first = notes.firstOrNull()
        if (first == null || !(first.path("position").isMissingNode || first.path("position").isNull)) emptyList() else notes.map { comment(it) }
    }

    private fun comment(n: JsonNode) = ReviewComment(n.path("id").asText(), n.path("author").path("username").asText(), n.path("body").asText(),
        n.path("created_at").asText(), null)

    override fun reply(number: Int, thread: ReviewThread, body: String) {
        post("$project/merge_requests/$number/discussions/${enc(thread.id)}/notes", mapOf("body" to body), "your reply")
    }

    override fun resolve(number: Int, thread: ReviewThread, resolved: Boolean) {
        call(if (resolved) "resolving the discussion" else "opening the discussion again") {
            rest.put().uri(URI.create("$project/merge_requests/$number/discussions/${enc(thread.id)}?resolved=$resolved"))
                .retrieve().body(JsonNode::class.java) ?: mapper.createObjectNode()
        }
    }

    override fun submit(pr: PrDetail, event: String, body: String, comments: List<OutComment>): String? {
        val m = get("$project/merge_requests/${pr.number}", "merge request !${pr.number}")
        val refs = m.path("diff_refs")
        for (c in comments) {
            val position = linkedMapOf<String, Any?>(
                "position_type" to "text",
                "base_sha" to refs.path("base_sha").asText(),
                "start_sha" to refs.path("start_sha").asText(),
                "head_sha" to refs.path("head_sha").asText(),
                "new_path" to c.path,
                "old_path" to c.path,
            )
            if (c.side == "LEFT") position["old_line"] = c.line else position["new_line"] = c.line
            post("$project/merge_requests/${pr.number}/discussions", mapOf("body" to c.body, "position" to position), "a line comment on ${c.path}:${c.line}")
        }
        val text = when (event) {
            "REQUEST_CHANGES" -> listOf("**Changes requested.**", body).filter { it.isNotBlank() }.joinToString("\n\n")
            else -> body
        }
        if (text.isNotBlank()) post("$project/merge_requests/${pr.number}/notes", mapOf("body" to text), "your review note")
        when (event) {
            "APPROVE" -> post("$project/merge_requests/${pr.number}/approve", mapOf("sha" to pr.headSha), "your approval")
            "REQUEST_CHANGES" -> runCatching { post("$project/merge_requests/${pr.number}/unapprove", emptyMap<String, Any>(), "taking back the approval") }
        }
        return pr.url
    }

    override fun merge(pr: PrDetail, method: String, deleteBranch: Boolean): String {
        if (method == "rebase") throw HostException("keel merges a GitLab merge request with a merge commit or a squash", "Pick merge or squash.", 400)
        val out = call("merging !${pr.number}") {
            rest.put().uri(URI.create("$project/merge_requests/${pr.number}/merge")).contentType(MediaType.APPLICATION_JSON)
                .body(mapper.valueToTree<JsonNode>(mapOf("sha" to pr.headSha, "squash" to (method == "squash"), "should_remove_source_branch" to deleteBranch)))
                .retrieve().body(JsonNode::class.java) ?: mapper.createObjectNode()
        }
        if (out.path("state").asText() != "merged") throw HostException("GitLab did not merge !${pr.number} (${out.path("state").asText("unknown")})", status = 409)
        return "Merged"
    }

    override fun fetchUrl(): String = "https://${ref.host}/${ref.path}.git"

    override fun authHeader(): String = "AUTHORIZATION: basic " + Base64.getEncoder().encodeToString("oauth2:$token".toByteArray())

    override fun headRef(number: Int) = "refs/merge-requests/$number/head"

    // ---- http

    private fun get(url: String, what: String): JsonNode = call(what) {
        rest.get().uri(URI.create(url)).retrieve().body(JsonNode::class.java) ?: mapper.createObjectNode()
    }

    private fun post(url: String, body: Any, what: String): JsonNode = call(what) {
        rest.post().uri(URI.create(url)).contentType(MediaType.APPLICATION_JSON).body(mapper.valueToTree<JsonNode>(body))
            .retrieve().body(JsonNode::class.java) ?: mapper.createObjectNode()
    }

    private fun call(what: String, block: () -> JsonNode): JsonNode = try {
        block()
    } catch (e: RestClientResponseException) {
        val msg = runCatching { mapper.readTree(e.responseBodyAsString).let { it.path("message").toString().trim('"').ifBlank { it.path("error").asText() } } }
            .getOrNull().orEmpty()
        val clean = msg.replace(token, "***").take(200)
        when (e.statusCode.value()) {
            401 -> throw HostException("GitLab refused the token ($what).", "Check the GitLab token in Connections (scope api).", 409)
            403 -> throw HostException("GitLab did not allow $what: $clean", "The token needs the api scope, and your role must allow it.", 409)
            404 -> throw HostException("GitLab did not find $what.", "Check that the token can see ${ref.path}.", 404)
            405, 406 -> throw HostException("GitLab did not allow $what: $clean", "It is not mergeable yet: pipeline, approvals, threads or conflicts.", 409)
            400, 409, 422 -> throw HostException("GitLab did not accept $what: $clean", status = 409)
            else -> throw HostException("GitLab answered ${e.statusCode.value()} for $what: $clean")
        }
    } catch (e: ResourceAccessException) {
        throw HostException("Could not reach GitLab at ${URI.create(root).host} for $what.", "Check the server URL in Connections and the network of keel's container.")
    }

    private fun enc(s: String) = URLEncoder.encode(s, Charsets.UTF_8).replace("+", "%20")
}
