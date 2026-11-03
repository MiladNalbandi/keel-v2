package keel.api.notify

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.io.Writer
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import java.util.Base64
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/** Why a send failed, in words that hold no address and no password. */
class SendFailed(message: String) : RuntimeException(message)

/** Where a chat webhook may point: https anywhere, plain http only on this computer or the local network. */
object WebhookAddress {
    fun check(url: String): URI {
        val uri = runCatching { URI(url.trim()) }.getOrNull()
        val host = uri?.host?.lowercase() ?: throw BadRequest("That is not a web address", "Paste the whole webhook address, it starts with https://")
        val http = uri.scheme?.lowercase()
        if (http != "https" && http != "http") throw BadRequest("A webhook address starts with https://")
        val addresses = runCatching { InetAddress.getAllByName(host).toList() }.getOrDefault(emptyList())
        if (addresses.any { it.isLinkLocalAddress || it.isAnyLocalAddress || it.isMulticastAddress })
            throw BadRequest("keel does not send to that address", "It is a machine's own or link-local address, not a chat service.")
        if (http == "http" && !addresses.all { it.isLoopbackAddress || it.isSiteLocalAddress })
            throw BadRequest("Over the internet a webhook must use https://", "The address holds a secret, so keel never sends it unencrypted.")
        return uri
    }
}

/** Posts one text to a chat's incoming webhook: Slack, Mattermost and most others read `text`, Discord reads `content`. */
class ChatSender(private val mapper: ObjectMapper) {
    private val http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(8)).followRedirects(HttpClient.Redirect.NEVER).build()

    fun send(url: String, text: String) {
        val uri = WebhookAddress.check(url)
        val key = if (uri.host.lowercase().let { it == "discord.com" || it.endsWith(".discord.com") }) "content" else "text"
        val request = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(15)).header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(mapOf(key to text)))).build()
        val status = try {
            http.send(request, HttpResponse.BodyHandlers.discarding()).statusCode()
        } catch (e: java.io.IOException) {
            throw SendFailed("The chat service cannot be reached (${e.javaClass.simpleName}).")     // the exception may quote the address
        }
        if (status !in 200..299) throw SendFailed("The chat service answered $status.")
    }
}

/** A mail server, as a channel keeps it. */
data class MailServer(val host: String, val port: Int, val security: String, val user: String, val password: String?)

/**
 * Sends one plain mail over SMTP. keel's api has no mail library and a plugin's jar carries none, and a message this
 * small needs only this much: EHLO, STARTTLS when asked, AUTH, MAIL, RCPT, DATA.
 *
 * Security is `tls` (the whole connection, usually port 465), `starttls` (usually 587), or `none`, which only a mail
 * server on this computer may use: a password is never sent unencrypted over a network.
 */
class MailSender {
    fun send(server: MailServer, from: String, to: List<String>, subject: String, body: String) {
        check(server)
        val socket = try {
            open(server)
        } catch (e: java.io.IOException) {
            throw SendFailed("The mail server ${server.host}:${server.port} cannot be reached (${e.javaClass.simpleName}).")
        }
        socket.use {
            try {
                talk(it, server, from, to, subject, body)
            } catch (e: java.io.IOException) {
                throw SendFailed("The talk with the mail server broke (${e.javaClass.simpleName}).")
            }
        }
    }

    fun check(server: MailServer) {
        if (server.security !in setOf("tls", "starttls", "none")) throw BadRequest("Security is tls, starttls or none")
        if (server.security == "none" && !isLocal(server.host))
            throw BadRequest("A mail server on the network needs tls or starttls", "keel never sends a password or a mail unencrypted over a network.")
        if (server.host.isBlank() || server.port !in 1..65535) throw BadRequest("Give the mail server's name and port")
    }

    private fun isLocal(host: String) = runCatching { InetAddress.getAllByName(host).all { it.isLoopbackAddress } }.getOrDefault(false)

    private fun open(server: MailServer): Socket {
        val raw = Socket()
        raw.soTimeout = TIMEOUT_MS
        raw.connect(InetSocketAddress(server.host, server.port), TIMEOUT_MS)
        return if (server.security == "tls") secure(raw, server.host, server.port) else raw
    }

    /** TLS over the open connection, checking the server's name against its certificate. */
    private fun secure(raw: Socket, host: String, port: Int): SSLSocket {
        val tls = (SSLSocketFactory.getDefault() as SSLSocketFactory).createSocket(raw, host, port, true) as SSLSocket
        tls.sslParameters = SSLParameters().apply { endpointIdentificationAlgorithm = "HTTPS" }
        tls.soTimeout = TIMEOUT_MS
        tls.startHandshake()
        return tls
    }

    private class Line(socket: Socket) {
        val input = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.UTF_8))
        val output: Writer = OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8)

        /** Reads a reply (its lines end with "NNN " on the last) and fails unless its code is one of [codes]. */
        fun expect(vararg codes: Int): List<String> {
            val lines = mutableListOf<String>()
            while (true) {
                val line = input.readLine() ?: throw SendFailed("The mail server closed the connection.")
                lines += line
                if (line.length < 4 || line[3] != '-') break
            }
            val code = lines.last().take(3).toIntOrNull()
            if (code !in codes.toList()) throw SendFailed("The mail server answered: ${lines.last().take(160)}")
            return lines
        }

        fun send(text: String) {
            output.write(text + "\r\n")
            output.flush()
        }
    }

    private fun talk(socket: Socket, server: MailServer, from: String, to: List<String>, subject: String, body: String) {
        var line = Line(socket)
        line.expect(220)
        line.send("EHLO keel")
        var hello = line.expect(250)
        if (server.security == "starttls") {
            if (hello.none { it.drop(4).uppercase().startsWith("STARTTLS") }) throw SendFailed("The mail server does not offer STARTTLS, so keel sends nothing.")
            line.send("STARTTLS")
            line.expect(220)
            line = Line(secure(socket, server.host, server.port))
            line.send("EHLO keel")
            hello = line.expect(250)
        }
        if (server.user.isNotBlank()) {
            val plain = "\u0000${server.user}\u0000${server.password.orEmpty()}"
            line.send("AUTH PLAIN " + Base64.getEncoder().encodeToString(plain.toByteArray()))
            line.expect(235)
        }
        line.send("MAIL FROM:<${address(from)}>")
        line.expect(250)
        to.forEach {
            line.send("RCPT TO:<${address(it)}>")
            line.expect(250, 251)
        }
        line.send("DATA")
        line.expect(354)
        val head = listOf("From: ${address(from)}", "To: ${to.joinToString(", ") { address(it) }}", "Subject: ${header(subject)}",
            "MIME-Version: 1.0", "Content-Type: text/plain; charset=UTF-8", "Content-Transfer-Encoding: 8bit")
        line.send(head.joinToString("\r\n") + "\r\n\r\n" + stuffed(body) + "\r\n.")
        line.expect(250)
        line.send("QUIT")
    }

    companion object {
        private const val TIMEOUT_MS = 15_000

        /** One line and no address tricks: a name that came from elsewhere can never add a header or a recipient. */
        fun header(s: String) = s.replace(Regex("[\\r\\n]+"), " ").trim().take(200)

        fun address(s: String): String {
            val a = s.trim()
            if (!Regex("^[^\\s<>@,;:\"]+@[^\\s<>@,;:\"]+$").matches(a)) throw BadRequest("\"${header(a)}\" is not a mail address")
            return a
        }

        /** A line that starts with a dot gets a second one (SMTP's rule), and lines end the way SMTP wants. */
        fun stuffed(body: String) = body.replace("\r\n", "\n").split("\n").joinToString("\r\n") { if (it.startsWith(".")) ".$it" else it }
    }
}
