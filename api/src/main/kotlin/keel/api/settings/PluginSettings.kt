package keel.api.settings

/**
 * One plugin's own settings: `plugins.<name>.<key>` in the settings table, General and per project, read and written
 * without the prefix. keel does not check the values: the plugin does. Get one with [SettingsService.plugin].
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may use it.
 */
class PluginSettings internal constructor(val name: String, private val store: SettingsService) {

    /** The defaults its settings sections give (SettingKey.default). */
    fun defaults(): Map<String, Any?> = store.pluginDefaults(name)

    /** What is set for every project. */
    fun general(): Map<String, Any?> = store.pluginValues(SettingsService.GENERAL, name)

    /** What this project sets itself. */
    fun overrides(pid: String): Map<String, Any?> = store.pluginValues(SettingsService.project(pid), name)

    /** What applies to this project: defaults, then General, then the project's own. */
    fun effective(pid: String): Map<String, Any?> = defaults() + general() + overrides(pid)

    /** One value: for a project, or for every project when [pid] is null. Null when nobody set it and it has no default. */
    fun get(key: String, pid: String? = null): Any? = (if (pid == null) defaults() + general() else effective(pid))[key]

    /** `{ key: value | null }` for every project; null removes the key. Returns what General sets now. */
    fun updateGeneral(patch: Map<String, Any?>): Map<String, Any?> {
        store.updateGeneral(prefixed(patch))
        return general()
    }

    /** `{ key: value | null }` for one project; null removes its own value, so General applies again. */
    fun updateProject(pid: String, patch: Map<String, Any?>): Map<String, Any?> {
        store.updateProject(pid, prefixed(patch))
        return overrides(pid)
    }

    private fun prefixed(patch: Map<String, Any?>): Map<String, Any?> = patch.mapKeys { (k, _) -> SettingsService.pluginKey(name, k) }
}
