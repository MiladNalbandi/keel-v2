package keel.api.settings

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Json
import keel.api.pluginhost.PluginHost
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class ProjectSettings(val general: Settings, val overrides: Map<String, Any?>, val effective: Settings)

/** One plugin's own settings for the web, without the prefix. For General, overrides is empty. */
data class PluginSettingsView(val general: Map<String, Any?>, val overrides: Map<String, Any?>, val effective: Map<String, Any?>)

/** A section as GET /api/settings/sections shows it. */
data class SettingsSectionView(val id: String, val title: String, val order: Int, val keys: List<SettingKey>)

/**
 * General settings, plus per-project overrides. Resolution: general → project override (null = use general).
 * A plugin's own settings sit next to them as `plugins.<name>.<key>` (see [plugin]): any such key is taken, core's keys
 * keep their checks, and [Settings] and the overrides show core's keys only.
 */
@Service
class SettingsService(
    private val jdbc: JdbcTemplate,
    private val mapper: ObjectMapper,
    private val sectionBeans: ObjectProvider<SettingsSection>,
) {

    private fun raw(scope: String): Map<String, Any?> =
        jdbc.query("SELECT json FROM settings WHERE scope = ?", { rs, _ -> rs.getString(1) }, scope)
            .firstOrNull()?.let { Json.readMap(it) } ?: emptyMap()

    private fun save(scope: String, value: Map<String, Any?>) {
        jdbc.update(
            "INSERT INTO settings(scope, json) VALUES (?, ?) ON CONFLICT(scope) DO UPDATE SET json = excluded.json",
            scope, Json.write(value),
        )
    }

    /** Core's keys only: a plugin's own keys never reach [Settings]. */
    private fun core(m: Map<String, Any?>): Map<String, Any?> = m.filterKeys { !isPluginKey(it) }

    /** The stored keys with the patch applied: null removes a key. */
    private fun patched(stored: Map<String, Any?>, patch: Map<String, Any?>): Map<String, Any?> {
        val next = stored.toMutableMap()
        for ((k, v) in patch) if (v == null) next.remove(k) else next[k] = v
        return next
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

    fun general(): Settings = fromMap(defaultsMap() + core(raw(GENERAL)))

    fun updateGeneral(patch: Map<String, Any?>): Settings {
        check(patch, allowNull = false)
        val merged = patched(raw(GENERAL), patch)
        val result = fromMap(defaultsMap() + core(merged)) // validates types
        save(GENERAL, merged)
        return result
    }

    fun overrides(pid: String): Map<String, Any?> = core(raw(project(pid)))

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
        val next = patched(raw(project(pid)), patch)
        fromMap(toMap(general()) + core(next)) // validates types
        save(project(pid), next)
        return forProject(pid)
    }

    /** Core's keys get their checks; a plugin's own key only needs a good name, and null removes it (General too). */
    private fun check(patch: Map<String, Any?>, allowNull: Boolean) {
        patch.keys.firstOrNull { isPluginKey(it) && !goodPluginKey(it) }?.let {
            throw BadRequest("\"$it\" is not a plugin setting", "Name it plugins.<plugin>.<key>, for example plugins.ci.on_failure.")
        }
        val corePatch = core(patch)
        val unknown = corePatch.keys - Settings.KEYS.toSet()
        if (unknown.isNotEmpty()) throw BadRequest("Unknown setting: ${unknown.joinToString()}", "Known settings: ${Settings.KEYS.joinToString()}")
        for ((k, v) in corePatch) {
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

    // ---- a plugin's own settings, and the sections -------------------------------------------

    /** One plugin's own settings (`plugins.<name>.<key>`). */
    fun plugin(name: String): PluginSettings {
        if (!PluginHost.NAME.matches(name)) throw BadRequest("\"$name\" is not a plugin name", "Lower-case letters, digits and dashes.")
        return PluginSettings(name, this)
    }

    /** The plugin's keys stored for this scope, without the prefix. */
    internal fun pluginValues(scope: String, name: String): Map<String, Any?> {
        val prefix = "$PLUGIN_PREFIX$name."
        return raw(scope).filterKeys { it.startsWith(prefix) }.mapKeys { (k, _) -> k.removePrefix(prefix) }
    }

    /** The defaults the sections give the plugin's keys, without the prefix. */
    internal fun pluginDefaults(name: String): Map<String, Any?> {
        val prefix = "$PLUGIN_PREFIX$name."
        return sectionBeans.orderedStream().toList().flatMap { it.keys }.filter { it.key.startsWith(prefix) }
            .associate { it.key.removePrefix(prefix) to it.default }
    }

    /** Every settings section, by order. */
    fun sections(): List<SettingsSectionView> = sectionBeans.orderedStream().toList()
        .map { SettingsSectionView(it.id, it.title, it.order, it.keys) }
        .sortedWith(compareBy({ it.order }, { it.id }))

    companion object {
        const val GENERAL = "general"
        fun project(pid: String) = "project:$pid"

        /** A plugin's own setting is `plugins.<name>.<key>`. */
        const val PLUGIN_PREFIX = "plugins."
        private val PLUGIN_SETTING = Regex("^[a-z][a-z0-9_]{0,63}$")

        fun pluginKey(name: String, key: String) = "$PLUGIN_PREFIX$name.$key"
        fun isPluginKey(key: String) = key.startsWith(PLUGIN_PREFIX)

        /** A plugin name by the plugin host's rule, then a snake_case key. */
        private fun goodPluginKey(key: String): Boolean {
            val rest = key.removePrefix(PLUGIN_PREFIX)
            return PluginHost.NAME.matches(rest.substringBefore('.', "")) && PLUGIN_SETTING.matches(rest.substringAfter('.', ""))
        }
    }
}
