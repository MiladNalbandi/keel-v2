package keel.api.connections

import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import java.io.OutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** What the web sees of a login: never the token, never the CLI's raw output. */
data class LoginView(
    val id: String,
    val provider: String,
    val status: String,            // starting | waiting | code_needed | done | failed | cancelled
    val url: String? = null,       // open this in the browser
    val code: String? = null,      // device code to type on that page (codex, copilot)
    val message: String,
    val hint: String? = null,      // last characters of the saved secret
)

/**
 * Logs a CLI in from the dashboard, inside the container, and saves the result encrypted in the database:
 * codex `login --device-auth` → auth.json (CODEX_AUTH_JSON), copilot `login --device-code` → GitHub token (GH_TOKEN),
 * claude `setup-token` → the printed token (CLAUDE_CODE_OAUTH_TOKEN; the user pastes the code the Claude page shows).
 *
 * Each login runs in a pseudo-terminal (`script`) with a scratch HOME, so nothing of the user's computer is used and
 * nothing is left behind. The output stays in memory and is never logged or returned.
 */
@Service
class LoginService(
    private val secrets: SecretService,
    @Value("\${keel.login-pty:true}") private val usePty: Boolean,
    @Value("\${keel.login-commands.claude:claude setup-token}") private val claudeCmd: String,
    @Value("\${keel.login-commands.codex:codex login --device-auth}") private val codexCmd: String,
    @Value("\${keel.login-commands.copilot:copilot login --device-code}") private val copilotCmd: String,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    private class Session(val id: String, val provider: String, val home: Path, val proc: Process, val stdin: OutputStream) {
        val out = StringBuilder()
        @Volatile var status = "starting"
        @Volatile var message = "Starting the login…"
        @Volatile var url: String? = null
        @Volatile var code: String? = null
        @Volatile var hint: String? = null
        /** v0.15.6 the Copilot CLI's "store the token in a plain file?" question was answered */
        @Volatile var consented = false
        val started: Instant = Instant.now()
    }

    private val sessions = ConcurrentHashMap<String, Session>()

    fun start(provider: String): LoginView {
        val cmd = when (provider) {
            "claude" -> claudeCmd
            "codex" -> codexCmd
            "copilot" -> copilotCmd
            else -> throw BadRequest("No login helper for \"$provider\"", "Use claude, codex or copilot.")
        }
        sessions.values.filter { it.provider == provider && it.status in setOf("starting", "waiting", "code_needed") }.forEach { cancel(it.id) }
        val home = Files.createTempDirectory("keel-login-")
        val env = mapOf(
            "HOME" to home.toString(), "CODEX_HOME" to home.resolve(".codex").toString(), "XDG_CONFIG_HOME" to home.resolve(".config").toString(),
            "PATH" to (System.getenv("PATH") ?: "/usr/local/bin:/usr/bin:/bin"), "LANG" to "C.UTF-8", "TERM" to "xterm-256color",
            "BROWSER" to "/bin/false", "COLUMNS" to "1000",
        )
        Files.createDirectories(home.resolve(".codex"))
        val argv = if (usePty) listOf("script", "-qfec", "stty cols 1000 rows 50 2>/dev/null; exec $cmd", "/dev/null") else listOf("sh", "-c", cmd)
        val pb = ProcessBuilder(argv).directory(home.toFile()).redirectErrorStream(true)
        pb.environment().clear()
        pb.environment().putAll(env)
        val proc = try { pb.start() } catch (e: Exception) {
            throw Conflict("Could not start the $provider login", "Is the $provider CLI installed in the image? (${e.message?.take(120)})")
        }
        val s = Session(UUID.randomUUID().toString().take(12), provider, home, proc, proc.outputStream)
        sessions[s.id] = s
        thread(isDaemon = true, name = "login-${s.id}") { read(s) }
        thread(isDaemon = true, name = "login-wait-${s.id}") { finish(s) }
        // Give the CLI a moment to print its link (and its device code: it can come in a later chunk),
        // so the first answer is already useful.
        repeat(40) { if ((s.url == null || (provider != "claude" && s.code == null)) && s.status == "starting") Thread.sleep(250) }
        return view(s)
    }

    fun get(id: String): LoginView = view(sessions[id] ?: throw NotFound("No login called $id", "Start the login again."))

    /** Claude: the code the Claude sign-in page shows after login. */
    fun code(id: String, code: String): LoginView {
        val s = sessions[id] ?: throw NotFound("No login called $id", "Start the login again.")
        if (s.provider != "claude") throw BadRequest("Only the Claude login asks for a code")
        val c = code.trim()
        if (c.isEmpty() || c.length > 2000 || c.any { it.isWhitespace() }) throw BadRequest("Paste the code exactly as the page shows it (one piece, no spaces)")
        s.stdin.write((c + if (usePty) "\r" else "\n").toByteArray())   // Enter in a terminal is \r
        s.stdin.flush()
        s.status = "waiting"
        s.message = "Checking the code…"
        return view(s)
    }

    fun cancel(id: String): LoginView {
        val s = sessions[id] ?: throw NotFound("No login called $id")
        if (s.status !in setOf("done", "failed")) {
            s.status = "cancelled"
            s.message = "Login cancelled."
        }
        s.proc.destroyForcibly()
        return view(s)
    }

    private fun view(s: Session) = LoginView(s.id, s.provider, s.status, s.url, s.code, s.message, s.hint)

    private fun read(s: Session) {
        val buf = ByteArray(4096)
        val input = s.proc.inputStream
        while (true) {
            val n = try { input.read(buf) } catch (e: Exception) { -1 }
            if (n < 0) break
            synchronized(s.out) {
                s.out.append(String(buf, 0, n, Charsets.UTF_8))
                if (s.out.length > 200_000) s.out.delete(0, s.out.length - 100_000)
            }
            parse(s)
        }
    }

    private fun clean(s: Session): String = synchronized(s.out) {
        s.out.toString().replace(Regex("\u001B\\[[0-9;?]*[ -/]*[@-~]"), "").replace(Regex("\u001B][^\u0007]*\u0007"), "")
    }

    private fun parse(s: Session) {
        if (s.status in setOf("done", "failed", "cancelled")) return
        val text = clean(s)
        if (s.url == null) {
            s.url = Regex("https://[^\\s\"'<>]+").findAll(text).map { it.value.trimEnd('.', ',', ')') }
                .firstOrNull { "device" in it || "oauth" in it || "authorize" in it }
        }
        if (s.provider != "claude" && s.code == null) s.code = Regex("\\b[A-Z0-9]{4}-[A-Z0-9]{4,5}\\b").find(text)?.value
        // v0.15.6 after the person authorized, the Copilot CLI finds no keychain in keel's container and asks
        // "System keychain unavailable. Store token in plaintext config file? (y/N)" — nobody saw it, so the login waited
        // forever. keel answers yes: the token goes to the login's own temporary folder, keel saves it encrypted (GH_TOKEN)
        // and deletes that folder.
        if (s.provider == "copilot" && !s.consented && Regex("(?i)store token in plain ?text").containsMatchIn(text)) {
            s.consented = true
            s.message = "Authorized. Saving the token…"
            runCatching {
                s.stdin.write((if (usePty) "y\r" else "y\n").toByteArray())
                s.stdin.flush()
            }
        }
        if (s.provider == "copilot" && Regex("(?i)token was not saved").containsMatchIn(text) && s.status !in setOf("done", "failed", "cancelled")) {
            s.status = "failed"
            s.message = "GitHub authorized keel, but the Copilot CLI did not save the token. Start the login again, or paste a token."
        }
        when {
            s.provider == "claude" && s.url != null && s.status == "starting" -> {
                s.status = "code_needed"
                s.message = "Open the link, sign in with your Claude account, then paste the code the page shows."
            }
            s.provider != "claude" && s.url != null && s.code != null && s.status == "starting" -> {
                s.status = "waiting"
                s.message = "Open the link, enter the code, and allow access. This page notices by itself when you are done."
            }
        }
        if (s.provider == "claude") {
            Regex("sk-ant-oat01-[A-Za-z0-9_\\-]{20,}").find(text.filterNot { it.isWhitespace() })?.let { save(s, "CLAUDE_CODE_OAUTH_TOKEN", it.value) }
        }
    }

    private fun finish(s: Session) {
        // v0.15.5 the CLI can stay open after it saved the login (the Copilot CLI does on some machines): look for the
        // result every 2 s while it runs, not only when it ends, so the page does not wait for the 15-minute limit
        val deadline = System.nanoTime() + TimeUnit.MINUTES.toNanos(15)
        var ended = false
        while (!ended && System.nanoTime() < deadline && s.status !in setOf("done", "cancelled", "failed")) {
            ended = s.proc.waitFor(2, TimeUnit.SECONDS)
            if (!ended) collect(s)
        }
        if (!ended && s.status != "done") s.proc.destroyForcibly()
        Thread.sleep(200)
        parse(s)
        collect(s)
        if (s.status !in setOf("done", "cancelled")) {
            s.status = "failed"
            s.message = when {
                !ended -> "The login took longer than 15 minutes and was stopped. Start it again."
                else -> lastLine(clean(s))?.let { "The login did not finish: $it" } ?: "The login did not finish. Start it again."
            }
        }
        s.home.toFile().deleteRecursively()
    }

    /** The login's result, when the CLI wrote it to a file: Codex's auth.json, the Copilot CLI's token. */
    private fun collect(s: Session) {
        if (s.status in setOf("done", "cancelled")) return
        try {
            when (s.provider) {
                "codex" -> {
                    val f = s.home.resolve(".codex/auth.json")
                    if (Files.isRegularFile(f) && Files.size(f) > 0) save(s, "CODEX_AUTH_JSON", Files.readString(f))
                }
                "copilot" -> findGithubToken(s.home)?.let { save(s, "GH_TOKEN", it) }
            }
        } catch (e: Exception) {
            log.warn("{} login: could not read the result: {}", s.provider, e.javaClass.simpleName)
        }
    }

    private fun save(s: Session, name: String, value: String) {
        if (s.status == "done") return
        s.hint = secrets.put(name, value)
        s.status = "done"
        s.message = "Logged in. Saved encrypted in keel's database as $name."
        log.info("{} login saved as {}", s.provider, name)
        s.proc.destroy()
    }

    /** The Copilot CLI keeps its token in a config file when there is no system keychain (as in a container). */
    private fun findGithubToken(home: Path): String? {
        val re = Regex("\\b(gho_|ghu_|github_pat_)[A-Za-z0-9_]{20,}")
        return Files.walk(home).use { files ->
            files.filter { Files.isRegularFile(it) && Files.size(it) < 1_000_000 }.toList()
        }.firstNotNullOfOrNull { f -> runCatching { re.find(Files.readString(f))?.value }.getOrNull() }
    }

    /** A short, safe last line for an error (anything that looks like a token is cut out). */
    private fun lastLine(text: String): String? = text.lines().map { it.trim() }.lastOrNull { it.length in 4..200 }
        ?.replace(Regex("(sk-ant-[A-Za-z0-9_\\-]+|gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)"), "…")
}
