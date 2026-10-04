package keel.api.dashboard

import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.KeelHome
import keel.api.common.KeelProperties
import keel.api.projects.ProjectService
import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.io.File
import java.io.IOException
import java.net.ConnectException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

data class DashboardInfo(val url: String, val started: Boolean)

/**
 * keel v1's dashboard (`keel dashboard`), run once in the background on 127.0.0.1:<port>
 * (KEEL_DASHBOARD_PORT, default 7391). The web opens it through the /keel-v1/ proxy.
 */
@Service
class KeelDashboardService(
    private val props: KeelProperties,
    private val home: KeelHome,
    private val projects: ProjectService,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private var process: Process? = null
    @Volatile private var lastPid: String? = null

    val port: Int get() = props.dashboardPort
    val base: String get() = "http://127.0.0.1:$port"

    val http: HttpClient = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1)
        .connectTimeout(Duration.ofSeconds(3))
        .followRedirects(HttpClient.Redirect.NEVER)
        .build()

    /** True when a keel dashboard answers /api/hello on the port. */
    fun running(): Boolean = try {
        val req = HttpRequest.newBuilder(URI.create("$base/api/hello")).timeout(Duration.ofSeconds(2)).GET().build()
        http.send(req, HttpResponse.BodyHandlers.discarding()).statusCode() == 200
    } catch (e: Exception) {
        false
    }

    @Synchronized
    fun ensure(pid: String?): DashboardInfo {
        if (pid != null) lastPid = pid
        if (running()) return DashboardInfo(URL, started = false)
        if (!home.installed()) {
            throw ApiException(HttpStatus.SERVICE_UNAVAILABLE, "keel v1 is not installed at ${home.path}", "Set KEEL_HOME to a keel checkout.")
        }
        val row = if (pid != null) projects.require(pid)
            else (lastPid?.let { runCatching { projects.require(it) }.getOrNull() }
                ?: projects.rows().firstOrNull { it.id != "demo" } ?: projects.rows().firstOrNull())
            ?: throw BadRequest("Add a project first", "keel's dashboard starts inside a project folder.")
        process?.takeIf { it.isAlive }?.destroy()

        val logFile = props.dataDir.resolve("keel-dashboard.log").toFile()
        val pb = ProcessBuilder(home.command("dashboard", "--port", port.toString()))
            .directory(File(row.root))
            .redirectErrorStream(true)
            .redirectOutput(ProcessBuilder.Redirect.to(logFile))
            .redirectInput(ProcessBuilder.Redirect.from(File("/dev/null")))
        pb.environment()["KEEL_DASHBOARD_PORT"] = port.toString()
        val p = try {
            pb.start()
        } catch (e: IOException) {
            throw ApiException(HttpStatus.BAD_GATEWAY, "Could not start keel dashboard", e.message)
        }
        process = p
        val until = System.currentTimeMillis() + START_WAIT_MS
        while (System.currentTimeMillis() < until) {
            if (running()) return DashboardInfo(URL, started = true)
            if (!p.isAlive) break
            Thread.sleep(150)
        }
        if (running()) return DashboardInfo(URL, started = true)
        val tail = runCatching { logFile.readLines().filter { it.isNotBlank() }.takeLast(5).joinToString("\n") }.getOrDefault("")
        if (p.isAlive) p.destroy()
        log.warn("keel dashboard did not start: {}", tail)
        throw ApiException(HttpStatus.BAD_GATEWAY, "keel dashboard did not start", tail.ifBlank { "See ${logFile.path}." })
    }

    companion object {
        const val URL = "/keel-v1/"
        const val START_WAIT_MS = 8_000L
    }
}

/**
 * GET /api/keel-dashboard starts the dashboard; everything under /keel-v1/ is a reverse proxy to it.
 *
 * keel's server only answers when the Host header is `127.0.0.1:<port>`, so the incoming Host is
 * not copied (the client sets it from the target URL). Its page fetches absolute paths
 * (`/api/map`, `/api/console/`, `/events`); those are rewritten to `/keel-v1/...` in the HTML.
 */
@RestController
class KeelDashboardController(private val dashboard: KeelDashboardService) {

    @GetMapping("/api/keel-dashboard")
    fun start(@RequestParam(required = false) project: String?): DashboardInfo = dashboard.ensure(project?.takeIf { it.isNotBlank() })

    @RequestMapping("/keel-v1", "/keel-v1/**")
    fun proxy(req: HttpServletRequest, res: HttpServletResponse) {
        val uri = req.requestURI.removePrefix(req.contextPath)
        if (uri == "/keel-v1") {
            res.sendRedirect("/keel-v1/" + (req.queryString?.let { "?$it" } ?: ""))
            return
        }
        val path = uri.removePrefix("/keel-v1").ifEmpty { "/" }
        val target = URI.create(dashboard.base + path + (req.queryString?.let { "?$it" } ?: ""))

        val body = if (req.method in setOf("GET", "HEAD", "DELETE", "OPTIONS")) {
            HttpRequest.BodyPublishers.noBody()
        } else {
            HttpRequest.BodyPublishers.ofByteArray(req.inputStream.readNBytes(MAX_BODY))
        }
        val b = HttpRequest.newBuilder(target).method(req.method, body)
        for (name in req.headerNames.toList()) {
            val lower = name.lowercase()
            if (lower in SKIP_REQUEST) continue
            for (v in req.getHeaders(name).toList()) {
                // keel checks Origin on console POSTs: it must be its own origin.
                b.header(name, if (lower == "origin") dashboard.base else v)
            }
        }
        val request = b.build()
        val upstream = try {
            dashboard.http.send(request, HttpResponse.BodyHandlers.ofInputStream())
        } catch (e: ConnectException) {
            // Not running (keel was restarted since it was opened): start it and try once more.
            try {
                dashboard.ensure(null)
                dashboard.http.send(request, HttpResponse.BodyHandlers.ofInputStream())
            } catch (e2: ApiException) {
                return error(res, "keel v1's dashboard could not start: ${e2.message}", "See Repo › Open in keel v1, or keel2 logs.")
            } catch (e2: IOException) {
                return error(res, "keel v1's dashboard could not start", e2.message)
            }
        } catch (e: IOException) {
            return error(res, "keel dashboard did not answer", e.message)
        }

        res.status = upstream.statusCode()
        val type = upstream.headers().firstValue("content-type").orElse("")
        val html = type.startsWith("text/html")
        upstream.headers().map().forEach { (name, values) ->
            val lower = name.lowercase()
            if (lower in SKIP_RESPONSE || (html && lower == "content-length")) return@forEach
            if (lower == "location") {
                values.forEach { res.addHeader(name, rewriteLocation(it)) }
                return@forEach
            }
            values.forEach { res.addHeader(name, it) }
        }
        upstream.body().use { input ->
            if (html) {
                val text = rewriteHtml(String(input.readAllBytes(), Charsets.UTF_8))
                val bytes = text.toByteArray(Charsets.UTF_8)
                res.setContentLength(bytes.size)
                res.outputStream.write(bytes)
                res.outputStream.flush()
                return
            }
            // Stream as it comes: /events is a server-sent events stream that stays open.
            val out = res.outputStream
            val buf = ByteArray(8192)
            try {
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    out.flush()
                }
            } catch (e: IOException) {
                // The browser went away; closing `input` drops the upstream connection.
            }
        }
    }

    private fun rewriteLocation(loc: String): String = when {
        loc.startsWith(dashboard.base) -> PREFIX + loc.removePrefix(dashboard.base)
        loc.startsWith("/") && !loc.startsWith("$PREFIX/") -> PREFIX + loc
        else -> loc
    }

    private fun error(res: HttpServletResponse, message: String, hint: String?) {
        res.status = HttpStatus.BAD_GATEWAY.value()
        res.contentType = "application/json"
        val json = keel.api.common.Json.write(mapOf("error" to message, "hint" to hint))
        res.outputStream.write(json.toByteArray(Charsets.UTF_8))
    }

    companion object {
        const val PREFIX = "/keel-v1"
        const val MAX_BODY = 1024 * 1024
        val SKIP_REQUEST = setOf(
            "host", "connection", "content-length", "expect", "upgrade", "keep-alive", "transfer-encoding", "te", "trailer",
            "proxy-connection", "proxy-authorization", "accept-encoding", "cookie", "authorization",
        )
        val SKIP_RESPONSE = setOf("connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate")

        /** Points the page's absolute fetches (`/api/...`, `/events`) at the proxy. */
        fun rewriteHtml(html: String): String {
            var out = html
            for (q in listOf("'", "\"", "`")) {
                out = out.replace("$q/api/", "$q$PREFIX/api/").replace("$q/events", "$q$PREFIX/events")
            }
            return out
        }
    }
}
