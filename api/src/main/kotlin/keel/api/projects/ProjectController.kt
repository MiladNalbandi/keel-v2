package keel.api.projects

import keel.api.events.EventHub
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class RegisterProject(val root: String = "", val name: String? = null)

@RestController
@RequestMapping("/api/projects")
class ProjectController(private val projects: ProjectService, private val hub: EventHub) {

    @GetMapping
    fun list(): List<Project> = projects.rows().map { projects.view(it) }

    @PostMapping
    fun register(@RequestBody body: RegisterProject): Project {
        val row = projects.register(body.root, body.name)
        val view = projects.view(row)
        hub.publish(row.id, "project.changed", view)
        return view
    }

    @GetMapping("/{pid}")
    fun get(@PathVariable pid: String): Project = projects.view(projects.require(pid))

    // the index status and its rebuild (/{pid}/index, /{pid}/index/rebuild) are the Graph plugin's (plugins/graph)
}
