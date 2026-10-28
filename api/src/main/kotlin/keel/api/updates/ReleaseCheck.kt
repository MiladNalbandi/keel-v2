package keel.api.updates

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.KvStore
import keel.api.common.Time
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.info.BuildProperties
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Service
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import java.nio.file.Paths
import java.time.Duration
import java.time.Instant
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** What a person chose in Settings › Access › Updates. channel: "stable" or "product" (keel Product, beta); null = the
 *  channel of the image keel runs. */
data class UpdateSettings(val checkDaily: Boolean = true, val channel: String? = null)

/** The newest release keel found: its version (without the v), its notes (Markdown, cut at [ReleaseCheck.NOTES_MAX]). */
data class Release(val version: String = "", val name: String? = null, val notes: String = "", val url: String? = null,
                   val publishedAt: String? = null)

/** The last check: what it found, when, and why it failed (null: it worked). Kept in the kv table. */
data class ReleaseCache(val release: Release? = null, val checkedAt: String? = null, val error: String? = null)

/** What GET /api/updates answers: the release check, the settings and the command that updates this keel. */
data class UpdatesView(
    val current: String,
    val latest: String?,
    val newer: Boolean,
    val name: String?,
    val notes: String,
    val url: String,
    val publishedAt: String?,
    val checkedAt: String?,
    val error: String?,
    val checkDaily: Boolean,
    val channel: String,
    val edition: String,
    val image: String?,
    val native: Boolean,
    val command: String,
    val commandWhy: String,
)

/** Reads the release JSON at a link. A stand-in in tests: keel never calls GitHub there. */
fun interface ReleaseSource {
    fun fetch(url: String): String
}

/**
 * keel 0.18.66 the update notice: once a day keel asks GitHub for the newest release of the repo it was built from
 * (GitHub's /releases/latest: never a draft or a pre-release), compares it with its own version and keeps the answer,
 * with the release notes, in its database. The web shows a pop-up once per new version; keel_health says it too.
 *
 *   tick (every hour) ── check every day on? ── last check > 24 h ago? ── a thread of its own ── GitHub
 *                                                                                  │
 *   GET /api/updates ◀── the kept answer (never the network) ◀───────────────────────┘
 *
 * Nothing waits for the network: the tick starts the check on its own thread and the page reads what is kept. A failed
 * check is silent; Settings › Access › Updates says "could not check: <why>". With "check every day" off keel makes no
 * network call at all (only a person's Check now does).
 * KEEL_RELEASE_REPO (owner/name) or KEEL_RELEASE_CHECK_URL (a whole link: http(s) or file:, for tests) say where.
 */
@Service
class ReleaseCheck(
    private val kv: KvStore,
    private val mapper: ObjectMapper,
    build: ObjectProvider<BuildProperties>,
    @Value("\${keel.release-repo:MiladNalbandi/keel-v2}") private val repo: String,
    @Value("\${keel.release-check-url:}") private val checkUrl: String,
    @Value("\${keel.release-check.scheduler:true}") private val scheduler: Boolean,
) {
    val version: String = build.ifAvailable?.version ?: "dev"

    /** Where the check reads. Tests put a stand-in here. */
    @Volatile var source: ReleaseSource = ReleaseSource { url -> download(url, version) }

    private val running = AtomicBoolean(false)
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "keel-release-check").apply { isDaemon = true } }

    val url: String get() = checkUrl.ifBlank { "https://api.github.com/repos/${repo.trim()}/releases/latest" }

    fun settings(): UpdateSettings = kv.get<UpdateSettings>(SETTINGS_KEY) ?: UpdateSettings()

    fun saveSettings(s: UpdateSettings): UpdateSettings {
        val channel = s.channel?.trim()?.lowercase()?.ifEmpty { null }
        if (channel != null && channel !in CHANNELS) throw BadRequest("No update channel \"${s.channel}\"", "One of: ${CHANNELS.joinToString(", ")}.")
        val saved = UpdateSettings(s.checkDaily, channel)
        kv.put(SETTINGS_KEY, saved)
        return saved
    }

    fun cache(): ReleaseCache = kv.get<ReleaseCache>(CACHE_KEY) ?: ReleaseCache()

    /** The newer version keel found, or null (none, not checked yet, or keel's own version is not a release). */
    fun newerVersion(): String? = cache().release?.version?.takeIf { Semver.newer(it, version) }

    /** Checks now, on this thread (a person's Check now; the tick calls it on its own thread). Never throws. */
    fun checkNow(): ReleaseCache {
        val before = cache()
        val next = try {
            val release = parse(mapper.readTree(source.fetch(url)))
            ReleaseCache(release, Time.now(), null)
        } catch (e: Exception) {
            // the release found before stays: a failed check only adds its reason
            ReleaseCache(before.release, Time.now(), why(e))
        }
        kv.put(CACHE_KEY, next)
        return next
    }

    /** Every hour: when "check every day" is on and the last check is older than a day, a check on its own thread. */
    @Scheduled(fixedDelayString = "\${keel.release-check.tick-ms:3600000}", initialDelayString = "\${keel.release-check.first-ms:90000}")
    fun tick() {
        if (!scheduler || !settings().checkDaily || !due(cache(), Instant.now())) return
        if (!running.compareAndSet(false, true)) return
        worker.execute {
            try { checkNow() } finally { running.set(false) }
        }
    }

    fun view(): UpdatesView {
        val c = cache()
        val s = settings()
        val r = c.release
        val edition = (System.getenv("KEEL_EDITION") ?: "full").ifBlank { "full" }
        val native = System.getenv("KEEL_NATIVE") == "1"
        val image = System.getenv("KEEL_IMAGE")?.trim()?.ifEmpty { null }
        val channel = s.channel ?: if (edition == "product") "product" else "stable"
        val latest = r?.version
        val (command, why) = Command.of(latest, channel, edition, image, native)
        return UpdatesView(
            current = version, latest = latest, newer = latest != null && Semver.newer(latest, version), name = r?.name,
            notes = r?.notes ?: "", url = r?.url ?: "https://github.com/${repo.trim()}/releases",
            publishedAt = r?.publishedAt, checkedAt = c.checkedAt, error = c.error, checkDaily = s.checkDaily,
            channel = channel, edition = edition, image = image, native = native, command = command, commandWhy = why,
        )
    }

    companion object {
        const val SETTINGS_KEY = "updates.settings"
        const val CACHE_KEY = "updates.release"
        val CHANNELS = listOf("stable", "product")
        /** the release notes keel keeps (Markdown); the rest is on GitHub */
        const val NOTES_MAX = 20_000
        private const val BODY_MAX = 1_000_000
        private val DAY: Duration = Duration.ofHours(24)

        fun due(c: ReleaseCache, now: Instant): Boolean {
            val at = c.checkedAt?.let { runCatching { Instant.parse(it) }.getOrNull() } ?: return true
            return Duration.between(at, now) >= DAY
        }

        /** A GitHub release (tag_name, name, body, html_url, published_at) as keel keeps it. */
        fun parse(n: JsonNode?): Release {
            val tag = n?.get("tag_name")?.asText()?.trim().orEmpty()
            if (Semver.parse(tag) == null) throw IllegalStateException("the answer has no release version (tag_name \"${tag.take(40)}\")")
            val body = n?.get("body")?.takeIf { it.isTextual }?.asText().orEmpty().replace("\r\n", "\n")
            val notes = if (body.length > NOTES_MAX) body.take(NOTES_MAX).substringBeforeLast('\n') + "\n\n…" else body
            return Release(tag.removePrefix("v"), n?.get("name")?.takeIf { it.isTextual }?.asText()?.take(200), notes,
                n?.get("html_url")?.takeIf { it.isTextual }?.asText(), n?.get("published_at")?.takeIf { it.isTextual }?.asText())
        }

        private fun why(e: Exception): String = when (e) {
            is java.net.http.HttpTimeoutException -> "no answer in time"
            is java.net.ConnectException, is java.net.UnknownHostException -> "no network (${e.javaClass.simpleName})"
            else -> (e.message ?: e.javaClass.simpleName).lineSequence().first().take(200)
        }

        /** The release JSON at [url]: http(s) with short time limits and a size cap, or a file: link (tests, e2e). */
        fun download(url: String, version: String): String {
            val uri = URI(url)
            if (uri.scheme == "file") return Files.readString(Paths.get(uri)).take(BODY_MAX)
            if (uri.scheme != "https" && uri.scheme != "http") throw IllegalArgumentException("not an http link: $url")
            val client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).followRedirects(HttpClient.Redirect.NORMAL).build()
            val req = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(15)).GET()
                .header("Accept", "application/vnd.github+json").header("User-Agent", "keel/$version").build()
            val res = client.send(req, HttpResponse.BodyHandlers.ofInputStream())
            res.body().use { body ->
                if (res.statusCode() == 404) throw IllegalStateException("the repo has no release yet (404)")
                if (res.statusCode() == 403 || res.statusCode() == 429) throw IllegalStateException("GitHub said wait (${res.statusCode()}: rate limit)")
                if (res.statusCode() !in 200..299) throw IllegalStateException("GitHub answered ${res.statusCode()}")
                val bytes = body.readNBytes(BODY_MAX + 1)
                if (bytes.size > BODY_MAX) throw IllegalStateException("the answer is too big")
                return bytes.toString(Charsets.UTF_8)
            }
        }
    }
}

/** keel's versions: 0.18.65, v0.18.65, 0.18.65-rc.1 (a pre-release is older than its release). */
object Semver {
    private val RE = Regex("^v?(\\d+)\\.(\\d+)\\.(\\d+)(?:-([0-9A-Za-z.-]+))?$")

    fun parse(v: String?): Pair<List<Int>, String?>? {
        val m = RE.matchEntire(v?.trim() ?: return null) ?: return null
        return listOf(m.groupValues[1], m.groupValues[2], m.groupValues[3]).map { it.toIntOrNull() ?: return null } to
            m.groupValues[4].ifEmpty { null }
    }

    /** [a] is newer than [b]; false when either is not a version (a "dev" build). */
    fun newer(a: String?, b: String?): Boolean {
        val x = parse(a) ?: return false
        val y = parse(b) ?: return false
        for (i in 0..2) if (x.first[i] != y.first[i]) return x.first[i] > y.first[i]
        val (pa, pb) = x.second to y.second
        return when {
            pa == null && pb == null -> false
            pa == null -> true          // 0.18.65 is newer than 0.18.65-rc.1
            pb == null -> false
            else -> pa > pb
        }
    }
}

/**
 * The command that updates this keel, and why that one. `keel2 update` keeps the image keel runs (a moving tag such
 * as :latest or :product-beta gets its newest build); a pinned tag (:0.18.64, :0.18.64-product-beta) would get the same
 * image again, so then the command names the new tag: KEEL_IMAGE=<image> keel2 update. Another channel than the one
 * keel runs names that channel's moving tag the same way.
 */
object Command {
    const val REPO = "ghcr.io/miladnalbandi/keel-v2"
    private val PINNED = Regex(":v?\\d+\\.\\d+(\\.\\d+)?(-[0-9A-Za-z.-]+)?$")

    fun of(latest: String?, channel: String, edition: String, image: String?, native: Boolean): Pair<String, String> {
        if (native) return "keel2 update" to "keel runs without Docker: keel2 update gets the newest release and restarts keel."
        if (edition == "core" && image == null) return "keel2 update" to "keel runs its core image: keel2 update gets the newest core image."
        val repo = image?.let { repoOf(it) }
        if (image != null && repo == null) {
            return "KEEL_IMAGE=$REPO:${tag(null, channel)} keel2 update" to
                "keel runs a local image ($image), built on this computer: this switches to the published one."
        }
        val pinned = image != null && PINNED.containsMatchIn(image)
        val running = when {
            image == null -> if (edition == "product") "product" else "stable"
            image.endsWith("product-beta") -> "product"
            else -> "stable"
        }
        return when {
            channel != running -> "KEEL_IMAGE=${repo ?: REPO}:${tag(if (pinned) latest else null, channel)} keel2 update" to
                "keel runs the ${label(running)} image; this moves it to ${label(channel)}."
            pinned && latest != null -> "KEEL_IMAGE=${repo ?: REPO}:${tag(latest, channel)} keel2 update" to
                "keel runs a pinned tag ($image): keel2 update alone would get that same tag again, so this names the new one."
            else -> "keel2 update" to "keel2 update gets the newest image of the tag keel runs and restarts keel."
        }
    }

    private fun label(channel: String) = if (channel == "product") "product beta" else "stable"

    /** the image tag of a channel: a version's own (0.18.65, 0.18.65-product-beta) or the moving one (latest, product-beta) */
    fun tag(version: String?, channel: String): String = when {
        version == null -> if (channel == "product") "product-beta" else "latest"
        channel == "product" -> "$version-product-beta"
        else -> version
    }

    /** ghcr.io/x/keel-v2:0.18.64 → ghcr.io/x/keel-v2; null for a local image (no registry, no slash). */
    fun repoOf(image: String): String? {
        val name = image.substringBefore('@')
        val slash = name.lastIndexOf('/')
        if (slash < 0) return null
        val colon = name.lastIndexOf(':')
        return if (colon > slash) name.substring(0, colon) else name
    }
}
