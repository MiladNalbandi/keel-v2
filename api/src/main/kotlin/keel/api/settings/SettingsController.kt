package keel.api.settings

import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api")
class SettingsController(private val settings: SettingsService, private val projects: ProjectService) {

    @GetMapping("/settings/general")
    fun general(): Settings = settings.general()

    @PutMapping("/settings/general")
    fun updateGeneral(@RequestBody patch: Map<String, Any?>): Settings = settings.updateGeneral(patch)

    /** The Settings page's sections: core's and the plugins', with their keys, types and defaults. */
    @GetMapping("/settings/sections")
    fun sections(): List<SettingsSectionView> = settings.sections()

    /** A plugin's own settings for every project (written with PUT /settings/general, `plugins.<name>.<key>`). */
    @GetMapping("/settings/plugins/{name}")
    fun pluginGeneral(@PathVariable name: String): PluginSettingsView {
        val p = settings.plugin(name)
        val general = p.general()
        return PluginSettingsView(general, emptyMap(), p.defaults() + general)
    }

    @GetMapping("/projects/{pid}/settings")
    fun project(@PathVariable pid: String): ProjectSettings {
        projects.require(pid)
        return settings.forProject(pid)
    }

    @PutMapping("/projects/{pid}/settings")
    fun updateProject(@PathVariable pid: String, @RequestBody patch: Map<String, Any?>): ProjectSettings {
        projects.require(pid)
        return settings.updateProject(pid, patch)
    }

    /** A plugin's own settings for one project (written with PUT /projects/{pid}/settings, `plugins.<name>.<key>`). */
    @GetMapping("/projects/{pid}/settings/plugins/{name}")
    fun pluginProject(@PathVariable pid: String, @PathVariable name: String): PluginSettingsView {
        projects.require(pid)
        val p = settings.plugin(name)
        return PluginSettingsView(p.general(), p.overrides(pid), p.effective(pid))
    }
}
