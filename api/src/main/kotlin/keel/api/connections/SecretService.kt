package keel.api.connections

import keel.api.common.BadRequest
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import keel.api.common.Time
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Secrets stored AES-GCM in the db. The master key comes from KEEL_SECRET, or is generated once
 * into $KEEL_DATA/master.key (0600). Values are never logged and never returned — only a hint.
 */
@Service
class SecretService(private val jdbc: JdbcTemplate, private val props: KeelProperties) {
    private val random = SecureRandom()

    private val key: SecretKey by lazy {
        val bytes = if (props.secret.isNotBlank()) {
            MessageDigest.getInstance("SHA-256").digest(props.secret.toByteArray())
        } else {
            val f = props.dataDir.resolve("master.key")
            if (Files.isRegularFile(f)) {
                Files.readAllBytes(f).also { require(it.size == 32) { "master.key must be 32 bytes" } }
            } else {
                val k = ByteArray(32).also { random.nextBytes(it) }
                Files.write(f, k)
                runCatching { Files.setPosixFilePermissions(f, PosixFilePermissions.fromString("rw-------")) }
                k
            }
        }
        SecretKeySpec(bytes, "AES")
    }

    fun hint(value: String): String = "…" + value.takeLast(3)

    fun put(name: String, value: String): String {
        checkName(name)
        if (value.isEmpty()) throw BadRequest("The value is empty")
        if (value.length > 65_536) throw BadRequest("The value is too long (64 KB at most)")
        val iv = ByteArray(12).also { random.nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, iv))
        cipher.updateAAD(name.toByteArray())
        val ct = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val hint = hint(value)
        jdbc.update(
            "INSERT INTO secrets(name, iv, ciphertext, hint, updated_at) VALUES (?, ?, ?, ?, ?) " +
                "ON CONFLICT(name) DO UPDATE SET iv = excluded.iv, ciphertext = excluded.ciphertext, hint = excluded.hint, updated_at = excluded.updated_at",
            name, iv, ct, hint, Time.now(),
        )
        return hint
    }

    fun delete(name: String) {
        checkName(name)
        if (jdbc.update("DELETE FROM secrets WHERE name = ?", name) == 0) throw NotFound("No secret called $name")
    }

    fun has(name: String): Boolean =
        (jdbc.queryForObject("SELECT COUNT(*) FROM secrets WHERE name = ?", Int::class.java, name) ?: 0) > 0

    fun hintOf(name: String): String? =
        jdbc.query("SELECT hint FROM secrets WHERE name = ?", { rs, _ -> rs.getString(1) }, name).firstOrNull()

    /** Decrypts for internal use only (engine calls). Never send this to the web. */
    fun get(name: String): String? {
        val row = jdbc.query("SELECT iv, ciphertext FROM secrets WHERE name = ?", { rs, _ -> rs.getBytes(1) to rs.getBytes(2) }, name)
            .firstOrNull() ?: return null
        return runCatching {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, row.first))
            cipher.updateAAD(name.toByteArray())
            String(cipher.doFinal(row.second), Charsets.UTF_8)
        }.getOrNull()
    }

    /** The API key for a provider: stored secret first, then the environment. */
    fun keyForProvider(provider: String): String? {
        val name = KEY_NAMES[provider] ?: return null
        return get(name) ?: System.getenv(name)?.takeIf { it.isNotBlank() }
    }

    /** The CLI login for a subscription provider (Claude setup-token, Codex auth.json, GitHub token): stored first, then the environment. */
    fun loginFor(provider: String): String? {
        val name = LOGIN_NAMES[provider] ?: return null
        return get(name) ?: System.getenv(name)?.takeIf { it.isNotBlank() }
    }

    /** The keys a flow or a test needs for this model, under the names the engine reads (StartThread.keys). */
    fun engineKeys(provider: String, mode: String): Map<String, String> =
        if (mode == "api") keyForProvider(provider)?.let { mapOf(provider to it) } ?: emptyMap()
        else loginFor(provider)?.let { mapOf(ENGINE_LOGIN_KEY.getValue(provider) to it) } ?: emptyMap()

    private fun checkName(name: String) {
        if (!Regex("^[A-Za-z0-9_.-]{1,64}$").matches(name)) {
            throw BadRequest("A secret name may use letters, digits, _ . - (up to 64)")
        }
    }

    companion object {
        val KEY_NAMES = mapOf("claude" to "ANTHROPIC_API_KEY", "codex" to "OPENAI_API_KEY", "copilot" to "GITHUB_TOKEN")
        val LOGIN_NAMES = mapOf("claude" to "CLAUDE_CODE_OAUTH_TOKEN", "codex" to "CODEX_AUTH_JSON", "copilot" to "GH_TOKEN")
        private val ENGINE_LOGIN_KEY = mapOf("claude" to "claude_oauth", "codex" to "codex_auth", "copilot" to "copilot")
    }
}
