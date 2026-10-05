package keel.api.hunt

import com.fasterxml.jackson.annotation.JsonProperty
import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class CloseFinding(val id: String = "", @JsonProperty("as") val disposition: String = "", val note: String = "")

/**
 * The bug hunt backlog, read from the engine (runs, candidates with verdicts and recipes, groups, the rendered report).
 * A thin pass-through for the Hunt page; the hunt itself runs as the `hunt` and `hunt-next` workflows.
 */
@RestController
@RequestMapping("/api/projects/{pid}/hunts")
class HuntController(private val projects: ProjectService, private val engine: EngineClient) {

    @GetMapping
    fun list(@PathVariable pid: String): JsonNode {
        projects.require(pid)
        return engine.hunts(pid)
    }

    @GetMapping("/{run}")
    fun get(@PathVariable pid: String, @PathVariable run: String): JsonNode {
        projects.require(pid)
        return engine.hunt(pid, run)
    }

    /** Close a finding (F-001) or a group (G-01) as fixed | accepted | wontfix; the note is required. */
    @PostMapping("/{run}/close")
    fun close(@PathVariable pid: String, @PathVariable run: String, @RequestBody body: CloseFinding): JsonNode {
        projects.require(pid)
        if (body.disposition !in setOf("fixed", "accepted", "wontfix")) throw BadRequest("Close it as fixed, accepted or wontfix")
        if (body.note.isBlank()) throw BadRequest("Closing needs a note", "The PR or commit for fixed, the reason for accepted or wontfix.")
        return engine.closeHunt(pid, run, mapOf("id" to body.id, "as" to body.disposition, "note" to body.note))
    }
}
