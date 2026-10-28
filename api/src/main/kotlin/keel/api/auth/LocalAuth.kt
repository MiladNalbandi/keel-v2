package keel.api.auth

import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.common.ApiPath
import keel.api.common.ErrorBody
import keel.api.common.KeelProperties
import keel.api.devices.DeviceAddresses
import keel.api.devices.DeviceStore
import keel.api.mcp.McpLevelGuard
import keel.api.mcp.McpTokens
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.http.MediaType
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.net.URI

/**
 * v0.18.1 keel's local api proves every caller (docs/plans/agent-clients.md section 0). Until 0.18.0 a request without
 * the MCP headers passed with no check at all, so any local program, or a web page through the browser, could approve a
 * gate, start a flow or delete a secret.
 *
 * In this order, for every request:
 *  1. the Host must be keel's own (localhost, 127.0.0.1, [::1], KEEL_PUBLIC_URL's host, KEEL_ALLOWED_HOSTS): a web
 *     page cannot reach keel through DNS rebinding;
 *  2. the path is read the way the controllers read it ([ApiPath]); an api path written with `;`, `//` or an encoded `/` is
 *     refused (400); outside /api and /internal (the web app's files, plugins' web files, the sign-in link) nothing more is needed,
 *     and neither is it for GET /api/health;
 *  3. X-Keel-Mcp-Token = an MCP level token: keel's MCP servers ([McpLevelGuard] then checks the level, as before);
 *  4. X-Keel-Token = the internal token: the engine, keel2 and keel's own scripts;
 *  5. `Authorization: Bearer <web key>`: a person's terminal app (the keel TUI); `Authorization: Bearer kd_…`: a paired
 *     phone's device token ([DeviceStore]): the person's access, except the device list and /internal; a removed one: 403;
 *  6. the sign-in cookie (0.18.64: a browser's own session, [WebSessions]): the browser. A GET passes with the cookie;
 *     any other method also needs `X-Keel-Web: 1` (a header another site's page cannot send: keel answers no CORS
 *     preflight) and, when the browser sends one, an Origin that is keel's own;
 *  7. anything else: 401 with how to sign in (`keel2 open`).
 * A wrong token is refused (403), never treated as "no token"; a cookie never lifts an MCP call to a person's access.
 * Open without a sign-in: GET /api/health and POST /api/devices/pair (a phone's one-time pairing code).
 * Wrong web keys, cookies and device tokens are counted per client ([AuthLimiter]): too many in a minute → 429.
 * Host: besides the names above, an IP address (a phone on the Wi-Fi: DNS rebinding always comes with a name) and the
 * hosts keel2 registered ([DeviceAddresses]: the Tailscale and tunnel names).
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE - 10)   // before McpAudit and McpLevelGuard
class LocalAuth(
    private val props: KeelProperties,
    private val key: WebKey,
    private val tokens: McpTokens,
    private val mapper: ObjectMapper,
    private val devices: DeviceStore,
    private val addresses: DeviceAddresses,
    private val limiter: AuthLimiter,
    private val sessions: WebSessions,
) : OncePerRequestFilter() {

    override fun shouldNotFilterAsyncDispatch(): Boolean = true

    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        if (!hostAllowed(request.serverName)) {
            return refuse(response, HttpServletResponse.SC_FORBIDDEN, "This keel only answers on localhost",
                "Open it as http://localhost:<port>. For another name, set KEEL_PUBLIC_URL or KEEL_ALLOWED_HOSTS.")
        }
        val path = ApiPath.of(request)   // the path the controllers see, not the raw one (`/api;x/..`, `/%61pi/..`)
        if (!ApiPath.isApi(path)) return chain.doFilter(request, response)
        if (ApiPath.ambiguous(request)) {
            return refuse(response, HttpServletResponse.SC_BAD_REQUEST, "This path is written in an unusual way",
                "Use the plain path, for example /api/projects: no ';', '//', '.' segments or encoded '/' or '.'.")
        }
        if (open(request.method, path)) return chain.doFilter(request, response)
        // an MCP call is always judged as one, whatever else it carries (McpLevelGuard keeps it within its level)
        request.getHeader(McpLevelGuard.TOKEN_HEADER)?.let { t ->
            if (tokens.levelOf(t) != null) return pass(request, response, chain, "mcp")
            return refuse(response, HttpServletResponse.SC_FORBIDDEN, "Wrong MCP token",
                "keel made new tokens: start the MCP server again (your client's /mcp, or keel2 mcp).")
        }
        request.getHeader(INTERNAL_HEADER)?.let { t ->
            if (props.internalToken.isNotEmpty() && McpTokens.same(t, props.internalToken)) return pass(request, response, chain, "internal")
            return refuse(response, HttpServletResponse.SC_FORBIDDEN, "Wrong X-Keel-Token", HINT)
        }
        request.getHeader("Authorization")?.let { a ->
            if (limiter.blocked(AuthLimiter.Kind.SIGN_IN, request)) return tooMany(response)
            val bearer = if (a.startsWith("Bearer ")) a.removePrefix("Bearer ").trim() else ""
            if (bearer.startsWith(DeviceStore.PREFIX)) {
                val d = devices.byToken(bearer)
                if (d == null) {
                    limiter.fail(AuthLimiter.Kind.SIGN_IN, request)
                    return refuse(response, HttpServletResponse.SC_FORBIDDEN, DEVICE_GONE, DEVICE_GONE_HINT)
                }
                if (path.startsWith("/internal/")) {
                    return refuse(response, HttpServletResponse.SC_FORBIDDEN, "A device cannot call keel's internal routes", null)
                }
                devices.seen(d, ClientIp.of(request))
                request.setAttribute(DEVICE_ATTR, d.id)
                return pass(request, response, chain, "device")
            }
            if (key.matches(bearer)) return pass(request, response, chain, "person")
            limiter.fail(AuthLimiter.Kind.SIGN_IN, request)
            return refuse(response, HttpServletResponse.SC_FORBIDDEN, "Wrong sign-in key", HINT)
        }
        val cookie = key.cookieOf(request)
        if (cookie != null && limiter.blocked(AuthLimiter.Kind.SIGN_IN, request)) return tooMany(response)
        if (cookie != null && signedIn(cookie, request, response)) {
            if (request.method in SAFE) return pass(request, response, chain, "browser")
            if (request.getHeader(WEB_HEADER) != "1") {
                return refuse(response, HttpServletResponse.SC_FORBIDDEN, "This change did not come from keel's own page", RELOAD)
            }
            val origin = request.getHeader("Origin")
            if (origin != null && !originAllowed(origin, request)) {
                return refuse(response, HttpServletResponse.SC_FORBIDDEN, "This change came from another site ($origin)", RELOAD)
            }
            return pass(request, response, chain, "browser")
        }
        if (cookie != null) limiter.fail(AuthLimiter.Kind.SIGN_IN, request)
        refuse(response, HttpServletResponse.SC_UNAUTHORIZED, SIGN_IN, HINT, signIn = true)
    }

    /**
     * 0.18.64 a live session of this browser ([WebSessions]); its use moves its end, and the cookie is sent again then.
     * A cookie from before 0.18.64 held the web key itself: it is swapped for a session once, so nobody has to sign in again.
     */
    private fun signedIn(cookie: String, request: HttpServletRequest, response: HttpServletResponse): Boolean =
        when (sessions.check(cookie)) {
            WebSessions.Check.YES -> true
            WebSessions.Check.MOVED -> true.also {
                response.addHeader(org.springframework.http.HttpHeaders.SET_COOKIE, sessions.cookie(request, cookie).toString())
            }
            WebSessions.Check.NO -> key.matches(cookie).also { old ->
                if (old) response.addHeader(org.springframework.http.HttpHeaders.SET_COOKIE, sessions.cookie(request, sessions.open()).toString())
            }
        }

    private fun pass(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain, who: String) {
        request.setAttribute(WHO_ATTR, who)
        chain.doFilter(request, response)
    }

    private fun tooMany(response: HttpServletResponse) {
        response.setHeader("Retry-After", limiter.retryAfter().toString())
        refuse(response, 429, "Too many wrong sign-in tries", "Wait a minute and try again.")
    }

    private fun refuse(response: HttpServletResponse, status: Int, error: String, hint: String?, signIn: Boolean = false) {
        response.status = status
        response.contentType = MediaType.APPLICATION_JSON_VALUE
        if (signIn) response.setHeader(AUTH_HEADER, "sign-in")
        mapper.writeValue(response.outputStream, ErrorBody(error, hint))
    }

    private fun open(method: String, path: String): Boolean =
        ((method == "GET" || method == "HEAD") && path.trimEnd('/') in OPEN_GETS) || (method == "POST" && path.trimEnd('/') in OPEN_POSTS)

    /** localhost, 127.0.0.1, [::1] (any port), the host of KEEL_PUBLIC_URL, and KEEL_ALLOWED_HOSTS (comma-separated). */
    fun hostAllowed(host: String?): Boolean {
        val h = host?.trim()?.lowercase()?.removePrefix("[")?.removeSuffix("]") ?: return false
        if (h in LOCAL_HOSTS || h.endsWith(".localhost")) return true
        if (ipLiteral(h)) return true
        return h in extraHosts || h in addresses.hosts()
    }

    /** 192.168.1.20, ::1, fe80::1: an address, not a name (a name can be rebound to 127.0.0.1; an address cannot). */
    private fun ipLiteral(h: String): Boolean =
        IPV4.matches(h) && h.split('.').all { (it.toIntOrNull() ?: 256) <= 255 } || (h.contains(':') && IPV6.matches(h))

    private val extraHosts: Set<String> by lazy {
        val fromUrl = props.publicUrl.takeIf { it.isNotBlank() }?.let { runCatching { URI(it.trim()).host }.getOrNull() }
        (props.allowedHosts.split(',') + listOfNotNull(fromUrl)).map { it.trim().lowercase() }.filter { it.isNotEmpty() }.toSet()
    }

    /** keel's own origin (the scheme, host and port the request came to), KEEL_PUBLIC_URL's, or KEEL_DEV_ORIGINS. */
    private fun originAllowed(origin: String, request: HttpServletRequest): Boolean {
        val o = origin.trim().trimEnd('/').lowercase()
        val port = request.serverPort
        val own = "${request.scheme}://${request.serverName}".lowercase().let { base ->
            if ((request.scheme == "http" && port == 80) || (request.scheme == "https" && port == 443)) base else "$base:$port"
        }
        if (o == own) return true
        val listed = (props.devOrigins.split(',') + props.publicUrl + addresses.origins()).map { it.trim().trimEnd('/').lowercase() }.filter { it.isNotEmpty() }
        return o in listed.map { originOf(it) }
    }

    private fun originOf(url: String): String = runCatching {
        val u = URI(url)
        if (u.port == -1) "${u.scheme}://${u.host}" else "${u.scheme}://${u.host}:${u.port}"
    }.getOrDefault(url)

    companion object {
        const val INTERNAL_HEADER = "X-Keel-Token"
        const val WEB_HEADER = "X-Keel-Web"
        /** Set on the 401 a caller gets without any sign-in: the web shows its sign-in screen on it. */
        const val AUTH_HEADER = "X-Keel-Auth"
        /** Who the request proved to be: internal, mcp, person, browser or device (a paired phone). */
        const val WHO_ATTR = "keel.auth.who"
        const val SIGN_IN = "keel needs you to sign in"
        const val HINT = "Run keel2 open in a terminal: it opens keel signed in. (keel2 open --print prints the link.)"
        const val RELOAD = "Reload keel's page. If it keeps happening, run keel2 open."
        val SAFE = setOf("GET", "HEAD", "OPTIONS")
        val LOCAL_HOSTS = setOf("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1")
        val OPEN_GETS = setOf("/api/health")
        val OPEN_POSTS = setOf("/api/devices/pair")
        /** The paired device a request proved to be (its id), when WHO_ATTR is "device". */
        const val DEVICE_ATTR = "keel.auth.device"
        const val DEVICE_GONE = "This device was removed"
        const val DEVICE_GONE_HINT = "Pair it again in keel: Settings › Access"
        private val IPV4 = Regex("[0-9]{1,3}(\\.[0-9]{1,3}){3}")
        private val IPV6 = Regex("[0-9a-f:.]+")
    }
}
