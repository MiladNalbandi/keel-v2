package keel.api.settings

/**
 * One setting of a section: its [key] as the settings endpoints take it (a plugin's own: `plugins.<name>.<key>`), its
 * [type] and its [default]. type: choice | bool | int | number | text | list | model.
 *
 * Part of keel's api SDK (keel-api-sdk).
 */
data class SettingKey(
    val key: String,
    val type: String,
    val default: Any? = null,
    /** For a choice: the values it may take. */
    val choices: List<String>? = null,
    /** Only in General settings: a project cannot override it. */
    val generalOnly: Boolean = false,
)

/**
 * A section of the Settings page: an id, a title and its keys. keel lists every SettingsSection bean at
 * GET /api/settings/sections, by [order]. For a plugin's own keys (`plugins.<name>.<key>`) the defaults here are what
 * [SettingsService.plugin] answers while nobody has set the key.
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may add one.
 */
interface SettingsSection {
    val id: String
    val title: String
    val order: Int get() = 100
    val keys: List<SettingKey>
}

/** A section given as plain values. */
data class SimpleSettingsSection(
    override val id: String,
    override val title: String,
    override val keys: List<SettingKey>,
    override val order: Int = 100,
) : SettingsSection
