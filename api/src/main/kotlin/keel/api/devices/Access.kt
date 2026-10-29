package keel.api.devices

import keel.api.common.KeelProperties
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import java.net.URI

/**
 * keel 0.18.66 Settings › Access › Where keel answers. keel itself cannot change where it listens (keel2 starts the
 * container with the port published on 127.0.0.1 or on the network); it reads what keel2 told it:
 *   KEEL_BIND        the address keel2 started it on (127.0.0.1, or 0.0.0.0 for --wifi); keel2 0.18.66 passes it into
 *                    the container (native mode always had it). An older keel2 sends none: then the Wi-Fi addresses say it.
 *   the addresses    keel2's PUT /api/devices/addresses after each start and share change (Wi-Fi, Tailscale, tunnel)
 *   KEEL_ALLOWED_HOSTS  this computer's own name on the network (server.local) when it listens there
 * mode: "wifi" when keel listens on the network, else "tailscale" when keel2 shares it there, else "local".
 * The page shows the choice and the exact keel2 command for it; saving it from the page needs a host helper (later).
 */
data class AccessView(
    val mode: String,
    val bind: String?,
    val wifi: List<String>,
    val names: List<String>,
    val tailscale: String?,
    val tunnel: String?,
    val native: Boolean,
    /** the main project folder on the person's computer, for `keel2 start <folder>`; null when keel2 did not say */
    val folder: String?,
    /** 0.18.68 keel2's absolute path on the person's computer (KEEL2_PATH), for the MCP snippets; null: an older keel2 */
    val keel2Path: String? = null,
)

@RestController
class AccessController(private val addresses: DeviceAddresses, private val props: KeelProperties) {

    @GetMapping("/api/access")
    fun access(): AccessView {
        val known = addresses.list()
        val wifi = known.filter { it.kind == "wifi" }.map { it.url }
        val bind = System.getenv("KEEL_BIND")?.trim()?.ifEmpty { null }
        val onNetwork = if (bind != null) bind != "127.0.0.1" else wifi.isNotEmpty()
        val port = wifi.firstNotNullOfOrNull { runCatching { URI(it).port }.getOrNull()?.takeIf { p -> p > 0 } }
        val names = if (!onNetwork || port == null) emptyList() else props.allowedHosts.split(',').map { it.trim() }
            .filter { it.endsWith(".local") && HOST.matches(it) }.distinct().map { "http://$it:$port" }
        val tailscale = known.firstOrNull { it.kind == "tailscale" }?.url
        val mode = when {
            onNetwork -> "wifi"
            tailscale != null -> "tailscale"
            else -> "local"
        }
        return AccessView(mode, bind, wifi, names, tailscale, known.firstOrNull { it.kind == "tunnel" }?.url,
            System.getenv("KEEL_NATIVE") == "1", props.hostWorkspace.trim().ifEmpty { null },
            System.getenv("KEEL2_PATH")?.trim()?.takeIf { it.startsWith("/") && PATH_OK.matches(it) })
    }

    companion object {
        private val HOST = Regex("^[A-Za-z0-9.-]{1,253}$")
        /** a path a snippet can carry as it is (no quote, no shell character) */
        private val PATH_OK = Regex("^[A-Za-z0-9._/~+-]{1,400}$")
    }
}
