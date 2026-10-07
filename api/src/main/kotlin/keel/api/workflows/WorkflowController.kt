package keel.api.workflows

import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

data class CreateWorkflow(val name: String = "", val from: String = "blank", val keelRules: Boolean? = null)
data class ImportWorkflow(val yaml: String? = null, val url: String? = null, val folder: String? = null)
data class CheckWorkflow(val yaml: String = "")
data class WorkflowFolder(val folder: String? = null)
data class ImportResult(val workflow: Workflow, val review: InstallReview)
data class InstallScope(val scope: String = "project")

@RestController
@RequestMapping("/api")
class WorkflowController(private val workflows: WorkflowService) {

    @GetMapping("/projects/{pid}/workflows")
    fun list(@PathVariable pid: String): List<Workflow> = workflows.list(pid)

    @PostMapping("/projects/{pid}/workflows")
    fun create(@PathVariable pid: String, @RequestBody body: CreateWorkflow): Workflow =
        workflows.create(pid, body.name, body.from, body.keelRules)

    @GetMapping("/workflows/{wid}")
    fun get(@PathVariable wid: String): Workflow = workflows.get(wid)

    @PutMapping("/workflows/{wid}")
    fun update(@PathVariable wid: String, @RequestBody body: WorkflowUpdate): Workflow = workflows.update(wid, body)

    @DeleteMapping("/workflows/{wid}")
    fun delete(@PathVariable wid: String): Map<String, Boolean> {
        workflows.delete(wid)
        return mapOf("ok" to true)
    }

    @GetMapping("/workflows/{wid}/export")
    fun export(@PathVariable wid: String): ResponseEntity<String> =
        ResponseEntity.ok()
            .contentType(MediaType.parseMediaType("text/yaml;charset=UTF-8"))
            .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=$wid.workflow.yaml")
            .body(workflows.export(wid))

    @PostMapping("/projects/{pid}/workflows/import")
    fun import(@PathVariable pid: String, @RequestBody body: ImportWorkflow): ImportResult {
        val (wf, review) = workflows.import(pid, body.yaml, body.url)
        val placed = body.folder?.takeIf { it.isNotBlank() }?.let { workflows.setFolder(pid, wf.id, it); wf.copy(folder = it.trim()) }
        return ImportResult(placed ?: wf, review)
    }

    /** v0.9.0: what a workflow YAML would be (steps, gates, agents, commands) and whether keel can run it; nothing is saved. */
    @PostMapping("/projects/{pid}/workflows/check")
    fun check(@PathVariable pid: String, @RequestBody body: CheckWorkflow): InstallReview = workflows.check(pid, body.yaml)

    /** v0.9.0: put a workflow in a folder of this project's Workflows page (empty: no folder). */
    @PutMapping("/projects/{pid}/workflows/{wid}/folder")
    fun folder(@PathVariable pid: String, @PathVariable wid: String, @RequestBody body: WorkflowFolder): Map<String, String?> =
        mapOf("folder" to workflows.setFolder(pid, wid, body.folder))

    @GetMapping("/library")
    fun library(@RequestParam(required = false) project: String?): List<LibraryItem> = workflows.library(project)

    @PostMapping("/projects/{pid}/library/{id}/install")
    fun install(@PathVariable pid: String, @PathVariable id: String, @RequestBody(required = false) body: InstallScope?): Workflow =
        workflows.install(pid, id, body?.scope ?: "project")
}
