package keel.api.settings

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Json
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class ProjectSettings(val general: Settings, val overrides: Map<String, Any?>, val effective: Settings)

/** General settings, plus per-project overrides. Resolution: general → project override (null = use general). */
@Service
class SettingsService(private val jdbc: JdbcTemplate, private val mapper: ObjectMapper) {

    private fun raw(scope: String): Map<String, Any?> =
        jdbc.query("SELECT json FROM settings WHERE scope = ?", { rs, _ -> rs.getString(1) }, scope)
            .firstOrNull()?.let { Json.readMap(it) } ?: emptyMap()

    private fun save(scope: String, value: Map<String, Any?>) {
        jdbc.update(
            "INSERT INTO settings(scope, json) VALUES (?, ?) ON CONFLICT(scope) DO UPDATE SET json = excluded.json",
            scope, Json.write(value),
        )
    }

    private fun defaultsMap(): Map<String, Any?> = toMap(Settings())

    private fun toMap(s: Settings): Map<String, Any?> {
        @Suppress("UNCHECKED_CAST")
        return mapper.convertValue(s, Map::class.java) as Map<String, Any?>
    }

    private fun fromMap(m: Map<String, Any?>): Settings = try {
        mapper.convertValue(m, Settings::class.java)
    } catch (e: IllegalArgumentException) {
        throw BadRequest("A setting has the wrong type", e.cause?.message?.take(200) ?: e.message)
    }

    fun general(): Settings = fromMap(defaultsMap() + raw(GENERAL))

    fun updateGeneral(patch: Map<String, Any?>): Settings {
        check(patch, allowNull = false)
        val merged = raw(GENERAL) + patch
        val result = fromMap(defaultsMap() + merged) // validates types
        save(GENERAL, merged)
        return result
    }

    fun overrides(pid: String): Map<String, Any?> = raw(project(pid))

    fun forProject(pid: String): ProjectSettings {
        val general = general()
        val overrides = overrides(pid)
        val effective = fromMap(toMap(general) + overrides)
        return ProjectSettings(general, overrides, effective)
    }

    fun effective(pid: String): Settings = forProject(pid).effective

    /** `{ key: value | null }` — null removes the override, so the project follows General again. */
    fun updateProject(pid: String, patch: Map<String, Any?>): ProjectSettings {
        check(patch, allowNull = true)
        val general = patch.keys.intersect(Settings.GENERAL_ONLY)
        if (general.isNotEmpty()) throw BadRequest("${general.joinToString()} is a setting of the whole keel", "Change it in Settings › General.")
        val next = overrides(pid).toMutableMap()
        for ((k, v) in patch) if (v == null) next.remove(k) else next[k] = v
        fromMap(toMap(general()) + next) // validates types
        save(project(pid), next)
        return forProject(pid)
    }

    private fun check(patch: Map<String, Any?>, allowNull: Boolean) {
        val unknown = patch.keys - Settings.KEYS.toSet()
        if (unknown.isNotEmpty()) throw BadRequest("Unknown setting: ${unknown.joinToString()}", "Known settings: ${Settings.KEYS.joinToString()}")
        for ((k, v) in patch) {
            if (v == null && !allowNull) throw BadRequest("$k cannot be empty in General settings")
            if (v != null && k in setOf("usage_warn", "usage_pause") && (v.toString().toDoubleOrNull() ?: -1.0) !in 0.0..1.0) {
                throw BadRequest("$k must be a number from 0 to 1", "For example 0.8 for 80%.")
            }
            if (v != null && k == "commit_author" && v.toString().isNotBlank() && !Settings.AUTHOR.matches(v.toString())) {
                throw BadRequest("commit_author must look like Name <email>", "For example: Ada Lovelace <ada@example.com>. Leave it empty for the project's git name.")
            }
            val choices = Settings.CHOICES[k]
            if (v != null && choices != null && v.toString() !in choices) {
                throw BadRequest("$k cannot be \"$v\"", "Pick one of: ${choices.joinToString()}")
            }
        }
    }

    companion object {
        const val GENERAL = "general"
        fun project(pid: String) = "project:$pid"
    }
}
