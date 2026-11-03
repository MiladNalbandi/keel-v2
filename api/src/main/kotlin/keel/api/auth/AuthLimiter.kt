package keel.api.auth

import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import java.net.InetAddress
import java.util.concurrent.ConcurrentHashMap

/**
 * Brute-force protection for keel's sign-ins once keel is reachable beyond this computer (`keel2 start --wifi`,
 * `keel2 share tailscale|tunnel`): wrong tries are counted per client and per kind in a one-minute window. Over the
 * client's limit, or over the limit of all clients together (a client that changes its forwarded address each try),
 * a wrong try of that kind is refused with 429 until the window has passed.
 * 0.21.3 the credential is checked first, and only a wrong one is counted or refused: a right web key, cookie, device
 * token or sign-in code always passes. Before, [blocked] was asked first, so about 200 wrong tokens a minute from the
 * Wi-Fi or a tunnel (the limit of all clients together) locked everyone out, the person's own browser too. keel's
 * secrets are 128 bits and more, so a guess that gets through is no real risk. A phone's pairing code is shorter
 * (50 bits): [Kind.PAIR] is still refused before the code is checked (DevicesController).
 *
 * A direct call from this computer (loopback, no proxy header) is never counted or refused: it is the person's own
 * browser or terminal, and keel's own scripts (the internal token and MCP tokens are never counted either).
 */
@Component
class AuthLimiter {
    enum class Kind(val perClient: Int, val total: Int) {
        /** the web key (`Authorization: Bearer`), the sign-in cookie, a device token, a sign-in code */
        SIGN_IN(20, 200),
        /** a phone's pairing code: guessable in principle (50 bits), so far fewer tries */
        PAIR(5, 30),
    }

    private val fails = ConcurrentHashMap<String, ArrayDeque<Long>>()

    /** True when this client's wrong tries of [kind] are over the limit (or all clients' together are). */
    fun blocked(kind: Kind, request: HttpServletRequest, now: Long = System.currentTimeMillis()): Boolean {
        if (ClientIp.direct(request)) return false
        return count("${kind.name}|${ClientIp.of(request)}", now) >= kind.perClient || count("${kind.name}|*", now) >= kind.total
    }

    /** One wrong try of [kind] by this client. */
    fun fail(kind: Kind, request: HttpServletRequest, now: Long = System.currentTimeMillis()) {
        if (ClientIp.direct(request)) return
        if (fails.size > MAX_KEYS) fails.entries.removeIf { e -> synchronized(e.value) { e.value.none { it > now - WINDOW_MS } } }
        listOf("${kind.name}|${ClientIp.of(request)}", "${kind.name}|*").forEach { k ->
            val q = fails.computeIfAbsent(k) { ArrayDeque() }
            synchronized(q) {
                q.addLast(now)
                while (q.size > 1000) q.removeFirst()
            }
        }
    }

    /** Seconds until the oldest wrong try of the window is gone (for Retry-After). */
    fun retryAfter(): Long = WINDOW_MS / 1000

    fun clear() = fails.clear()

    private fun count(key: String, now: Long): Int {
        val q = fails[key] ?: return 0
        synchronized(q) {
            while (q.isNotEmpty() && q.first() <= now - WINDOW_MS) q.removeFirst()
            return q.size
        }
    }

    companion object {
        const val WINDOW_MS = 60_000L
        const val MAX_KEYS = 10_000
    }
}

/**
 * Who is calling, as an address. keel sees a local proxy as the caller when it is reached through `tailscale serve`
 * or a Cloudflare tunnel (127.0.0.1 natively, Docker's gateway in keel's container); then the proxy's header names the
 * real client: Cf-Connecting-Ip (cloudflared sets it, a client cannot), else the last X-Forwarded-For entry (the one the
 * nearest proxy added).
 * 0.21.3 those headers count only from where keel2's proxies call keel: loopback, the container's own gateway (read from
 * /proc/net/route, only inside a container) and KEEL_TRUSTED_PROXIES (comma-separated addresses). Before, any private
 * address was trusted, so a caller on the same Wi-Fi named another address in X-Forwarded-For on each try and was never
 * held back on its own.
 */
object ClientIp {
    /** Addresses trusted to name the client (tests set it; null = loopback, the gateway and KEEL_TRUSTED_PROXIES). */
    @Volatile var trustedForTests: Set<String>? = null

    fun of(request: HttpServletRequest): String {
        val remote = request.remoteAddr ?: ""
        if (!trustedProxy(remote)) return remote
        request.getHeader("Cf-Connecting-Ip")?.trim()?.takeIf { it.isNotEmpty() }?.let { return it.take(64) }
        request.getHeader("X-Forwarded-For")?.split(',')?.map { it.trim() }?.lastOrNull { it.isNotEmpty() }?.let { return it.take(64) }
        return remote
    }

    /** A call made on this computer itself, not passed on by a proxy. */
    fun direct(request: HttpServletRequest): Boolean =
        loopback(request.remoteAddr ?: "") && request.getHeader("Cf-Connecting-Ip") == null && request.getHeader("X-Forwarded-For") == null

    private fun loopback(ip: String): Boolean = ip == "127.0.0.1" || ip == "::1" || ip == "0:0:0:0:0:0:0:1" || ip.startsWith("127.")

    /** Where tailscale serve and cloudflared call keel from. */
    fun trustedProxy(ip: String): Boolean {
        if (loopback(ip)) return true
        val trusted = trustedForTests ?: configured
        return normal(ip) in trusted
    }

    private fun normal(ip: String): String =
        runCatching { if (ip.contains(':') || ip.matches(Regex("[0-9.]+"))) InetAddress.getByName(ip).hostAddress else ip }.getOrDefault(ip)

    private val configured: Set<String> by lazy {
        val listed = (System.getenv("KEEL_TRUSTED_PROXIES") ?: "").split(',').map { it.trim() }.filter { it.isNotEmpty() }
        (listed + listOfNotNull(containerGateway())).map { normal(it) }.toSet()
    }

    /** Inside a container (Docker, Podman): its default gateway, where the host's published port forwards from. */
    private fun containerGateway(): String? {
        if (!java.nio.file.Files.exists(java.nio.file.Path.of("/.dockerenv")) &&
            !java.nio.file.Files.exists(java.nio.file.Path.of("/run/.containerenv"))) return null
        return runCatching { gatewayOf(java.nio.file.Files.readAllLines(java.nio.file.Path.of("/proc/net/route"))) }.getOrNull()
    }

    /** The default route's gateway in /proc/net/route's form (hex, little-endian), as 172.17.0.1. */
    fun gatewayOf(routeLines: List<String>): String? {
        val hex = routeLines.drop(1).map { it.trim().split(Regex("\\s+")) }
            .firstOrNull { it.size > 2 && it[1] == "00000000" && it[2] != "00000000" }?.get(2) ?: return null
        val n = hex.toLongOrNull(16) ?: return null
        return (0 until 4).joinToString(".") { ((n shr (8 * it)) and 0xff).toString() }
    }
}
