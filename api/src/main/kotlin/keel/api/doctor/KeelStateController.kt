package keel.api.doctor

import com.fasterxml.jackson.databind.JsonNode
import keel.api.engine.EngineClient
import keel.api.events.EventHub
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** One keel v2 flow that runs or waits in the project (keel_state). */
data class StateFlow(val id: String, val title: String, val workflow: String?, val status: String, val step: String?,
                     val phase: String?, val worktree: String?)

/**
 * 0.18.64 keel's flow state for a project, keel v1's leftover too (MCP `keel_state`): the keel v2 flows that run or
 * wait there, and the state file keel v1 left (`.keel/state.json`) as the engine reads it (engine GET /keel-state; null when there is
 * none). A v1 flow nobody runs any more still blocks commits through keel v1's hooks; close archives its file to
 * `.keel/archive/state-<date>.json` (engine POST /keel-state/close), never deletes it. MCP: close is a write.
 */
@RestController
@RequestMapping("/api")
class KeelStateController(private val engine: EngineClient, private val projects: ProjectService, private val jdbc: JdbcTemplate,
                          private val hub: EventHub) {

    @GetMapping("/projects/{pid}/keel-state")
    fun show(@PathVariable pid: String): Map<String, Any?> {
        val root = projects.root(pid)
        val flows = jdbc.query(
            "SELECT id, title, workflow_id, status, current, phase, worktree FROM threads " +
                "WHERE project_id = ? AND status IN ('running','waiting') AND hidden_at IS NULL ORDER BY updated_at DESC",
            { rs, _ -> StateFlow(rs.getString(1), rs.getString(2) ?: "", rs.getString(3), rs.getString(4), rs.getString(5),
                rs.getString(6), rs.getString(7)) }, pid,
        )
        val v1 = engine.get("/keel-state?root={root}", mapOf("root" to root.toString())).path("v1")
        return mapOf("project" to pid, "flows" to flows, "v1" to (if (v1.isMissingNode) null else v1))
    }

    /** Archives keel v1's state file: {archived, flow, was_active}. 404 when there is none. */
    @PostMapping("/projects/{pid}/keel-state/close")
    fun close(@PathVariable pid: String): JsonNode =
        engine.post("/keel-state/close", mapOf("root" to projects.root(pid).toString()))
            .also { hub.publish(pid, "project.changed", mapOf("id" to pid)) }
}
