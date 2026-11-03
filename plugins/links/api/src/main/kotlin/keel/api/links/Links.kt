package keel.api.links

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.KvStore
import keel.api.common.Time
import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import keel.api.connections.SecretService
import keel.api.flow.FlowContributor
import keel.api.plugins.PluginService
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.net.InetAddress
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

/** What keel keeps about the link, without its key. */
data class LinearLink(val url: String = DEFAULT_URL, val who: String? = null, val at: String? = null)
data class LinearView(val linked: Boolean, val url: String, val who: String?, val at: String?, val hint: String?, val sends: String)
data class LinearBody(val token: String = "", val url: String? = null)

const val DEFAULT_URL = "https://api.linear.app/graphql"

/**
 * The Links plugin's api part: the person's Linear key. It is checked with Linear before it is kept, kept encrypted, never sent back,
 * and handed to the engine in memory with each agent call of a project that has the plugin on ([LinksKeys]). The tools are in the
 * engine part (keel_plugin_links).
 *
 *     GET    /api/plugins/links/linear    linked or not, as whom, what can go to the model company
 *     POST   /api/plugins/links/linear    {token, url?} checks it with Linear, then keeps it
 *     DELETE /api/plugins/links/linear    forgets it
 */
@Service
class LinksService(private val secrets: SecretService, private val kv: KvStore, private val mapper: ObjectMapper) {
    private val http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(8)).followRedirects(HttpClient.Redirect.NEVER).build()

    private fun link(): LinearLink = kv.get<LinearLink>(KEY) ?: LinearLink()

    fun view(): LinearView {
        val l = link()
        return LinearView(secrets.has(TOKEN), l.url, l.who, l.at, secrets.hintOf(TOKEN), SENDS)
    }

    fun save(b: LinearBody): LinearView {
        val token = b.token.trim()
        if (token.isEmpty()) throw BadRequest("Paste your Linear API key", "Linear › Settings › Security & access › Personal API keys.")
        val url = checked(b.url?.trim()?.ifEmpty { null } ?: DEFAULT_URL)
        val who = viewer(url, token)
        secrets.put(TOKEN, token)
        kv.put(KEY, LinearLink(url, who, Time.now()))
        return view()
    }

    fun forget() {
        if (secrets.has(TOKEN)) secrets.delete(TOKEN)
        kv.delete(KEY)
    }

    /** The key and address for an agent call, or nothing. */
    fun keys(): Map<String, String> {
        val token = secrets.get(TOKEN) ?: return emptyMap()
        return mapOf("links:linear:token" to token, "links:linear:url" to link().url)
    }

    /** The key goes only over https, or to a machine on this computer (a stand-in, in tests). */
    private fun checked(url: String): String {
        val uri = runCatching { URI(url) }.getOrNull()
        val host = uri?.host ?: throw BadRequest("That is not a web address")
        val local = runCatching { InetAddress.getAllByName(host).all { it.isLoopbackAddress } }.getOrDefault(false)
        if (uri.scheme != "https" && !(uri.scheme == "http" && local)) throw BadRequest("The address must start with https://", "Your key goes there, so keel never sends it unencrypted.")
        return url
    }

    /** Asks Linear who this key is. A refusal says why and nothing is kept. */
    private fun viewer(url: String, token: String): String? {
        val body = mapper.writeValueAsString(mapOf("query" to "query { viewer { name } }"))
        val request = HttpRequest.newBuilder(URI(url)).timeout(Duration.ofSeconds(15)).header("Content-Type", "application/json")
            .header("Authorization", token).POST(HttpRequest.BodyPublishers.ofString(body)).build()
        val res = try {
            http.send(request, HttpResponse.BodyHandlers.ofString())
        } catch (e: java.io.IOException) {
            throw BadRequest("Linear cannot be reached (${e.javaClass.simpleName})", "Nothing was kept.")
        }
        if (res.statusCode() == 401 || res.statusCode() == 403) throw BadRequest("Linear refused this key", "Nothing was kept. Check the key.")
        val json: JsonNode? = runCatching { mapper.readTree(res.body()) }.getOrNull()
        val name = json?.path("data")?.path("viewer")?.path("name")?.asText(null)
        if (res.statusCode() != 200 || name == null) throw BadRequest("Linear did not say who this key is (it answered ${res.statusCode()})", "Nothing was kept.")
        return name
    }

    companion object {
        const val TOKEN = "plugin.links.linear.token"
        const val KEY = "plugin.links.linear"
        const val SENDS = "What an agent reads in Linear goes to your model company: issue titles, descriptions and comments. People's names do not, unless they wrote them in the text. " +
            "A comment or a new issue goes to Linear only after your OK in the Inbox, and you see the exact words first."
    }
}

/** The key reaches the engine only for a project that has the plugin on (Control › Plugins). */
@Component
class LinksKeys(private val links: LinksService, private val plugins: PluginService) : FlowContributor {
    override fun keys(pid: String): Map<String, String> = if (plugins.on(pid, "links")) links.keys() else emptyMap()
}

@Component
class LinksConnectionKind : ConnectionKind {
    override val kind = "links-linear"
    override val title = "Linear"
    override val scope = "keel"
    override val order = 50
    override val fields = listOf(ConnectionField("token", "API key", "secret", required = true))
    override val tokens = setOf(LinksService.TOKEN)
}

@RestController
@RequestMapping("/api/plugins/links/linear")
class LinksController(private val links: LinksService) {
    @GetMapping
    fun view(): LinearView = links.view()

    @PostMapping
    fun save(@RequestBody body: LinearBody): LinearView = links.save(body)

    @DeleteMapping
    fun forget(): LinearView {
        links.forget()
        return links.view()
    }
}
