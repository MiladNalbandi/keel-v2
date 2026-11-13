package keel.api.settings

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Db
import keel.api.common.Json
import keel.api.pluginhost.PluginHost
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.ApplicationEventPublisher
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class ProjectSettings(
    val general: Settings,
    val overrides: Map<String, Any?>,
    val effective: Settings,
    /** B15 the General keys someone saved (the rest are keel's defaults): a cap the person set is never raised. */
    val generalSaved: Set<String> = emptySet(),
)

/** One plugin's own settings for the web, without the prefix. For General, overrides is empty. */
data class PluginSettingsView(val general: Map<String, Any?>, val overrides: Map<String, Any?>, val effective: Map<String, Any?>)

/** A section as GET /api/settings/sections shows it. */
data class SettingsSectionView(val id: String, val title: String, val order: Int, val keys: List<SettingKey>)

/**
 * General settings, plus per-project overrides. Resolution: general → project override (null = use general).
 * A plugin's own settings sit next to them as `plugins.<name>.<key>` (see [plugin]): any such key is taken, core's keys
 * keep their checks, and [Settings] and the overrides show core's keys only.
 */
/**
 * Settings were saved: General (projectId null) or one project's own. [keys] = the keys the change named. Part of keel's
 * api SDK: a plugin whose engine part needs its settings while flows run listens (the Supervisor's switch).
 */
data class SettingsChanged(val projectId: String?, val keys: Set<String>)

@Service
class SettingsService(
    private val jdbc: JdbcTemplate,
    private val mapper: ObjectMapper,
    private val sectionBeans: ObjectProvider<SettingsSection>,
    private val events: ObjectProvider<ApplicationEventPublisher>,
) {

    /** A scope's saved values; a key keel no longer has (v0.17.2 keel_rules) is left out and dropped at the next save. */
    private fun raw(scope: String): Map<String, Any?> =
        (Db.settingsJson(jdbc, scope)?.let { Json.readMap(it) } ?: emptyMap()).filterKeys { it in Settings.KEYS || isPluginKey(it) }

    private fun save(scope: String, value: Map<String, Any?>) {
        Db.saveSettings(jdbc, scope, value)
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

    /** Someone saved [key] in Settings › General (it is not keel's default). */
    fun isGeneralSet(key: String): Boolean = raw(GENERAL).containsKey(key)

    /** B15 someone saved [key] for project [pid], or in General: a value the person chose, not keel's default. */
    fun isSet(pid: String, key: String): Boolean = raw(project(pid)).containsKey(key) || isGeneralSet(key)

    fun updateGeneral(patch: Map<String, Any?>): Settings {
        check(patch, allowNull = false)
        val merged = patched(raw(GENERAL), patch)
        val result = fromMap(defaultsMap() + core(merged)) // validates types
        save(GENERAL, merged)
        events.ifAvailable { it.publishEvent(SettingsChanged(null, patch.keys)) }
        return result
    }

    fun overrides(pid: String): Map<String, Any?> = core(raw(project(pid)))

    fun forProject(pid: String): ProjectSettings {
        val general = general()
        val overrides = overrides(pid)
        val effective = fromMap(toMap(general) + overrides)
        return ProjectSettings(general, overrides, effective, core(raw(GENERAL)).keys)
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
        events.ifAvailable { it.publishEvent(SettingsChanged(pid, patch.keys)) }
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
