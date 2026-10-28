package keel.api.auth

import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.common.Forbidden
import org.springframework.http.HttpHeaders
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.time.Duration

@RestController
class WebSignInController(
    private val key: WebKey,
    private val codes: SignInCodes,
    private val limiter: AuthLimiter,
    private val sessions: WebSessions,
) {

    /** A one-time sign-in code, only for keel2 and keel's scripts (the internal token; [LocalAuth] checked it). */
    @PostMapping("/api/web/code")
    fun code(request: HttpServletRequest): SignInCode {
        if (request.getAttribute(LocalAuth.WHO_ATTR) != "internal") {
            throw Forbidden("Only keel2 can make a sign-in code", "Run keel2 open in a terminal.")
        }
        val c = codes.make()
        return SignInCode(c, "/web/login?code=$c", SignInCodes.TTL.seconds)
    }

    /** The link keel2 opens: a fresh code sets the sign-in cookie and goes to keel's page; a used or old one says so. */
    @GetMapping("/web/login")
    fun login(
        @RequestParam(required = false) code: String?,
        @RequestParam(required = false) next: String?,
        request: HttpServletRequest,
        response: HttpServletResponse,
    ) {
        // keel reachable from the Wi-Fi or a tunnel: wrong codes count like wrong keys (AuthLimiter)
        if (limiter.blocked(AuthLimiter.Kind.SIGN_IN, request)) {
            response.status = 429
            response.setHeader("Retry-After", limiter.retryAfter().toString())
            return
        }
        if (!codes.use(code)) {
            if (!code.isNullOrBlank()) limiter.fail(AuthLimiter.Kind.SIGN_IN, request)
            response.status = HttpServletResponse.SC_FOUND
            response.setHeader(HttpHeaders.LOCATION, "/?signin=expired")
            return
        }
        // 0.18.64 a session of this browser's own, never the web key
        response.addHeader(HttpHeaders.SET_COOKIE, sessions.cookie(request, sessions.open()).toString())
        response.status = HttpServletResponse.SC_FOUND
        response.setHeader(HttpHeaders.LOCATION, safeNext(next))
    }

    /** Signs this browser out: its session ends and the cookie goes; other browsers stay signed in. */
    @PostMapping("/api/web/sign-out")
    fun signOut(request: HttpServletRequest, response: HttpServletResponse): Map<String, Boolean> {
        sessions.end(key.cookieOf(request))
        response.addHeader(HttpHeaders.SET_COOKIE, sessions.cookie(request, "", Duration.ZERO).toString())
        return mapOf("ok" to true)
    }

    /** 0.18.64 signs every browser out (each session ends); a terminal app's web key and paired phones stay. */
    @PostMapping("/api/web/sign-out-everywhere")
    fun signOutEverywhere(request: HttpServletRequest, response: HttpServletResponse): Map<String, Any> {
        val n = sessions.endAll()
        response.addHeader(HttpHeaders.SET_COOKIE, sessions.cookie(request, "", Duration.ZERO).toString())
        return mapOf("ok" to true, "ended" to n)
    }

    /** Who this request is (internal, mcp, person, browser): the web asks once to know it is signed in. */
    @GetMapping("/api/web/me")
    fun me(request: HttpServletRequest): Map<String, Any?> = mapOf("signed_in" to true, "via" to request.getAttribute(LocalAuth.WHO_ATTR))

    companion object {

        /** Only a path on this keel ("/flow?x=1"), never another site ("//evil", "https://…"). */
        fun safeNext(next: String?): String {
            val n = next?.trim().orEmpty()
            // what the browser will see: it decodes %XX and drops tab and newline, so "/%09/evil.com" becomes "//evil.com"
            var seen = n
            repeat(3) { seen = runCatching { java.net.URLDecoder.decode(seen.replace("+", "%2B"), Charsets.UTF_8) }.getOrElse { return "/" } }
            if (seen.any { it < ' ' || it == '\u007f' || it == '\\' }) return "/"
            return if (n.startsWith("/") && seen.startsWith("/") && !seen.startsWith("//") && !seen.contains("://")) n else "/"
        }
    }
}
