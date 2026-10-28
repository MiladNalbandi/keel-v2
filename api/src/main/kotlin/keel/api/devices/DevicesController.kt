package keel.api.devices

import jakarta.servlet.http.HttpServletRequest
import keel.api.auth.AuthLimiter
import keel.api.auth.ClientIp
import keel.api.auth.LocalAuth
import keel.api.common.ApiException
import keel.api.common.Forbidden
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import keel.api.common.Time
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.info.BuildProperties
import org.springframework.http.HttpStatus
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController
import java.net.URLEncoder

data class PairRequest(val code: String? = null, val name: String? = null, val platform: String? = null)
data class AddressesRequest(val urls: List<DeviceAddress> = emptyList())

/**
 * Phone access (keel's Android app). The contract the app is built against:
 *  - POST /api/devices/pair (no other sign-in; [LocalAuth] lets it through) `{code, name, platform}` →
 *    `{token, device: {id, name, platform, createdAt}, keel: {name, version}}`; a wrong, old or used code → 403,
 *    too many wrong codes → 429.
 *  - then every call: `Authorization: Bearer <device token>`; GET /api/devices/me → `{device, keel}`.
 *  - a person (the browser's cookie or the web key) lists, removes and pairs devices: GET /api/devices,
 *    DELETE /api/devices/{id}, POST /api/devices/pairing → `{code, expiresAt, urls: [{kind, url}], qr}`.
 *  - keel2 (the internal token) tells keel where it is reachable: PUT /api/devices/addresses `{urls: [{kind, url}]}`.
 * The bodies are written as maps: their keys are camelCase as the app expects (the api's JSON is snake_case elsewhere).
 */
@RestController
class DevicesController(
    private val devices: DeviceStore,
    private val codes: PairingCodes,
    private val addresses: DeviceAddresses,
    private val limiter: AuthLimiter,
    private val props: KeelProperties,
    build: ObjectProvider<BuildProperties>,
) {
    private val version: String = build.ifAvailable?.version ?: "dev"

    private fun keel(): Map<String, String> = linkedMapOf("name" to keelName(), "version" to version)

    private fun keelName(): String = props.name.trim().ifEmpty { "keel" }

    @PostMapping("/api/devices/pair")
    fun pair(@RequestBody(required = false) body: PairRequest?, request: HttpServletRequest): Map<String, Any?> {
        if (limiter.blocked(AuthLimiter.Kind.PAIR, request)) {
            throw ApiException(HttpStatus.TOO_MANY_REQUESTS, "Too many wrong pairing codes",
                "Wait a minute, then scan a new code in keel: Settings › Access › Pair a phone.")
        }
        if (!codes.use(body?.code)) {
            limiter.fail(AuthLimiter.Kind.PAIR, request)
            throw Forbidden("This pairing code is wrong, used or expired", "Make a new one in keel: Settings › Access › Pair a phone.")
        }
        val name = (body?.name ?: "").trim().filter { it >= ' ' }.take(60).ifEmpty { "Phone" }
        val platform = (body?.platform ?: "").trim().lowercase().filter { it.isLetterOrDigit() || it == '-' }.take(20).ifEmpty { "android" }
        val (device, token) = devices.add(name, platform, ClientIp.of(request))
        return linkedMapOf("token" to token, "device" to device.brief(), "keel" to keel())
    }

    @GetMapping("/api/devices/me")
    fun me(request: HttpServletRequest): Map<String, Any?> {
        val id = request.getAttribute(LocalAuth.DEVICE_ATTR) as? String
            ?: throw Forbidden("This is for a paired device", "A phone calls it with its device token.")
        val d = devices.get(id) ?: throw Forbidden(LocalAuth.DEVICE_GONE, LocalAuth.DEVICE_GONE_HINT)
        return linkedMapOf("device" to d.brief(), "keel" to keel())
    }

    @GetMapping("/api/devices")
    fun list(request: HttpServletRequest): List<Map<String, Any?>> {
        personOnly(request)
        return devices.list().map { it.full() }
    }

    @DeleteMapping("/api/devices/{id}")
    fun remove(@PathVariable id: String, request: HttpServletRequest): Map<String, Any?> {
        personOnly(request)
        if (!devices.remove(id)) throw NotFound("No device $id", "It may be removed already.")
        return mapOf("ok" to true)
    }

    @PostMapping("/api/devices/pairing")
    fun pairing(request: HttpServletRequest): Map<String, Any?> {
        personOnly(request)
        val (code, until) = codes.make()
        val urls = reachable(request)
        val withQr = urls.map { linkedMapOf("kind" to it.kind, "url" to it.url, "qr" to qr(it.url, code)) }
        return linkedMapOf(
            "code" to code,
            "expiresAt" to Time.iso(until),
            "urls" to withQr,
            "qr" to withQr.first()["qr"],
            "name" to keelName(),
            "reachable" to urls.any { it.kind != "local" },
        )
    }

    /** What keel2 last sent; keel2 and a person may read it. */
    @GetMapping("/api/devices/addresses")
    fun getAddresses(request: HttpServletRequest): Map<String, Any?> {
        val who = request.getAttribute(LocalAuth.WHO_ATTR)
        if (who != "internal" && who !in PERSON) throw Forbidden("Only keel2 or a person can read this", null)
        return mapOf("urls" to addresses.list())
    }

    @PutMapping("/api/devices/addresses")
    fun putAddresses(@RequestBody body: AddressesRequest, request: HttpServletRequest): Map<String, Any?> {
        if (request.getAttribute(LocalAuth.WHO_ATTR) != "internal") {
            throw Forbidden("Only keel2 can set keel's addresses", "Run keel2 start --wifi, keel2 share tailscale or keel2 share tunnel.")
        }
        return mapOf("urls" to addresses.replace(body.urls))
    }

    /** keel2's addresses (Tailscale first: https and from anywhere), then this page's own address when it is local. */
    private fun reachable(request: HttpServletRequest): List<DeviceAddress> {
        val known = addresses.list().filter { it.kind != "local" }.sortedBy { DeviceAddresses.KINDS.indexOf(it.kind) }
        val host = request.serverName ?: "localhost"
        val port = request.serverPort
        val default = (request.scheme == "http" && port == 80) || (request.scheme == "https" && port == 443)
        val h = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
        val own = "${request.scheme}://$h${if (default) "" else ":$port"}"
        val local = if (known.none { it.url.equals(own, ignoreCase = true) }) listOf(DeviceAddress("local", own)) else emptyList()
        return known + local
    }

    private fun qr(url: String, code: String): String = "keel://pair?u=${enc(url)}&c=${enc(code)}&n=${enc(keelName())}"

    private fun enc(s: String): String = URLEncoder.encode(s, Charsets.UTF_8).replace("+", "%20")

    private fun personOnly(request: HttpServletRequest) {
        if (request.getAttribute(LocalAuth.WHO_ATTR) !in PERSON) {
            throw Forbidden("Only a person signed in to keel can manage devices", "Open keel in your browser: Settings › Access.")
        }
    }

    companion object {
        /** the browser (sign-in cookie) and the person's terminal app (web key); never a device, an MCP or keel's own token */
        val PERSON = setOf("browser", "person")
    }
}
