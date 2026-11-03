package keel.api.common

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.time.Duration
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/** The texts a caller shows when a download fails (each caller keeps its own wording). */
class FetchTexts(
    val notHttp: () -> BadRequest,
    val invalidUrl: () -> BadRequest = notHttp,
    val failed: (String?) -> BadRequest,
    val status: (Int) -> BadRequest,
    val tooBig: () -> BadRequest,
)

/**
 * The one way keel downloads a text file for a person-given link (a workflow, a skill). It only does http and https,
 * resolves the host and refuses this computer and private networks (loopback, link-local such as the cloud metadata
 * address 169.254.169.254, private, multicast), follows redirects itself and checks every hop the same way, caps the
 * size, and puts a time limit on the whole download (headers and body).
 * 0.21.3 it connects to the very address it checked. Before, java.net.http resolved the host a second time to connect,
 * so a name that answers a public address first and 169.254.169.254 (or 127.0.0.1) next (DNS rebinding) got past the
 * check. Now each hop resolves once, checks every address, and opens the socket to a checked one itself (https: the
 * certificate is still checked for the host's name, SNI included). A plain HTTP/1.1 GET, Connection: close, no
 * compression: all a text download needs.
 */
object HttpFetch {
    /** A JVM property for tests that serve a file from 127.0.0.1; nothing sets it in keel (the rest of the rules stay on). */
    const val ALLOW_LOOPBACK_PROPERTY = "keel.test.fetch-allow-loopback"
    private const val MAX_HOPS = 5
    private const val MAX_HEADER_BYTES = 64 * 1024
    private val REDIRECTS = setOf(301, 302, 303, 307, 308)
    private val timer = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "keel-http-fetch-timer").apply { isDaemon = true } }

    fun refusedAddress(): BadRequest = BadRequest("That link points to this computer or a private network",
        "Use a public http or https link.")

    /** True for an address a link may not point to. */
    fun blocked(a: InetAddress): Boolean {
        if (a.isAnyLocalAddress || a.isLoopbackAddress || a.isLinkLocalAddress || a.isSiteLocalAddress || a.isMulticastAddress) return true
        val b = a.address
        return when (a) {
            is Inet4Address -> (b[0].toInt() and 0xff == 100 && b[1].toInt() and 0xc0 == 64) ||   // 100.64.0.0/10 shared address space
                b[0].toInt() == 0                                                                  // 0.0.0.0/8
            is Inet6Address -> b[0].toInt() and 0xfe == 0xfc                                      // fc00::/7 unique local
            else -> false
        }
    }

    /**
     * Downloads [url] as text. [allowLoopback] is for tests only (a local server); every other rule stays. [resolve]
     * is the name lookup (tests: a name that answers another address each time).
     */
    fun get(
        url: String, maxBytes: Int, texts: FetchTexts,
        connectTimeout: Duration = Duration.ofSeconds(5), totalTimeout: Duration = Duration.ofSeconds(20),
        allowLoopback: Boolean = System.getProperty(ALLOW_LOOPBACK_PROPERTY) == "true",
        resolve: (String) -> Array<InetAddress> = { InetAddress.getAllByName(it) },
    ): String {
        var uri = parse(url, texts)
        val deadline = System.nanoTime() + totalTimeout.toNanos()
        for (hop in 0..MAX_HOPS) {
            val addresses = checkedAddresses(uri, allowLoopback, resolve)
            val res = try {
                exchange(uri, addresses, connectTimeout, deadline, maxBytes)
            } catch (e: BadRequest) {
                throw e
            } catch (e: TooBig) {
                throw texts.tooBig()
            } catch (e: Exception) {
                throw texts.failed(if (System.nanoTime() >= deadline) "the server stopped sending" else e.message?.take(200))
            }
            if (res.status in REDIRECTS) {
                val next = res.location ?: throw texts.status(res.status)
                uri = parse(runCatching { uri.resolve(next).toString() }.getOrElse { throw texts.invalidUrl() }, texts)
                continue
            }
            if (res.status !in 200..299) throw texts.status(res.status)
            return String(res.body, Charsets.UTF_8)
        }
        throw texts.failed("too many redirects")
    }

    private fun parse(url: String, texts: FetchTexts): URI {
        val uri = try { URI(url) } catch (e: Exception) { throw texts.invalidUrl() }
        if (uri.scheme?.lowercase() !in setOf("http", "https") || uri.host.isNullOrBlank()) throw texts.notHttp()
        return uri
    }

    /** The host's addresses, each one checked: the download connects only to one of these. */
    private fun checkedAddresses(uri: URI, allowLoopback: Boolean, resolve: (String) -> Array<InetAddress>): List<InetAddress> {
        val host = uri.host.removePrefix("[").removeSuffix("]")
        val addresses = try { resolve(host).toList() } catch (e: Exception) { throw BadRequest("Could not find that host", uri.host) }
        if (addresses.isEmpty()) throw BadRequest("Could not find that host", uri.host)
        if (addresses.any { blocked(it) && !(allowLoopback && it.isLoopbackAddress) }) throw refusedAddress()
        return addresses
    }

    private class Response(val status: Int, val location: String?, val body: ByteArray)
    private class TooBig : IOException("too big")

    /** One GET to one of [addresses] (the first that answers), within [deadline]: the socket is closed when it passes. */
    private fun exchange(uri: URI, addresses: List<InetAddress>, connectTimeout: Duration, deadline: Long, maxBytes: Int): Response {
        val https = uri.scheme.lowercase() == "https"
        val host = uri.host.removePrefix("[").removeSuffix("]")
        val port = if (uri.port != -1) uri.port else if (https) 443 else 80
        var socket: Socket? = null
        var last: Exception? = null
        for (a in addresses) {
            val left = TimeUnit.NANOSECONDS.toMillis(deadline - System.nanoTime())
            if (left <= 0) break
            val s = Socket()
            try {
                s.connect(InetSocketAddress(a, port), minOf(connectTimeout.toMillis(), left).toInt().coerceAtLeast(1))
                socket = s
                break
            } catch (e: Exception) {
                runCatching { s.close() }
                last = e
            }
        }
        val raw = socket ?: throw (last ?: IOException("could not connect"))
        val watchdog = timer.schedule({ runCatching { raw.close() } }, (deadline - System.nanoTime()).coerceAtLeast(1_000_000), TimeUnit.NANOSECONDS)
        try {
            val conn: Socket = if (https) {
                val ssl = (SSLSocketFactory.getDefault() as SSLSocketFactory).createSocket(raw, host, port, true) as SSLSocket
                ssl.sslParameters = ssl.sslParameters.apply {
                    endpointIdentificationAlgorithm = "HTTPS"   // the certificate must be for the host's name
                    if (!ipLiteral(host)) serverNames = listOf(SNIHostName(host))
                }
                ssl.startHandshake()
                ssl
            } else raw
            val target = (uri.rawPath?.ifEmpty { null } ?: "/") + (uri.rawQuery?.let { "?$it" } ?: "")
            val hostHeader = uri.host + (if (uri.port != -1) ":${uri.port}" else "")
            val request = "GET $target HTTP/1.1\r\nHost: $hostHeader\r\nUser-Agent: keel\r\nAccept: */*\r\n" +
                "Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
            conn.getOutputStream().apply { write(request.toByteArray(Charsets.ISO_8859_1)); flush() }
            val input = BufferedInputStream(conn.getInputStream())
            val (status, headers) = readHead(input)
            val location = headers["location"]
            if (status in REDIRECTS || status !in 200..299) return Response(status, location, ByteArray(0))
            val body = when {
                headers["transfer-encoding"]?.lowercase()?.contains("chunked") == true -> readChunked(input, maxBytes)
                headers["content-length"] != null -> {
                    val n = headers["content-length"]!!.trim().toLongOrNull() ?: throw IOException("a broken Content-Length")
                    if (n > maxBytes) throw TooBig()
                    val b = input.readNBytes(n.toInt())
                    if (b.size < n) throw IOException("the body ended early")
                    b
                }
                else -> input.readNBytes(maxBytes + 1).also { if (it.size > maxBytes) throw TooBig() }
            }
            return Response(status, location, body)
        } finally {
            watchdog.cancel(false)
            runCatching { raw.close() }
        }
    }

    private fun ipLiteral(h: String): Boolean = h.contains(':') || h.matches(Regex("[0-9.]+"))

    /** The status line and the headers (names in lower case; the last one of a name counts). */
    private fun readHead(input: InputStream): Pair<Int, Map<String, String>> {
        var total = 0
        fun line(): String {
            val out = ByteArrayOutputStream()
            while (true) {
                val c = input.read()
                if (c == -1) { if (out.size() == 0) throw IOException("the server closed the connection"); break }
                if (++total > MAX_HEADER_BYTES) throw IOException("the response headers are too long")
                if (c == '\n'.code) break
                if (c != '\r'.code) out.write(c)
            }
            return out.toString(Charsets.ISO_8859_1)
        }
        val statusLine = line()
        val parts = statusLine.split(' ')
        val status = parts.getOrNull(1)?.toIntOrNull()
        if (!statusLine.startsWith("HTTP/") || status == null) throw IOException("not an http response")
        val headers = HashMap<String, String>()
        while (true) {
            val l = line()
            if (l.isEmpty()) break
            val i = l.indexOf(':')
            if (i > 0) headers[l.substring(0, i).trim().lowercase()] = l.substring(i + 1).trim()
        }
        if (status in 100..199) return readHead(input)   // 100 Continue and the like: the real answer follows
        return status to headers
    }

    private fun readChunked(input: InputStream, maxBytes: Int): ByteArray {
        val out = ByteArrayOutputStream()
        while (true) {
            val sizeLine = StringBuilder()
            while (true) {
                val c = input.read()
                if (c == -1) throw IOException("the body ended early")
                if (c == '\n'.code) break
                if (c != '\r'.code) sizeLine.append(c.toChar())
                if (sizeLine.length > 1024) throw IOException("a broken chunk")
            }
            val size = sizeLine.toString().substringBefore(';').trim().toLongOrNull(16) ?: throw IOException("a broken chunk")
            if (size == 0L) return out.toByteArray()
            if (out.size() + size > maxBytes) throw TooBig()
            val b = input.readNBytes(size.toInt())
            if (b.size < size) throw IOException("the body ended early")
            out.write(b)
            input.read(); input.read()   // the chunk's CRLF
        }
    }
}
