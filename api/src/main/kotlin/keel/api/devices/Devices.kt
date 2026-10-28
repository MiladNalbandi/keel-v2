package keel.api.devices

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import keel.api.common.BadRequest
import keel.api.common.Hash
import keel.api.common.KeelProperties
import keel.api.common.Time
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.net.URI
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.SecureRandom
import java.time.Duration
import java.util.concurrent.ConcurrentHashMap

/** A paired phone (table devices). The token itself is never kept: only its SHA-256. */
data class Device(
    val id: String,
    val name: String,
    val platform: String,
    val createdAt: String,
    val lastSeenAt: String?,
    val lastIp: String?,
) {
    /** What the app sees (pair, me): exactly these four fields. */
    fun brief(): Map<String, Any?> = linkedMapOf("id" to id, "name" to name, "platform" to platform, "createdAt" to createdAt)

    /** What Settings › Access lists. */
    fun full(): Map<String, Any?> = brief() + linkedMapOf("lastSeenAt" to lastSeenAt, "lastIp" to lastIp)
}

/**
 * The paired devices. A device token is `kd_` + 64 hex (32 random bytes); the prefix tells [keel.api.auth.LocalAuth]
 * it is a device's and not the web key. [byToken] looks it up by its hash; [seen] writes the last call at most once a
 * minute per device.
 */
@Component
class DeviceStore(private val jdbc: JdbcTemplate) {
    private val lastWrite = ConcurrentHashMap<String, Long>()

    fun list(): List<Device> = jdbc.query("SELECT * FROM devices ORDER BY created_at DESC, id", ::row)

    fun get(id: String): Device? = jdbc.query("SELECT * FROM devices WHERE id = ?", ::row, id).firstOrNull()

    /** A new device; returns it with its token (shown once, to the device that paired). */
    fun add(name: String, platform: String, ip: String?): Pair<Device, String> {
        val token = PREFIX + Hash.hex(ByteArray(32).also { random.nextBytes(it) })
        val id = "dev-" + Hash.hex(ByteArray(6).also { random.nextBytes(it) })
        val now = Time.now()
        jdbc.update(
            "INSERT INTO devices (id, name, platform, token_hash, created_at, last_seen_at, last_ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            id, name, platform, Hash.sha256Hex(token), now, now, ip,
        )
        lastWrite[id] = System.currentTimeMillis()
        return Device(id, name, platform, now, now, ip) to token
    }

    /** The device this token belongs to, or null (unknown, or removed). */
    fun byToken(token: String): Device? {
        if (!looksLike(token)) return null
        val hash = Hash.sha256Hex(token)
        val found = jdbc.query("SELECT * FROM devices WHERE token_hash = ?", { rs, _ -> row(rs, 0) to rs.getString("token_hash") }, hash)
            .firstOrNull() ?: return null
        return if (Hash.constantTimeEquals(found.second, hash)) found.first else null
    }

    /** Records a call (time and address), at most once a minute per device. */
    fun seen(device: Device, ip: String?, now: Long = System.currentTimeMillis()) {
        val last = lastWrite[device.id]
        if (last != null && now - last < SEEN_EVERY.toMillis() && ip == device.lastIp) return
        lastWrite[device.id] = now
        jdbc.update("UPDATE devices SET last_seen_at = ?, last_ip = ? WHERE id = ?", Time.iso(now), ip, device.id)
    }

    fun remove(id: String): Boolean {
        lastWrite.remove(id)
        return jdbc.update("DELETE FROM devices WHERE id = ?", id) > 0
    }

    private fun row(rs: java.sql.ResultSet, @Suppress("UNUSED_PARAMETER") n: Int) = Device(
        id = rs.getString("id"),
        name = rs.getString("name"),
        platform = rs.getString("platform"),
        createdAt = rs.getString("created_at"),
        lastSeenAt = rs.getString("last_seen_at"),
        lastIp = rs.getString("last_ip"),
    )

    companion object {
        const val PREFIX = "kd_"
        val SEEN_EVERY: Duration = Duration.ofMinutes(1)
        private val random = SecureRandom()
        fun looksLike(token: String?): Boolean = token != null && token.startsWith(PREFIX) && token.length == PREFIX.length + 64
    }
}

/** One-time pairing codes: 10 characters (no 0/O/1/I), valid 10 minutes, used once. */
@Component
class PairingCodes {
    private val codes = ConcurrentHashMap<String, Long>()

    /** A new code and when it expires (epoch millis). */
    fun make(now: Long = System.currentTimeMillis()): Pair<String, Long> {
        codes.entries.removeIf { it.value < now }
        if (codes.size >= MAX) codes.clear()
        val code = (1..LENGTH).map { ALPHABET[random.nextInt(ALPHABET.length)] }.joinToString("")
        val until = now + TTL.toMillis()
        codes[code] = until
        return code to until
    }

    /** True once for a code that is still fresh; the code is gone afterwards either way. */
    fun use(code: String?, now: Long = System.currentTimeMillis()): Boolean {
        val c = normalize(code) ?: return false
        val until = codes.remove(c) ?: return false
        return until >= now
    }

    fun clear() = codes.clear()

    companion object {
        val TTL: Duration = Duration.ofMinutes(10)
        const val LENGTH = 10
        const val MAX = 20
        const val ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        private val random = SecureRandom()

        /** As a person may type it: any case, with spaces or dashes. */
        fun normalize(code: String?): String? = code?.uppercase()?.filter { it.isLetterOrDigit() }?.takeIf { it.isNotEmpty() }
    }
}

/** An address a phone can reach this keel at. kind: wifi | tailscale | tunnel | local. */
data class DeviceAddress(val kind: String = "", val url: String = "")

/**
 * The addresses keel2 knows this keel is reachable at (the Wi-Fi URL of `keel2 start --wifi`, `keel2 share tailscale`,
 * `keel2 share tunnel`): keel2 sends the whole list after each start and share change (PUT /api/devices/addresses).
 * Kept in $KEEL_DATA/run/addresses.json, so it survives a restart. Their host names are keel's own too (LocalAuth).
 */
@Component
class DeviceAddresses(private val props: KeelProperties) {
    private val file get() = props.dataDir.resolve("run").resolve("addresses.json")
    private val mapper = jacksonObjectMapper()

    @Volatile private var cached: List<DeviceAddress>? = null

    fun list(): List<DeviceAddress> = cached ?: load().also { cached = it }

    /** The host names in the list (lower case), for LocalAuth's host check. */
    fun hosts(): Set<String> = list().mapNotNull { runCatching { URI(it.url).host?.lowercase() }.getOrNull() }.toSet()

    /** The origins in the list ("https://x.ts.net"), for LocalAuth's origin check. */
    fun origins(): Set<String> = list().map { it.url.lowercase() }.toSet()

    @Synchronized
    fun replace(items: List<DeviceAddress>): List<DeviceAddress> {
        val clean = items.map { validate(it) }.distinctBy { it.url }
        if (clean.size > 20) throw BadRequest("Too many addresses", "Send at most 20.")
        val f = file
        Files.createDirectories(f.parent)
        val tmp = f.resolveSibling("addresses.json.tmp")
        Files.writeString(tmp, mapper.writeValueAsString(clean))
        Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
        cached = clean
        return clean
    }

    private fun load(): List<DeviceAddress> = runCatching {
        val f = file
        if (!Files.isRegularFile(f)) emptyList() else mapper.readValue<List<DeviceAddress>>(Files.readString(f))
            .mapNotNull { runCatching { validate(it) }.getOrNull() }
    }.getOrDefault(emptyList())

    companion object {
        val KINDS = listOf("tailscale", "wifi", "tunnel", "local")

        /** kind is known; url is http(s)://host[:port] with nothing after it (a trailing slash is dropped). */
        fun validate(a: DeviceAddress): DeviceAddress {
            val kind = a.kind.trim().lowercase()
            if (kind !in KINDS) throw BadRequest("Unknown address kind '${a.kind}'", "Use one of: ${KINDS.joinToString(", ")}.")
            val url = a.url.trim().trimEnd('/')
            val u = runCatching { URI(url) }.getOrNull()
            val ok = u != null && (u.scheme == "http" || u.scheme == "https") && !u.host.isNullOrBlank() &&
                u.rawPath.isNullOrEmpty() && u.rawQuery == null && u.rawFragment == null && u.rawUserInfo == null
            if (!ok) throw BadRequest("Not a base URL: $url", "Send http(s)://host[:port], for example http://192.168.1.20:8088.")
            return DeviceAddress(kind, url)
        }
    }
}
