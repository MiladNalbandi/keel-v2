package keel.api.review

import keel.api.common.BadRequest
import keel.api.common.KvStore
import keel.api.connections.SecretService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.net.URI

data class GitLabSave(val url: String = "", val token: String? = null)
data class GitLabView(val set: Boolean, val url: String?, val host: String?, val hint: String?)

/**
 * Connections › GitLab: the server (gitlab.com or your company's) and a personal access token with the api scope. The
 * token is kept encrypted like every keel secret and never leaves keel; the URL tells which projects are on GitLab.
 */
@Service
class GitLabConnection(private val secrets: SecretService, private val kv: KvStore) {
    companion object {
        const val TOKEN = "GITLAB_TOKEN"
        const val KV = "review.gitlab"
    }

    private data class Saved(val url: String = "")

    fun url(): String? = kv.get<Saved>(KV)?.url?.ifBlank { null } ?: System.getenv("GITLAB_URL")?.ifBlank { null }

    fun host(): String? = url()?.let { runCatching { URI.create(it).host?.lowercase() }.getOrNull() }

    fun token(): String? = secrets.get(TOKEN) ?: System.getenv(TOKEN)?.takeIf { it.isNotBlank() }

    fun view(): GitLabView = GitLabView(token() != null && url() != null, url(), host(), secrets.hintOf(TOKEN))

    fun save(b: GitLabSave): GitLabView {
        val url = b.url.trim().trimEnd('/')
        val u = runCatching { URI.create(url) }.getOrNull()
        if (u == null || u.scheme !in setOf("https", "http") || u.host.isNullOrBlank()) {
            throw BadRequest("That is not a GitLab address", "Write it like https://gitlab.com or https://gitlab.yourcompany.com")
        }
        b.token?.trim()?.takeIf { it.isNotEmpty() }?.let { secrets.put(TOKEN, it) }
        if (token() == null) throw BadRequest("Add the token too", "GitLab › Preferences › Access tokens, with the api and read_user scopes.")
        kv.put(KV, Saved("${u.scheme}://${u.authority}"))
        return view()
    }

    fun clear(): GitLabView {
        if (secrets.has(TOKEN)) secrets.delete(TOKEN)
        kv.delete(KV)
        return view()
    }
}

@RestController
@RequestMapping("/api/gitlab")
class GitLabConnectionController(private val gitlab: GitLabConnection) {
    @GetMapping fun get() = gitlab.view()
    @PutMapping fun save(@RequestBody b: GitLabSave) = gitlab.save(b)
    @DeleteMapping fun clear() = gitlab.clear()
}
