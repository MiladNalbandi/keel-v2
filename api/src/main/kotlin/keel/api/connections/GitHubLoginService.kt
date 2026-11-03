package keel.api.connections

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** What the web sees of a GitHub sign-in: the code and the link, never the token or the CLI's raw output. */
data class GitHubLoginView(
    val id: String,
    val status: String,            // starting | waiting | done | failed | cancelled
    val host: String,
    val url: String? = null,       // open this in the browser
    val code: String? = null,      // the one-time code to type there
    val message: String,
    val login: String? = null,     // who is connected (when done)
    val scopes: List<String> = emptyList(),
    val via: String? = null,       // gh | oauth
)

data class GitHubLoginStart(val host: String = "")

/** Who the saved GitHub token belongs to: stored encrypted next to the token (GITHUB_REPO_INFO), never the token. */
data class GitHubWho(val login: String? = null, val scopes: List<String> = emptyList(), val host: String = "github.com", val via: String? = null)

/**
 * Connections › GitHub › "Sign in with GitHub": the device flow, the way the Copilot login works. keel runs
 * `gh auth login --web` inside the container (scratch HOME, in a pseudo-terminal), shows the one-time code and the
 * link, waits until gh is done, reads the token with `gh auth token` and saves it where a pasted token goes
 * (GITHUB_REPO_TOKEN), so the Git plugin, the PR step, review and the others use it. Without `gh`, the standard OAuth
 * device flow runs against the host with a client id the company sets (KEEL_GITHUB_CLIENT_ID): a public OAuth App id,
 * no secret. A GitHub Enterprise host works the same (`--hostname`). The token is never logged or returned.
 */
@Service
class GitHubLoginService(
    private val secrets: SecretService,
    private val mapper: ObjectMapper,
    @Value("\${keel.login-pty:true}") private val usePty: Boolean,
    @Value("\${keel.github-login.gh:gh}") private val gh: String,
    @Value("\${keel.github.oauth-client-id:\${KEEL_GITHUB_CLIENT_ID:}}") private val clientId: String,
    /** tests: where the OAuth device endpoints and the API live instead of https://<host> */
    @Value("\${keel.github-login.oauth-base:}") private val oauthBase: String,
    /** how long gh may take to print the code before the sign-in fails with what it printed */
    @Value("\${keel.github-login.code-timeout-seconds:30}") private val codeTimeoutSeconds: Long = 30,
    /** how long a short gh call (auth token, status, logout) may take before it is killed with its children */
    @Value("\${keel.github-login.run-timeout-seconds:30}") private val runTimeoutSeconds: Long = 30,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    private class Session(val id: String, val host: String, val home: Path?, val proc: Process?, val stdin: java.io.OutputStream?) {
        val out = StringBuilder()
        @Volatile var status = "starting"
        @Volatile var message = "Starting the sign-in…"
        @Volatile var url: String? = null
        @Volatile var code: String? = null
        @Volatile var who: GitHubWho? = null
        @Volatile var enter = false
        @Volatile var answered = 0      // yes/no prompts already answered
        @Volatile var cursorAsked = 0   // cursor-position requests (ESC [ 6 n) already answered
        @Volatile var cancelled = false
        val started: java.time.Instant = java.time.Instant.now()
    }

    private val sessions = ConcurrentHashMap<String, Session>()

    fun start(rawHost: String): GitHubLoginView {
        val host = rawHost.trim().ifEmpty { "github.com" }.lowercase()
        if (!Regex("^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:[0-9]{1,5})?$").matches(host)) throw BadRequest("\"$rawHost\" is not a host name", "Use github.com or your GitHub Enterprise host, like github.example.com.")
        evictOld(Duration.ofHours(1))
        sessions.values.filter { it.status in ACTIVE }.forEach { cancel(it.id) }
        val s = if (ghAvailable()) startGh(host) else startOAuth(host)
        // the code can come a moment after the link
        repeat(40) { if (s.status == "starting" && (s.url == null || s.code == null)) Thread.sleep(250) }
        return view(s)
    }

    /** Forgets the sign-ins older than [maxAge]: a running one is stopped with its children, and its scratch HOME goes. */
    internal fun evictOld(maxAge: Duration) {
        val cutoff = java.time.Instant.now().minus(maxAge)
        sessions.values.filter { !it.started.isAfter(cutoff) }.forEach { s ->
            sessions.remove(s.id)
            if (s.status in ACTIVE) { s.cancelled = true; s.status = "cancelled"; s.message = "Sign-in cancelled." }
            s.proc?.let { keel.api.common.Proc.killTree(it) }
            runCatching { s.home?.toFile()?.deleteRecursively() }
        }
    }

    internal fun sessionCount(): Int = sessions.size

    fun get(id: String): GitHubLoginView = view(sessions[id] ?: throw NotFound("No sign-in called $id", "Start the sign-in again."))

    fun cancel(id: String): GitHubLoginView {
        val s = sessions[id] ?: throw NotFound("No sign-in called $id")
        if (s.status in ACTIVE) {
            s.cancelled = true
            s.status = "cancelled"
            s.message = "Sign-in cancelled."
        }
        s.proc?.let { keel.api.common.Proc.killTree(it) }
        return view(s)
    }

    /** Who is connected, or null when no token is saved by keel. */
    fun who(): GitHubWho? = secrets.get(INFO)?.let { runCatching { mapper.readValue(it, GitHubWho::class.java) }.getOrNull() }

    /**
     * Sign out: the token keel saved and who it was are deleted. keel never keeps a gh login: gh runs with a scratch HOME
     * that is deleted after the sign-in, so there is nothing for `gh auth logout` to remove (and the person's own gh
     * login is not touched). The token itself stays valid at GitHub until they remove keel's access there.
     */
    fun signOut(): GitHubSignOut {
        val had = listOf(TOKEN, INFO).filter { secrets.has(it) }
        had.forEach { secrets.delete(it) }
        return GitHubSignOut(removed = had.isNotEmpty(), revokedAtGitHub = false,
            note = if (had.isEmpty()) "keel had no GitHub token saved."
            else "keel forgot the token. GitHub still counts it as valid until you remove keel's access at github.com/settings/applications (or delete the token there).")
    }

    // ---- gh ------------------------------------------------------------------------------

    private fun ghCmd(): List<String> = gh.trim().split(Regex("\\s+"))

    private fun ghAvailable(): Boolean = runCatching {
        capture(keel.api.common.Proc.cleanEnv(ProcessBuilder(ghCmd() + "--version").redirectErrorStream(true)), minOf(10L, runTimeoutSeconds))?.second == 0
    }.getOrDefault(false)

    /**
     * Runs [pb] and reads what it prints, within [timeoutSeconds]: the output is read on its own thread, so a process that
     * hangs cannot block the caller (readAllBytes before waitFor waited for ever). On the timeout the process and its
     * children are killed. Returns (output, exit code), or null when it timed out.
     */
    private fun capture(pb: ProcessBuilder, timeoutSeconds: Long): Pair<String, Int>? {
        val p = pb.start()
        p.outputStream.close()
        val out = java.io.ByteArrayOutputStream()
        val reader = thread(isDaemon = true, name = "gh-capture") { runCatching { p.inputStream.copyTo(out) } }
        if (!p.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
            keel.api.common.Proc.killTree(p)
            reader.join(2000)
            return null
        }
        reader.join(2000)   // a child that kept the pipe open must not hold us either
        if (reader.isAlive) p.descendants().forEach { it.destroyForcibly() }
        return synchronized(out) { out.toString(Charsets.UTF_8) } to p.exitValue()
    }

    private fun env(home: Path) = mapOf(
        "HOME" to home.toString(), "XDG_CONFIG_HOME" to home.resolve(".config").toString(), "GH_CONFIG_DIR" to home.resolve(".config/gh").toString(),
        "PATH" to (System.getenv("PATH") ?: "/usr/local/bin:/usr/bin:/bin"), "LANG" to "C.UTF-8", "TERM" to "dumb",
        // gh's default prompter asks the terminal questions (colour, cursor position) that keel's pty never answers; the accessible one reads plain lines
        "GH_ACCESSIBLE_PROMPTER" to "true",
        "BROWSER" to "/bin/false", "COLUMNS" to "1000", "GH_PROMPT_DISABLED" to "", "NO_COLOR" to "1", "GH_NO_UPDATE_NOTIFIER" to "1",
    )

    private fun startGh(host: String): Session {
        val home = Files.createTempDirectory("keel-gh-login-")
        Files.createDirectories(home.resolve(".config/gh"))
        val login = ghCmd() + listOf("auth", "login", "--hostname", host, "--git-protocol", "https", "--web", "--scopes", "repo,read:org,workflow", "--insecure-storage")
        val argv = if (usePty) keel.api.common.Proc.inTerminal("stty cols 1000 rows 50 2>/dev/null; exec " + login.joinToString(" ")) else login
        val pb = ProcessBuilder(argv).directory(home.toFile()).redirectErrorStream(true)
        pb.environment().clear()
        pb.environment().putAll(env(home).filterKeys { it != "GH_PROMPT_DISABLED" })
        val proc = try { pb.start() } catch (e: Exception) {
            home.toFile().deleteRecursively()
            throw Conflict("Could not start gh", "Is the GitHub CLI installed in the image? (${e.message?.take(120)})")
        }
        val s = Session(UUID.randomUUID().toString().take(12), host, home, proc, proc.outputStream)
        sessions[s.id] = s
        thread(isDaemon = true, name = "gh-login-${s.id}") { readGh(s) }
        thread(isDaemon = true, name = "gh-login-wait-${s.id}") { finishGh(s) }
        return s
    }

    private fun readGh(s: Session) {
        PtyOutput.pump(s.proc!!, s.out, 100_000, 50_000) { parseGh(s) }
    }

    private fun cleanOut(s: Session): String = PtyOutput.clean(s.out)

    /** gh prints "! First copy your one-time code: ABCD-1234" and "Open this URL to continue in your web browser: https://…/login/device". */
    private fun parseGh(s: Session) {
        if (s.status !in ACTIVE) return
        val text = cleanOut(s)
        if (s.code == null) s.code = parseCode(text)
        if (s.url == null) s.url = parseUrl(text)
        val eol = if (usePty) "\r" else "\n"
        synchronized(s) {
            // safety net: a terminal query (cursor position) is answered, so gh never waits for a terminal
            val asked = synchronized(s.out) { Regex("\u001B\\[6n").findAll(s.out).count() }
            while (s.cursorAsked < asked) { s.cursorAsked++; send(s, "\u001B[1;1R") }
            // gh asks "Authenticate Git with your GitHub credentials? (Y/n)" — the accessible prompter writes "(default: yes) [Y/n]": yes; any other yes/no prompt: its default
            val prompts = Regex("(?m)^[^\\n]*\\?[^\\n]*?[(\\[](?:Y/n|y/N)[)\\]]").findAll(text).toList()
            while (s.answered < prompts.size) {
                val p = prompts[s.answered++].value
                send(s, (if (p.contains("Authenticate Git", ignoreCase = true)) "Y" else "") + eol)
            }
            if (!s.enter && Regex("(?i)press enter").containsMatchIn(text)) {
                s.enter = true
                send(s, eol)
            }
        }
        if (s.status == "starting" && s.url != null && s.code != null) {
            s.status = "waiting"
            s.message = "Open the link, enter the code, and allow access. This page notices by itself when you are done."
        }
    }

    private fun send(s: Session, text: String) {
        runCatching { s.stdin?.write(text.toByteArray()); s.stdin?.flush() }
    }

    private fun finishGh(s: Session) {
        // gh must print the code soon; if it does not, say what it printed instead of staying at "starting"
        if (!s.proc!!.waitFor(codeTimeoutSeconds, TimeUnit.SECONDS)) {
            parseGh(s)
            if (s.code == null && !s.cancelled) {
                val tail = scrub(cleanOut(s).trim().takeLast(300))
                keel.api.common.Proc.killTree(s.proc)
                fail(s, "gh did not show a sign-in code after $codeTimeoutSeconds seconds. It printed: ${tail.ifEmpty { "(nothing)" }}")
                s.home?.toFile()?.deleteRecursively()
                return
            }
        }
        val ended = s.proc.waitFor(15, TimeUnit.MINUTES)
        Thread.sleep(200)
        parseGh(s)
        try {
            if (s.cancelled) return
            if (!ended) {
                keel.api.common.Proc.killTree(s.proc)
                fail(s, "The sign-in took longer than 15 minutes and was stopped. Start it again.")
            } else if (s.proc.exitValue() != 0) {
                fail(s, cleanOut(s).lines().map { it.trim() }.lastOrNull { it.length in 4..200 }?.let { "GitHub sign-in did not finish: ${scrub(it)}" } ?: "GitHub sign-in did not finish. Start it again.")
            } else {
                val token = runGh(s.home!!, "auth", "token", "--hostname", s.host)?.trim().orEmpty()
                if (token.isEmpty() || token.any { it.isWhitespace() }) {
                    fail(s, "gh signed in but keel could not read the token. Start it again, or paste a token.")
                } else {
                    val status = runGh(s.home, "auth", "status", "--hostname", s.host).orEmpty()
                    save(s, token, parseStatus(status, s.host, "gh"))
                }
            }
        } catch (e: Exception) {
            log.warn("github sign-in: {}", e.javaClass.simpleName)
            fail(s, "GitHub sign-in did not finish. Start it again, or paste a token.")
        } finally {
            s.home?.toFile()?.deleteRecursively()
        }
    }

    /** Runs gh with the scratch HOME; the answer is the text it printed (stdout and stderr), null when it did not start. */
    private fun runGh(home: Path, vararg args: String): String? = runCatching {
        val pb = ProcessBuilder(ghCmd() + args).directory(home.toFile()).redirectErrorStream(true)
        pb.environment().clear()
        pb.environment().putAll(env(home).filterKeys { it != "GH_PROMPT_DISABLED" })
        capture(pb, runTimeoutSeconds)?.takeIf { it.second == 0 }?.first
    }.getOrNull()

    // ---- the standard OAuth device flow, when gh is not there ------------------------------

    private val http by lazy { HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build() }

    private fun base(host: String) = oauthBase.ifBlank { "https://$host" }
    private fun apiBase(host: String) = if (oauthBase.isNotBlank()) "$oauthBase/api/v3" else if (host == "github.com") "https://api.github.com" else "https://$host/api/v3"

    private fun form(url: String, vararg kv: Pair<String, String>): com.fasterxml.jackson.databind.JsonNode {
        val body = kv.joinToString("&") { "${it.first}=${URLEncoder.encode(it.second, Charsets.UTF_8)}" }
        val req = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(20)).header("Accept", "application/json")
            .header("Content-Type", "application/x-www-form-urlencoded").POST(HttpRequest.BodyPublishers.ofString(body)).build()
        return mapper.readTree(http.send(req, HttpResponse.BodyHandlers.ofString()).body())
    }

    private fun startOAuth(host: String): Session {
        if (clientId.isBlank()) throw Conflict(
            "GitHub sign-in needs the gh CLI, and this image has none",
            "Install gh in the image, or set KEEL_GITHUB_CLIENT_ID to a public OAuth App client id (device flow on) for $host. Or paste a token.",
        )
        val first = try { form("${base(host)}/login/device/code", "client_id" to clientId, "scope" to "repo read:org workflow") } catch (e: Exception) {
            throw Conflict("Could not reach $host", "Check the host name and the network, or paste a token.")
        }
        val device = first["device_code"]?.asText()
        if (device.isNullOrBlank()) throw Conflict("$host refused the sign-in: ${scrub(first["error_description"]?.asText() ?: first["error"]?.asText() ?: "no device code")}", "Check the client id has the device flow turned on, or paste a token.")
        val s = Session(UUID.randomUUID().toString().take(12), host, null, null, null)
        s.code = first["user_code"]?.asText()
        s.url = first["verification_uri"]?.asText() ?: "${base(host)}/login/device"
        s.status = "waiting"
        s.message = "Open the link, enter the code, and allow access. This page notices by itself when you are done."
        sessions[s.id] = s
        thread(isDaemon = true, name = "github-oauth-${s.id}") { pollOAuth(s, device, first["interval"]?.asLong() ?: 5, first["expires_in"]?.asLong() ?: 900) }
        return s
    }

    private fun pollOAuth(s: Session, device: String, interval0: Long, expires: Long) {
        var interval = interval0.coerceAtLeast(1)
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(expires)
        try {
            while (!s.cancelled && System.nanoTime() < deadline) {
                Thread.sleep(interval * 1000)
                if (s.cancelled) return
                val r = form("${base(s.host)}/login/oauth/access_token", "client_id" to clientId, "device_code" to device, "grant_type" to "urn:ietf:params:oauth:grant-type:device_code")
                val token = r["access_token"]?.asText()
                when {
                    !token.isNullOrBlank() -> {
                        val scopes = r["scope"]?.asText().orEmpty().split(',', ' ').filter { it.isNotBlank() }
                        save(s, token, GitHubWho(userLogin(s.host, token), scopes, s.host, "oauth"))
                        return
                    }
                    r["error"]?.asText() == "authorization_pending" -> Unit
                    r["error"]?.asText() == "slow_down" -> interval += 5
                    r["error"]?.asText() == "access_denied" -> return fail(s, "GitHub sign-in was refused. Start it again if that was a mistake.")
                    else -> return fail(s, "GitHub sign-in did not finish: ${scrub(r["error_description"]?.asText() ?: r["error"]?.asText() ?: "unknown error")}")
                }
            }
            if (!s.cancelled) fail(s, "The code expired. Start the sign-in again.")
        } catch (e: Exception) {
            if (!s.cancelled) fail(s, "GitHub sign-in did not finish. Start it again, or paste a token.")
        }
    }

    private fun userLogin(host: String, token: String): String? = runCatching {
        val req = HttpRequest.newBuilder(URI.create("${apiBase(host)}/user")).timeout(Duration.ofSeconds(15))
            .header("Authorization", "Bearer $token").header("Accept", "application/json").GET().build()
        mapper.readTree(http.send(req, HttpResponse.BodyHandlers.ofString()).body())["login"]?.asText()
    }.getOrNull()

    // ---- shared ------------------------------------------------------------------------

    private fun save(s: Session, token: String, who: GitHubWho) {
        if (s.cancelled || s.status == "done") return
        secrets.put(TOKEN, token)
        secrets.put(INFO, mapper.writeValueAsString(who))
        s.who = who
        s.status = "done"
        s.message = "Connected. The token is saved encrypted in keel's database."
        log.info("github sign-in saved for {}", s.host)
    }

    private fun fail(s: Session, message: String) {
        if (s.status in setOf("done", "cancelled")) return
        s.status = "failed"
        s.message = message
    }

    private fun view(s: Session) = GitHubLoginView(s.id, s.status, s.host, s.url, s.code, s.message, s.who?.login, s.who?.scopes.orEmpty(), s.who?.via)

    companion object {
        const val TOKEN = "GITHUB_REPO_TOKEN"
        const val INFO = "GITHUB_REPO_INFO"
        private val ACTIVE = setOf("starting", "waiting")

        fun parseCode(text: String): String? =
            Regex("(?i)one-time code:?\\s*([A-Z0-9]{4}-[A-Z0-9]{4})").find(text)?.groupValues?.get(1)
                ?: Regex("\\b[A-Z0-9]{4}-[A-Z0-9]{4}\\b").find(text)?.value

        fun parseUrl(text: String): String? =
            Regex("https?://[^\\s\"'<>]+").findAll(text).map { it.value.trimEnd('.', ',', ')') }.firstOrNull { "/login/device" in it }

        /** `gh auth status`: "Logged in to github.com account NAME (…)" and "Token scopes: 'repo', 'read:org'". */
        fun parseStatus(text: String, host: String, via: String): GitHubWho {
            val login = Regex("(?i)account\\s+([A-Za-z0-9][A-Za-z0-9-]*)").find(text)?.groupValues?.get(1)
            val scopes = Regex("(?i)token scopes:\\s*(.+)").find(text)?.groupValues?.get(1)
                ?.split(',')?.map { it.trim().trim('\'', '"', ' ') }?.filter { it.isNotEmpty() }.orEmpty()
            return GitHubWho(login, scopes, host, via)
        }

        /** A short, safe line for an error: anything that looks like a token is cut out. */
        fun scrub(text: String): String = text.replace(Regex("(gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)"), "…")
    }
}

data class GitHubSignOut(val removed: Boolean, val revokedAtGitHub: Boolean, val note: String)
