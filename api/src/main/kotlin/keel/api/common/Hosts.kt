package keel.api.common

import java.net.URI

/**
 * 0.18.64 where a saved token may go. A connection's token is for one site: when its address moves to another host,
 * keel asks for the token again instead of sending the saved one there (Jira, GitLab, a local model server's key). A
 * token never goes over plain http:// to another machine: http:// is only for this machine (Jira, GitLab) and, for a
 * local model server, also for the local network ([localNetwork]).
 */
object Hosts {
    /** scheme://host:port in lower case (the port only when the URL names one); null for an address that is not a URL. */
    fun origin(url: String?): String? {
        val u = runCatching { URI(url?.trim().orEmpty()) }.getOrNull() ?: return null
        val host = u.host?.lowercase() ?: return null
        val scheme = u.scheme?.lowercase() ?: return null
        return if (u.port == -1) "$scheme://$host" else "$scheme://$host:${u.port}"
    }

    fun hostOf(url: String?): String? = runCatching { URI(url?.trim().orEmpty()).host?.lowercase() }.getOrNull()

    /** True when both addresses are the same site: same scheme, host and port. */
    fun sameSite(a: String?, b: String?): Boolean = origin(a) != null && origin(a) == origin(b)

    /** This machine: localhost, 127.0.0.0/8, ::1. */
    fun loopback(host: String?): Boolean {
        val h = host?.trim()?.lowercase()?.removePrefix("[")?.removeSuffix("]") ?: return false
        return h == "localhost" || h.endsWith(".localhost") || h == "::1" || h == "0:0:0:0:0:0:0:1" || V4.matchEntire(h)?.groupValues?.get(1) == "127"
    }

    /**
     * This machine or the local network: [loopback], host.docker.internal (the machine Docker runs on), a *.local name
     * (mDNS), and the private ranges 10/8, 172.16/12, 192.168/16, 169.254/16, fc00::/7, fe80::/10.
     */
    fun localNetwork(host: String?): Boolean {
        if (loopback(host)) return true
        val h = host?.trim()?.lowercase()?.removePrefix("[")?.removeSuffix("]") ?: return false
        if (h == "host.docker.internal" || h.endsWith(".local")) return true
        V4.matchEntire(h)?.let { m ->
            val (a, b) = m.groupValues[1].toInt() to m.groupValues[2].toInt()
            return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168) || (a == 169 && b == 254)
        }
        return h.contains(':') && (h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe8") || h.startsWith("fe9") ||
            h.startsWith("fea") || h.startsWith("feb"))
    }

    /** A token may go to this URL: https, or http to this machine (or, [lan], to the local network). */
    fun tokenSafe(url: String?, lan: Boolean = false): Boolean {
        val u = runCatching { URI(url?.trim().orEmpty()) }.getOrNull() ?: return false
        return when (u.scheme?.lowercase()) {
            "https" -> true
            "http" -> if (lan) localNetwork(u.host) else loopback(u.host)
            else -> false
        }
    }

    private val V4 = Regex("^(\\d{1,3})\\.(\\d{1,3})\\.\\d{1,3}\\.\\d{1,3}$")
}
