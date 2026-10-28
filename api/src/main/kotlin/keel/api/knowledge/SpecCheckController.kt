package keel.api.knowledge

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class SpecCheckBody(val path: String = "")
data class CitationsBody(val path: String? = null, val text: String? = null)

/**
 * keel 0.18.64 keel's spec check and citation check on a file of the project, outside a flow (the MCP tools
 * keel_spec_check and keel_citations_check). The engine reads the file in the project's folder
 * (runtime/spec_routes.py); both change nothing, so a read MCP token may call them (McpLevels READ_POSTS).
 */
@RestController
@RequestMapping("/api/projects/{pid}")
class SpecCheckController(private val engine: EngineClient, private val projects: ProjectService) {

    /** {path, criteria, findings: [{level, text}], fix, summary}: what a flow's spec check finds in the file. */
    @PostMapping("/specs/check")
    fun spec(@PathVariable pid: String, @RequestBody body: SpecCheckBody): JsonNode {
        val path = body.path.trim().ifBlank { throw BadRequest("Send the spec's path", "Relative to the project, e.g. docs/specs/orders.md.") }
        return engine.post("/specs/check", mapOf("root" to projects.root(pid).toString(), "path" to path))
    }

    /** {path, checked, bad: [{cite, path, problem, level}]}: every path:line in the file (or the text) that does not resolve. */
    @PostMapping("/citations/check")
    fun citations(@PathVariable pid: String, @RequestBody body: CitationsBody): JsonNode {
        val path = body.path?.trim()?.ifBlank { null }
        if ((path == null) == (body.text == null)) throw BadRequest("Send a path or a text", "One of them: a file of the project, or the text to check.")
        if ((body.text?.length ?: 0) > MAX_TEXT) throw BadRequest("The text is longer than ${MAX_TEXT / 1_000_000} MB")
        val root = projects.root(pid).toString()
        return engine.post("/citations/check", if (path != null) mapOf("root" to root, "path" to path) else mapOf("root" to root, "text" to body.text))
    }

    companion object {
        const val MAX_TEXT = 2_000_000
    }
}
