package keel.api.common

import com.fasterxml.jackson.annotation.JsonInclude
import com.fasterxml.jackson.databind.DeserializationFeature
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.PropertyNamingStrategies
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory
import com.fasterxml.jackson.dataformat.yaml.YAMLGenerator
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.fasterxml.jackson.module.kotlin.readValue
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.io.File
import java.nio.file.Path
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

object Time {
    fun now(): String = DateTimeFormatter.ISO_INSTANT.format(Instant.now())
    fun iso(epochMillis: Long): String = DateTimeFormatter.ISO_INSTANT.format(Instant.ofEpochMilli(epochMillis))
}

object Slug {
    fun of(text: String): String {
        val s = text.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-')
        return s.ifBlank { "x" }.take(60)
    }
}

/** YAML mapper for workflow files and keel v1 yml/front matter. Snake case, nulls left out. */
object Yaml {
    val mapper: ObjectMapper = ObjectMapper(
        YAMLFactory()
            .disable(YAMLGenerator.Feature.WRITE_DOC_START_MARKER)
            .enable(YAMLGenerator.Feature.MINIMIZE_QUOTES)
            .enable(YAMLGenerator.Feature.ALWAYS_QUOTE_NUMBERS_AS_STRINGS),
    ).apply {
        findAndRegisterModules()
        propertyNamingStrategy = PropertyNamingStrategies.SNAKE_CASE
        setSerializationInclusion(JsonInclude.Include.NON_NULL)
        configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
    }

    /** Lenient read into a generic map; null if the text is not a YAML mapping. */
    fun readMap(text: String): Map<String, Any?>? = try {
        @Suppress("UNCHECKED_CAST")
        mapper.readValue(text, Map::class.java) as Map<String, Any?>?
    } catch (e: Exception) {
        null
    }
}

/** A plain JSON mapper for columns we store as text. Keys stay as written (maps), objects go snake case. */
object Json {
    val mapper: ObjectMapper = jacksonObjectMapper().apply {
        findAndRegisterModules()
        propertyNamingStrategy = PropertyNamingStrategies.SNAKE_CASE
        configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
    }

    fun write(value: Any?): String = mapper.writeValueAsString(value)
    inline fun <reified T> read(text: String): T = mapper.readValue(text)
    fun readMap(text: String?): Map<String, Any?> =
        if (text.isNullOrBlank()) emptyMap() else try { mapper.readValue(text) } catch (e: Exception) { emptyMap() }
    fun readList(text: String?): List<String> =
        if (text.isNullOrBlank()) emptyList() else try { mapper.readValue(text) } catch (e: Exception) { emptyList() }
}

data class ProcResult(val code: Int, val out: String, val err: String, val timedOut: Boolean = false) {
    val ok get() = code == 0 && !timedOut
}

/** Runs a program with a timeout. Never uses a shell unless the caller passes one. */
object Proc {
    /** Kills a process and everything it started (the children are listed first: once the parent is gone they are lost). */
    fun killTree(p: Process) {
        runCatching { p.descendants().toList().forEach { it.destroyForcibly() } }
        p.destroyForcibly()
    }

    /**
     * The command line that runs [shellLine] inside a pseudo-terminal with `script`: GNU/Linux `script -qfec <line> /dev/null`,
     * macOS (BSD) `script -q /dev/null sh -c <line>`.
     */
    fun inTerminal(shellLine: String, mac: Boolean = System.getProperty("os.name").orEmpty().lowercase().contains("mac")): List<String> =
        if (mac) listOf("script", "-q", "/dev/null", "sh", "-c", shellLine) else listOf("script", "-qfec", shellLine, "/dev/null")

    /**
     * 0.21.3 the git settings every git keel's api runs gets (the same as the engine's tools/git.py SAFE_ARGS): an agent
     * can write the project's .git/config (core.fsmonitor, core.hooksPath) or a hook, and the api's own `git status`
     * would run that program inside the api. No hook, no fsmonitor, no ext transport.
     */
    val GIT_SAFE_ARGS = listOf("-c", "protocol.ext.allow=never", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")

    /**
     * 0.21.3 the only variables of the api's own environment a child gets. The api holds KEEL_INTERNAL_TOKEN and the
     * keys of Connections in its environment; a git (or a program it starts: an fsmonitor, a credential helper) must
     * not see them. What the child needs beyond these the caller passes in `env`.
     */
    val CHILD_ENV = setOf(
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TZ", "TMPDIR",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
    )

    /**
     * 0.21.3 agents run as their own user in keel's group (engine tools/agent_user.py; docker/keel-start sets
     * KEEL_AGENT_USER and umask 077): what the api's git writes in .git stays readable and writable for the agent's git.
     */
    private val agentsApart = !System.getenv("KEEL_AGENT_USER").isNullOrBlank() && System.getenv("KEEL_NATIVE") != "1"

    /** `cmd` with [GIT_SAFE_ARGS] after the program when it is git (`git` or a path to it), else as it is. */
    fun hardened(cmd: List<String>, shared: Boolean = agentsApart): List<String> {
        val exe = cmd.firstOrNull() ?: return cmd
        if (exe != "git" && !exe.endsWith("/git")) return cmd
        return listOf(exe) + GIT_SAFE_ARGS + (if (shared) listOf("-c", "core.sharedRepository=group") else emptyList()) + cmd.drop(1)
    }

    /** The environment of a child: [CHILD_ENV] and LC_* of the api's own, then `env`, then keel's fixed git settings. */
    fun childEnv(env: Map<String, String> = emptyMap(), parent: Map<String, String> = System.getenv()): Map<String, String> {
        val out = parent.filterKeys { it in CHILD_ENV || it.startsWith("LC_") }.toMutableMap()
        out.putAll(env)
        out["GIT_TERMINAL_PROMPT"] = "0"
        out.putIfAbsent("GIT_ALLOW_PROTOCOL", "https:ssh:file")
        return out
    }

    /** Sets up `pb` the way [run] does: hardened git arguments are the caller's (see [hardened]); a clean environment. */
    fun cleanEnv(pb: ProcessBuilder, env: Map<String, String> = emptyMap()): ProcessBuilder {
        val e = pb.environment()
        val clean = childEnv(env, e.toMap())
        e.clear()
        e.putAll(clean)
        return pb
    }

    fun run(cmd: List<String>, cwd: Path? = null, timeoutSec: Long = 10, env: Map<String, String> = emptyMap()): ProcResult {
        return try {
            val pb = ProcessBuilder(hardened(cmd))
            if (cwd != null) pb.directory(cwd.toFile())
            cleanEnv(pb, env)
            val outFile = File.createTempFile("keel-out", ".txt")
            val errFile = File.createTempFile("keel-err", ".txt")
            try {
                pb.redirectOutput(outFile).redirectError(errFile).redirectInput(ProcessBuilder.Redirect.from(File("/dev/null")))
                val p = pb.start()
                val done = p.waitFor(timeoutSec, TimeUnit.SECONDS)
                if (!done) {
                    killTree(p)
                    return ProcResult(-1, outFile.readText(), errFile.readText(), timedOut = true)
                }
                ProcResult(p.exitValue(), outFile.readText(), errFile.readText())
            } finally {
                outFile.delete(); errFile.delete()
            }
        } catch (e: Exception) {
            ProcResult(-1, "", e.message ?: "could not start ${cmd.firstOrNull()}")
        }
    }

    /** Path of a program on PATH, or null. */
    fun which(name: String): String? {
        val path = System.getenv("PATH") ?: return null
        return path.split(File.pathSeparator).map { File(it, name) }.firstOrNull { it.isFile && it.canExecute() }?.absolutePath
    }
}

/** A tiny JSON document store (table kv). */
@Component
class KvStore(private val jdbc: JdbcTemplate) {
    fun getRaw(key: String): String? =
        jdbc.query("SELECT json FROM kv WHERE key = ?", { rs, _ -> rs.getString(1) }, key).firstOrNull()

    fun put(key: String, value: Any?) {
        jdbc.update(
            "INSERT INTO kv(key, json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET json = excluded.json",
            key, Json.write(value),
        )
    }

    fun delete(key: String) {
        jdbc.update("DELETE FROM kv WHERE key = ?", key)
    }

    final inline fun <reified T> get(key: String): T? = getRaw(key)?.let { runCatching { Json.read<T>(it) }.getOrNull() }
}

object Db {
    /** The saved JSON of one `settings` row (its scope), or null. (The `kv` table is [KvStore]'s.) */
    fun settingsJson(jdbc: JdbcTemplate, scope: String): String? =
        jdbc.query("SELECT json FROM settings WHERE scope = ?", { rs, _ -> rs.getString(1) }, scope).firstOrNull()

    /** Writes one `settings` row as JSON, whether or not it exists yet. */
    fun saveSettings(jdbc: JdbcTemplate, scope: String, value: Any?) {
        jdbc.update(
            "INSERT INTO settings(scope, json) VALUES (?, ?) ON CONFLICT(scope) DO UPDATE SET json = excluded.json",
            scope, Json.write(value),
        )
    }

    /** Runs an INSERT on one connection and returns the new rowid. */
    fun insertId(jdbc: JdbcTemplate, sql: String, vararg args: Any?): Long =
        jdbc.execute(org.springframework.jdbc.core.ConnectionCallback { con ->
            con.prepareStatement(sql).use { ps ->
                args.forEachIndexed { i, a -> ps.setObject(i + 1, a) }
                ps.executeUpdate()
            }
            con.createStatement().use { st -> st.executeQuery("SELECT last_insert_rowid()").use { rs -> rs.next(); rs.getLong(1) } }
        }) ?: 0L
}
