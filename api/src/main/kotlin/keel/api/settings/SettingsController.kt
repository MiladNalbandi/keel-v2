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
}
