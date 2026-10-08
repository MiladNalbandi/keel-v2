package keel.api.settings

import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

/**
 * The Settings page's sections today (web pages/Settings.tsx), one bean each. Every key of [Settings] is in exactly one.
 * The plugins' fields (ci_on_failure, push_pr, branch_pattern) stay in Git until they move with their part.
 */
@Configuration
class CoreSettingsSections(private val mapper: ObjectMapper) {

    @Suppress("UNCHECKED_CAST")
    private val defaults: Map<String, Any?> by lazy { mapper.convertValue(Settings(), Map::class.java) as Map<String, Any?> }

    private fun section(id: String, title: String, order: Int, vararg keys: String) =
        SimpleSettingsSection(id, title, keys.map(::key), order)

    /** The key's type follows its default; a key with choices is a choice. */
    private fun key(name: String): SettingKey {
        val default = defaults[name]
        val choices = Settings.CHOICES[name]
        val type = when {
            choices != null -> "choice"
            default is Boolean -> "bool"
            default is Int || default is Long -> "int"
            default is Number -> "number"
            default is Map<*, *> -> "model"
            default is List<*> -> "list"
            else -> "text"
        }
        return SettingKey(name, type, default, choices?.toList(), name in Settings.GENERAL_ONLY)
    }

    @Bean
    fun modeSettings(): SettingsSection = section("mode", "What this keel does", 0, "keel_mode")

    @Bean
    fun flowSettings(): SettingsSection =
        section("flow", "Flow and gates", 10, "run_mode", "gates_mode", "keel_rules", "fix_attempts", "coverage_min")

    @Bean
    fun modelSettings(): SettingsSection =
        section("models", "Models", 20, "default_model", "implementer_model", "reviewer_model", "cheaper_model")

    @Bean
    fun budgetSettings(): SettingsSection = section("budget", "Budget", 30, "cap_tokens", "on_cap", "usage_warn", "usage_pause")

    @Bean
    fun gitSettings(): SettingsSection = section("git", "Git", 40,
        "branch_pattern", "web_lane_worktree", "commit_coauthor", "commit_author", "ci_on_failure", "push_pr")

    @Bean
    fun notifySettings(): SettingsSection = section("notify", "Notifications", 50, "notify")

    @Bean
    fun envSettings(): SettingsSection = section("env", "Environment", 60, "env_names", "mcp")
}
