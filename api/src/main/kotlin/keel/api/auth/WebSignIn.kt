package keel.api.auth

import jakarta.servlet.http.HttpServletRequest
import keel.api.common.Hash
import keel.api.common.KeelProperties
import keel.api.common.Time
import org.springframework.http.ResponseCookie
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.security.SecureRandom
import java.time.Duration
import java.util.concurrent.ConcurrentHashMap

/**
 * v0.18.1 the key a person's browser and terminal app sign in with: 32 random bytes in $KEEL_DATA/run/web-key (0600),
 * made once and kept across restarts, so a signed-in browser stays signed in. `keel2 auth reset` deletes the file: the
 * api makes a new key at its next start and every browser signs in again.
 *
 * The cookie's name carries the key's id (`keel_<8 hex>`): browsers keep cookies by host, not by port, so a second keel
 * on localhost (another port) has its own cookie and never reads this one.
 * 0.18.64 the cookie holds a browser's own session id ([WebSessions]), never this key: the key stays for a terminal app
 * (`Authorization: Bearer`). Over https the cookie is `__Host-keel_<8 hex>`, Secure.
 */
@Component
class WebKey(private val props: KeelProperties) {
    val file: Path get() = props.dataDir.resolve("run").resolve("web-key")

    val value: String by lazy { load() }

    val cookieName: String by lazy { "keel_" + sha256(value).take(8) }

    fun matches(candidate: String?): Boolean = !candidate.isNullOrEmpty() && Hash.constantTimeEquals(candidate, value)

    /** 0.18.64 the cookie's name over https: Secure, this host only, path / (a `__Host-` cookie). */
    val secureCookieName: String get() = "__Host-$cookieName"

    fun cookieOf(request: HttpServletRequest): String? =
        request.cookies?.firstOrNull { it.name == secureCookieName }?.value ?: request.cookies?.firstOrNull { it.name == cookieName }?.value

    /** The key is made when keel starts: a terminal app (keel TUI, xal) reads the file before its first request. */
    @jakarta.annotation.PostConstruct
    fun ensure() {
        value
    }

    private fun load(): String {
        val f = file
        if (Files.isRegularFile(f)) {
            val text = Files.readString(f).trim()
            if (text.length >= 32) return text
        }
        Files.createDirectories(f.parent)
        val made = hex(ByteArray(32).also { random.nextBytes(it) })
        val tmp = f.resolveSibling("web-key.tmp")
        Files.deleteIfExists(tmp)
        keel.api.common.OwnerOnly.create(tmp, (made + "\n").toByteArray())
        Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        return made
    }

    companion object {
        private val random = SecureRandom()
        fun hex(b: ByteArray): String = Hash.hex(b)
        fun sha256(s: String): String = Hash.sha256Hex(s)
    }
}

/**
 * 0.18.64 a browser's sign-in: a random session id per browser, kept as its SHA-256 (table web_sessions). It ends 30
 * days after its last use ([TTL], each use moves the end), at sign-out (this one) or at "sign out everywhere" (all).
 * Until 0.18.63 the cookie was the web key itself, for 400 days, and a sign-out never ended it.
 * 0.21.3 a session is bound to the host and port it was opened on ([hostOf], column host): browsers keep cookies by
 * host, not by port, so they send keel's cookie to every other server on localhost too; a cookie that comes in on
 * another host or port than its own is refused (a session from before 0.21.3 is bound at its next use).
 */
@Component
class WebSessions(private val jdbc: JdbcTemplate, private val key: WebKey) {

    /** A new session, for the address [host] ([hostOf]); returns its id (only the browser's cookie has it). */
    fun open(now: Long = System.currentTimeMillis(), host: String? = null): String {
        val id = PREFIX + Hash.hex(ByteArray(32).also { random.nextBytes(it) })
        jdbc.update("DELETE FROM web_sessions WHERE expires_ms < ?", now)
        jdbc.update("INSERT INTO web_sessions(id_hash, created_at, last_seen_at, expires_ms, host) VALUES (?, ?, ?, ?, ?)",
            Hash.sha256Hex(id), Time.iso(now), Time.iso(now), now + TTL.toMillis(), host)
        return id
    }

    enum class Check { NO, YES, MOVED }

    /**
     * Is this a live session, on its own address [host] ([hostOf]; null: any)? A use moves its end to [TTL] from now
     * (written at most once per [MOVE_EVERY]: MOVED). On another address: NO, and the session stays for its own one.
     */
    fun check(id: String?, now: Long = System.currentTimeMillis(), host: String? = null): Check {
        if (id == null || !id.startsWith(PREFIX) || id.length != PREFIX.length + 64) return Check.NO
        val hash = Hash.sha256Hex(id)
        val (until, bound) = jdbc.query("SELECT expires_ms, host FROM web_sessions WHERE id_hash = ?",
            { rs, _ -> rs.getLong(1) to rs.getString(2) }, hash).firstOrNull()
            ?: return Check.NO
        if (until < now) {
            jdbc.update("DELETE FROM web_sessions WHERE id_hash = ?", hash)
            return Check.NO
        }
        if (host != null) {
            val own = bound ?: if (jdbc.update("UPDATE web_sessions SET host = ? WHERE id_hash = ? AND host IS NULL", host, hash) == 1) host
                else jdbc.query("SELECT host FROM web_sessions WHERE id_hash = ?", { rs, _ -> rs.getString(1) }, hash).firstOrNull()
            if (own != host) return Check.NO
        }
        if (now + TTL.toMillis() - until < MOVE_EVERY.toMillis()) return Check.YES
        jdbc.update("UPDATE web_sessions SET last_seen_at = ?, expires_ms = ? WHERE id_hash = ?", Time.iso(now), now + TTL.toMillis(), hash)
        return Check.MOVED
    }

    /** Ends this browser's session (sign-out). */
    fun end(id: String?) {
        if (id != null) jdbc.update("DELETE FROM web_sessions WHERE id_hash = ?", Hash.sha256Hex(id))
    }

    /** Ends every browser's session ("sign out everywhere"); returns how many there were. */
    fun endAll(): Int = jdbc.update("DELETE FROM web_sessions")

    /** The sign-in cookie for this request: `__Host-…` and Secure over https, else the plain name. */
    fun cookie(request: HttpServletRequest, value: String, maxAge: Duration = TTL): ResponseCookie =
        if (https(request)) ResponseCookie.from(key.secureCookieName, value).secure(true).httpOnly(true).sameSite("Strict").path("/").maxAge(maxAge).build()
        else ResponseCookie.from(key.cookieName, value).httpOnly(true).sameSite("Strict").path("/").maxAge(maxAge).build()

    companion object {
        const val PREFIX = "ks_"
        val TTL: Duration = Duration.ofDays(30)
        val MOVE_EVERY: Duration = Duration.ofHours(1)
        private val random = SecureRandom()

        /** The address a request came to, as a session is bound to it: the Host header's name and port (localhost:8088). */
        fun hostOf(request: HttpServletRequest): String = "${(request.serverName ?: "").lowercase()}:${request.serverPort}"

        /** keel itself on https, or behind a proxy that says so. */
        fun https(request: HttpServletRequest): Boolean =
            request.isSecure || request.getHeader("X-Forwarded-Proto")?.trim()?.lowercase() == "https"
    }
}

/** One-time sign-in codes: 60 seconds, used once. keel2 asks for one and opens the browser with it. */
@Component
class SignInCodes {
    private val codes = ConcurrentHashMap<String, Long>()

    fun make(now: Long = System.currentTimeMillis()): String {
        codes.entries.removeIf { it.value < now }
        if (codes.size >= MAX) codes.clear()
        val code = WebKey.hex(ByteArray(16).also { random.nextBytes(it) })
        codes[code] = now + TTL.toMillis()
        return code
    }

    /** True once for a code that is still fresh; the code is gone afterwards either way. */
    fun use(code: String?, now: Long = System.currentTimeMillis()): Boolean {
        if (code.isNullOrBlank()) return false
        val until = codes.remove(code.trim()) ?: return false
        return until >= now
    }

    companion object {
        val TTL: Duration = Duration.ofSeconds(60)
        const val MAX = 50
        private val random = SecureRandom()
    }
}

data class SignInCode(val code: String, val path: String, val expires_in: Long)
