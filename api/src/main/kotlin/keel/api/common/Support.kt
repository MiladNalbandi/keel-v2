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
    fun run(cmd: List<String>, cwd: Path? = null, timeoutSec: Long = 10, env: Map<String, String> = emptyMap()): ProcResult {
        return try {
            val pb = ProcessBuilder(cmd)
            if (cwd != null) pb.directory(cwd.toFile())
            pb.environment().putAll(env)
            pb.environment()["GIT_TERMINAL_PROMPT"] = "0"
            val outFile = File.createTempFile("keel-out", ".txt")
            val errFile = File.createTempFile("keel-err", ".txt")
            try {
                pb.redirectOutput(outFile).redirectError(errFile).redirectInput(ProcessBuilder.Redirect.from(File("/dev/null")))
                val p = pb.start()
                val done = p.waitFor(timeoutSec, TimeUnit.SECONDS)
                if (!done) {
                    p.destroyForcibly()
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
