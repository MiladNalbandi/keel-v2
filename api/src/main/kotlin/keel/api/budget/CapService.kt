package keel.api.budget

import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.projects.ProjectService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.PutMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController
import java.util.UUID

/** CONTRACT v0.2 `Cap`. `limit` is a number in `unit` (tokens or usd). */
data class Cap(
    val id: String = "",
    val scope: String = "",
    val limit: Double = 0.0,
    val unit: String = "tokens",
    val action: String = "pause",
)

@Service
class CapService(private val jdbc: JdbcTemplate, private val projects: ProjectService) {

    fun list(pid: String): List<Cap> {
        projects.require(pid)
        return jdbc.query(
            "SELECT id, scope, cap_limit, unit, action FROM caps WHERE project_id = ? ORDER BY created_at, id",
            { rs, _ -> Cap(rs.getString(1), rs.getString(2), rs.getDouble(3), rs.getString(4), rs.getString(5)) }, pid,
        )
    }

    private fun find(pid: String, id: String): Cap = list(pid).firstOrNull { it.id == id } ?: throw NotFound("No cap $id")

    fun create(pid: String, cap: Cap): Cap {
        projects.require(pid)
        check(cap)
        val id = "cap-" + UUID.randomUUID().toString().take(8)
        jdbc.update(
            "INSERT INTO caps(id, project_id, scope, cap_limit, unit, action, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            id, pid, cap.scope, cap.limit, cap.unit, cap.action, Time.now(),
        )
        return find(pid, id)
    }

    fun update(pid: String, id: String, cap: Cap): Cap {
        find(pid, id)
        check(cap)
        jdbc.update(
            "UPDATE caps SET scope = ?, cap_limit = ?, unit = ?, action = ? WHERE id = ? AND project_id = ?",
            cap.scope, cap.limit, cap.unit, cap.action, id, pid,
        )
        return find(pid, id)
    }

    fun delete(pid: String, id: String) {
        projects.require(pid)
        if (jdbc.update("DELETE FROM caps WHERE id = ? AND project_id = ?", id, pid) == 0) throw NotFound("No cap $id")
    }

    private fun check(c: Cap) {
        if (c.scope !in SCOPES) throw BadRequest("scope cannot be \"${c.scope}\"", "Pick one of: ${SCOPES.joinToString()}")
        if (c.unit !in UNITS) throw BadRequest("unit cannot be \"${c.unit}\"", "Pick one of: ${UNITS.joinToString()}")
        if (c.action !in ACTIONS) throw BadRequest("action cannot be \"${c.action}\"", "Pick one of: ${ACTIONS.joinToString()}")
        if (c.limit <= 0) throw BadRequest("limit must be above 0")
    }

    companion object {
        val SCOPES = setOf("day", "flow", "step", "api_month")
        val UNITS = setOf("tokens", "usd")
        val ACTIONS = setOf("pause", "cheaper", "stop")
    }
}

@RestController
class CapController(private val caps: CapService) {
    @GetMapping("/api/projects/{pid}/caps")
    fun list(@PathVariable pid: String): List<Cap> = caps.list(pid)

    @PostMapping("/api/projects/{pid}/caps")
    fun create(@PathVariable pid: String, @RequestBody body: Cap): Cap = caps.create(pid, body)

    @PutMapping("/api/projects/{pid}/caps/{id}")
    fun update(@PathVariable pid: String, @PathVariable id: String, @RequestBody body: Cap): Cap = caps.update(pid, id, body)

    @DeleteMapping("/api/projects/{pid}/caps/{id}")
    fun delete(@PathVariable pid: String, @PathVariable id: String): Map<String, Boolean> {
        caps.delete(pid, id)
        return mapOf("ok" to true)
    }
}
