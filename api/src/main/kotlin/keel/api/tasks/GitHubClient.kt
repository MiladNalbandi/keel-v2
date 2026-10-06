package keel.api.tasks

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.http.MediaType
import org.springframework.http.client.JdkClientHttpRequestFactory
import org.springframework.web.client.ResourceAccessException
import org.springframework.web.client.RestClient
import org.springframework.web.client.RestClientResponseException
import java.net.URI
import java.net.http.HttpClient
import java.time.Duration

/** A pull request named by its web URL: https://github.com/<owner>/<repo>/pull/<n> (or a GitHub Enterprise host). */
data class PrRef(val host: String, val owner: String, val repo: String, val number: Int) {
    companion object {
        private val RX = Regex("^https?://([^/]+)/([^/]+)/([^/]+)/pull/(\\d+)(?:[/?#].*)?$")

        fun parse(url: String?): PrRef? {
            val m = RX.matchEntire(url?.trim() ?: return null) ?: return null
            return PrRef(m.groupValues[1].lowercase(), m.groupValues[2], m.groupValues[3], m.groupValues[4].toInt())
        }
    }
}

class GitHubException(override val message: String) : RuntimeException(message)

/** The latest decision of each reviewer that decided (APPROVED or CHANGES_REQUESTED), and who only commented. */
data class PrReviews(val approved: List<String>, val changes: List<String>, val commented: List<String>)

/**
 * The two GitHub REST calls tasks need: ask for reviewers and read the reviews. [apiBase] overrides the API root (tests,
 * or an Enterprise server); otherwise github.com uses https://api.github.com and another host https://<host>/api/v3.
 */
class GitHubClient(private val token: String, private val mapper: ObjectMapper, private val apiBase: String = "") {

    private fun root(pr: PrRef): String = apiBase.trim().trimEnd('/').ifBlank {
        if (pr.host == "github.com") "https://api.github.com" else "https://${pr.host}/api/v3"
    }

    private val rest: RestClient by lazy {
        val http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(8)).build()
        RestClient.builder().requestFactory(JdkClientHttpRequestFactory(http).apply { setReadTimeout(Duration.ofSeconds(20)) })
            .defaultHeader("Authorization", "Bearer $token")
            .defaultHeader("Accept", "application/vnd.github+json")
            .defaultHeader("X-GitHub-Api-Version", "2022-11-28")
            .build()
    }

    /** Users by login; "org/team" names a team (its slug goes to team_reviewers). */
    fun requestReviewers(pr: PrRef, logins: List<String>) {
        val (teams, users) = logins.partition { "/" in it }
        val body = mapOf("reviewers" to users, "team_reviewers" to teams.map { it.substringAfter('/') }).filterValues { it.isNotEmpty() }
        call("asking for reviewers") {
            rest.post().uri(URI.create("${root(pr)}/repos/${pr.owner}/${pr.repo}/pulls/${pr.number}/requested_reviewers"))
                .contentType(MediaType.APPLICATION_JSON).body(mapper.valueToTree<JsonNode>(body))
                .retrieve().body(JsonNode::class.java)
        }
    }

    fun reviews(pr: PrRef): PrReviews {
        val list = call("reading the reviews") {
            rest.get().uri(URI.create("${root(pr)}/repos/${pr.owner}/${pr.repo}/pulls/${pr.number}/reviews?per_page=100"))
                .retrieve().body(JsonNode::class.java)
        }
        val latest = linkedMapOf<String, String>()
        val commented = linkedSetOf<String>()
        list?.forEach { r ->
            val who = r.get("user")?.get("login")?.asText() ?: return@forEach
            when (val state = r.get("state")?.asText()) {
                "APPROVED", "CHANGES_REQUESTED", "DISMISSED" -> latest[who] = state
                "COMMENTED" -> commented += who
            }
        }
        return PrReviews(latest.filterValues { it == "APPROVED" }.keys.toList(), latest.filterValues { it == "CHANGES_REQUESTED" }.keys.toList(),
            commented.filter { it !in latest }.toList())
    }

    private fun call(what: String, block: () -> JsonNode?): JsonNode? = try {
        block()
    } catch (e: RestClientResponseException) {
        val n = e.statusCode.value()
        val said = runCatching { mapper.readTree(e.responseBodyAsString).get("message")?.asText() }.getOrNull()
        throw GitHubException(clean(when (n) {
            401 -> "GitHub refused the token (401) while $what."
            403 -> "GitHub says the token may not do that (403) while $what${said?.let { ": $it" } ?: "."}"
            404 -> "GitHub did not find the pull request (404) while $what. The token may not see this repository."
            422 -> "GitHub refused the reviewers (422)${said?.let { ": $it" } ?: "."}"
            else -> "GitHub answered $n while $what${said?.let { ": $it" } ?: "."}"
        }))
    } catch (e: ResourceAccessException) {
        throw GitHubException("Could not reach GitHub while $what: ${e.mostSpecificCause.javaClass.simpleName}")
    }

    private fun clean(s: String) = if (token.length >= 4) s.replace(token, "***") else s
}
