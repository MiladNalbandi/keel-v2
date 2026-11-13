package keel.api.connections

import keel.api.common.BadRequest
import keel.api.common.Hash
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import keel.api.common.Time
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
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
class SecretService(
    private val jdbc: JdbcTemplate,
    private val props: KeelProperties,
    private val kinds: ObjectProvider<ConnectionKind>,
    /** Local model servers and Vertex AI: their connection is the provider's key for the engine (a JSON). */
    private val servers: ObjectProvider<ModelServerService>,
) {
    private val random = SecureRandom()

    private val key: SecretKey by lazy {
        val bytes = if (props.secret.isNotBlank()) {
            Hash.sha256(props.secret.toByteArray())
        } else {
            val f = props.dataDir.resolve("master.key")
            if (Files.isRegularFile(f)) {
                Files.readAllBytes(f).also { require(it.size == 32) { "master.key must be 32 bytes" } }
            } else {
                val k = ByteArray(32).also { random.nextBytes(it) }
                keel.api.common.OwnerOnly.create(f, k)
                k
            }
        }
        SecretKeySpec(bytes, "AES")
    }

    fun hint(value: String): String = "…" + value.takeLast(3)

    /** Tokens and API keys never contain whitespace; a copy from a wrapped terminal line often does. keel's own tokens,
     *  and those a part's connection kind names (ConnectionKind.tokens: Code Review's GITLAB_TOKEN). */
    private fun clean(name: String, value: String): String =
        if (name in TOKEN_NAMES || kinds.stream().anyMatch { name in it.tokens }) value.filterNot { it.isWhitespace() } else value

    /** Refuses a key that cannot be right (PUT /api/secrets): a copy from a wrapped terminal line often has
     *  extra characters. Only the shapes keel knows; the message names the bad characters, never the value. */
    fun checkShape(name: String, raw: String) {
        val value = clean(name, raw)
        if (value.isEmpty()) return
        val claude = SHAPES[name]
        if (name !in TOKEN_NAMES) return
        val allowed: (Char) -> Boolean = if (claude != null) { c -> (c.isLetterOrDigit() && c.code < 128) || c in "-_" }
            else { c -> (c.isLetterOrDigit() && c.code < 128) || c in "-_.=" }
        val what = claude ?: name
        val bad = value.filterNot(allowed).toSet().joinToString("")
        if (bad.isNotEmpty()) throw BadRequest("The $what has characters a token never has ($bad).",
            "A token shown over two lines is often copied with extra characters. Copy it in one piece and save it again.")
        if (claude != null && !value.startsWith("sk-ant-")) throw BadRequest(
            "The $what does not start with sk-ant- (it has ${value.length} characters).", "Check that you pasted the right key.")
    }

    fun put(name: String, raw: String): String {
        checkName(name)
        val value = clean(name, raw)
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

    /**
     * The host the saved GitHub repo token belongs to: github.com, or the Enterprise host the person signed in to
     * (Connections › GitHub). keel sends that token to this host only.
     */
    fun githubHost(): String {
        val host = get("GITHUB_REPO_INFO")?.let { runCatching { com.fasterxml.jackson.databind.ObjectMapper().readTree(it).path("host").asText("") }.getOrNull() }
        return host?.trim()?.lowercase()?.takeIf { it.isNotEmpty() } ?: "github.com"
    }

    /** The API key for a provider: stored secret first, then the environment. */
    fun keyForProvider(provider: String): String? {
        if (provider in ModelServerService.PROVIDERS) return servers.ifAvailable?.engineConfig(provider)
        val name = KEY_NAMES[provider] ?: return null
        return (get(name) ?: System.getenv(name)?.takeIf { it.isNotBlank() })?.let { clean(name, it) }
    }

    /**
     * 0.18.64 keel's own logins and connections: the model keys (and the old CLI logins), the GitHub token, Vertex AI, the
     * local model servers' keys and the tokens a part's connection kind names (GitLab). An MCP server never gets one (McpService).
     */
    fun keelOwn(name: String): Boolean =
        name in TOKEN_NAMES || name in KEY_NAMES.values || name in OWN ||
            name.startsWith(ModelServerService.secretOf("")) || kinds.stream().anyMatch { name in it.tokens }

    /**
     * The keys a flow or a test needs for this model, under the names the engine reads (StartThread.keys). #101 keel's
     * own harness runs every model on its provider's API key, whatever mode was saved: no app login goes to the engine.
     */
    @Suppress("UNUSED_PARAMETER")
    fun engineKeys(provider: String, mode: String): Map<String, String> =
        apiKey(provider)?.let { mapOf(provider to it) } ?: emptyMap()

    /**
     * #101 the key keel's harness calls a model with: the provider's API key. GitHub Models also takes another GitHub
     * token (GitHub's own `gh models` uses it the same way): see [copilotToken].
     */
    fun apiKey(provider: String): String? = if (provider == "copilot") copilotToken()?.first else keyForProvider(provider)

    /** #101 where GitHub Models' token comes from, as Connections shows it: a saved key's hint, or where it was found. */
    fun copilotKeyHint(): String? = copilotToken()?.second

    /**
     * #101 GitHub Models' token and its hint: GITHUB_TOKEN (saved, else the environment), else GH_TOKEN (saved by the old
     * Copilot login, else the environment), else the token of keel's GitHub sign-in when it is for github.com (keel
     * sends an Enterprise host's token to that host only).
     */
    private fun copilotToken(): Pair<String, String?>? {
        for (name in listOf(KEY_NAMES.getValue("copilot"), GH_TOKEN)) {
            get(name)?.let { return clean(name, it) to hintOf(name) }
            System.getenv(name)?.takeIf { it.isNotBlank() }?.let { return clean(name, it) to "from the environment" }
        }
        if (githubHost() != "github.com") return null
        return get(GITHUB_SIGN_IN)?.let { clean(GITHUB_SIGN_IN, it) to "from the GitHub sign-in" }
    }

    private fun checkName(name: String) {
        if (!Regex("^[A-Za-z0-9_.-]{1,64}$").matches(name)) {
            throw BadRequest("A secret name may use letters, digits, _ . - (up to 64)")
        }
    }

    companion object {
        val KEY_NAMES = mapOf("claude" to "ANTHROPIC_API_KEY", "codex" to "OPENAI_API_KEY", "copilot" to "GITHUB_TOKEN")
        /** #101 the old Copilot CLI login's GitHub token: GitHub Models' token when no GITHUB_TOKEN is set. */
        const val GH_TOKEN = "GH_TOKEN"
        /** The token of keel's GitHub sign-in (GitHubLoginService.TOKEN). */
        private const val GITHUB_SIGN_IN = "GITHUB_REPO_TOKEN"
        /** Secret names whose value is a single token (whitespace is removed). */
        val TOKEN_NAMES = setOf("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", GH_TOKEN, GITHUB_SIGN_IN)
        /**
         * 0.18.64 keel's other own secrets ([keelOwn]): the GitHub sign-in's account, Vertex AI's credentials. #101 also the
         * old Claude and Codex CLI logins: unused now, but an old saved one never shows as a project's secret, never goes to
         * an MCP server or the engine, and can still be deleted.
         */
        private val OWN = setOf("CLAUDE_CODE_OAUTH_TOKEN", "CODEX_AUTH_JSON", "GITHUB_REPO_INFO", "VERTEX_CREDENTIALS", "GITLAB_TOKEN")
        /** Secrets with a known Claude shape (sk-ant-…, only letters, digits, - and _), and what they are called. */
        private val SHAPES = mapOf("ANTHROPIC_API_KEY" to "Anthropic API key")
    }
}
