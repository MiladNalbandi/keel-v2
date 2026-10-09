package keel.api.projects

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.events.EventHub
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class RegisterProject(val root: String = "", val name: String? = null)

/** v0.15.7 many folders at once; `names` gives some of them a name (by their path as sent). */
data class RegisterProjects(val roots: List<String> = emptyList(), val names: Map<String, String> = emptyMap())

data class BulkAdded(val added: List<Project>, val skipped: List<Skipped>)

@RestController
@RequestMapping("/api/projects")
class ProjectController(private val projects: ProjectService, private val hub: EventHub, private val engine: EngineClient) {

    @GetMapping
    fun list(): List<Project> = projects.rows().map { projects.view(it) }

    @PostMapping
    fun register(@RequestBody body: RegisterProject): Project {
        val row = projects.register(body.root, body.name)
        val view = projects.view(row)
        hub.publish(row.id, "project.changed", view)
        return view
    }

    /** v0.15.7 Add projects: registers every ticked repo; one that is a project already, or not a folder, is skipped with why. */
    @PostMapping("/bulk")
    fun bulk(@RequestBody body: RegisterProjects): BulkAdded {
        if (body.roots.isEmpty()) throw BadRequest("roots is empty", "Send the absolute paths of the repos inside the container.")
        if (body.roots.size > 500) throw BadRequest("Too many folders at once (${body.roots.size})", "Send at most 500.")
        val (rows, skipped) = projects.registerMany(body.roots, body.names)
        val added = rows.map { row -> projects.view(row).also { hub.publish(row.id, "project.changed", it) } }
        return BulkAdded(added, skipped)
    }

    @GetMapping("/{pid}")
    fun get(@PathVariable pid: String): Project = projects.view(projects.require(pid))

    /** The code graph index: idle | indexing | ready | failed, with file and symbol counts (engine runtime/scan.py). */
    @GetMapping("/{pid}/index")
    fun index(@PathVariable pid: String): JsonNode {
        projects.require(pid)
        return engine.index(pid)
    }

    /** Scans the project again with a full re-index; answers at once with status "indexing". */
    @PostMapping("/{pid}/index/rebuild")
    fun rebuildIndex(@PathVariable pid: String): JsonNode {
        val row = projects.require(pid)
        return engine.scan(row.id, row.root, rebuild = true)
    }
}
