package keel.api.supervisor

import com.fasterxml.jackson.databind.JsonNode
import jakarta.servlet.http.HttpServletRequest
import keel.api.common.BadRequest
import keel.api.common.Forbidden
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.events.AgentCalls
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.flow.FlowService
import keel.api.mcp.McpLevelGuard
import keel.api.notifications.NotificationService
import keel.api.projects.ProjectService
import org.springframework.stereotype.Component
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

/**
 * The Supervisor plugin's api part (plugins/supervisor, docs/plans/supervisor.md). Its engine part does the work; the api
 * passes its routes through for one project and turns its events into agent calls and notifications.
 *
 *     GET  /api/projects/{pid}/supervisor/decisions?threadId=&limit=   the audit log, newest first
 *     GET  /api/projects/{pid}/flows/{tid}/supervisor                   a flow's Supervisor settings, tokens, last rows
 *     POST /api/projects/{pid}/flows/{tid}/supervisor                   {on, mode, model, effort, cap_tokens, max_answers, policy}
 *                                                                       (policy rung: may also needs i_understand: [RUNG_OK])
 *     GET|POST /api/projects/{pid}/supervisor/flow                      the same for the project's flow now (keel TUI's
 *                                                                       /supervisor: a plugin's TUI command calls its own routes)
 */
@RestController
@RequestMapping("/api/projects/{pid}")
class SupervisorController(private val engine: EngineClient, private val projects: ProjectService,
                           private val flows: FlowService) {
    @GetMapping("/supervisor/decisions")
    fun decisions(@PathVariable pid: String, @RequestParam(required = false) threadId: String?,
                  @RequestParam(defaultValue = "100") limit: Int): JsonNode {
        projects.require(pid)
        val thread = threadId?.let { "&thread_id=${enc(it)}" } ?: ""
        return engine.get("/supervisor/decisions?project_id=${enc(pid)}$thread&limit=${limit.coerceIn(1, 500)}")
    }

    @GetMapping("/flows/{tid}/supervisor")
    fun settings(@PathVariable pid: String, @PathVariable tid: String): JsonNode {
        own(pid, tid)
        return engine.get("/threads/${enc(tid)}/supervisor")
    }

    @PostMapping("/flows/{tid}/supervisor")
    fun change(@PathVariable pid: String, @PathVariable tid: String, @RequestBody body: Map<String, Any?>,
               request: HttpServletRequest): JsonNode {
        own(pid, tid)
        checkPolicy(body, request)
        val allowed = setOf("on", "mode", "model", "effort", "cap_tokens", "max_answers", "policy")
        return engine.post("/threads/${enc(tid)}/supervisor", body.filterKeys { it in allowed })
    }

    @GetMapping("/supervisor/flow")
    fun current(@PathVariable pid: String): Any = flowNow(pid)?.let { settings(pid, it) } ?: NO_FLOW

    @PostMapping("/supervisor/flow")
    fun changeCurrent(@PathVariable pid: String, @RequestBody body: Map<String, Any?>, request: HttpServletRequest): Any =
        flowNow(pid)?.let { change(pid, it, body, request) } ?: NO_FLOW

    /**
     * 0.18.64 `policy: {rung: may}` lets the Supervisor run the commands it chose on a failing setup rung, with nobody
     * looking. It stays possible (an owner may want it; e2e/supervisor uses it), but only said out loud: the body must
     * also have `i_understand: "commands run without you"`. An MCP client below the admin level never changes the
     * policy (keel_supervisor_set does not send one).
     */
    private fun checkPolicy(body: Map<String, Any?>, request: HttpServletRequest) {
        val policy = body["policy"] as? Map<*, *> ?: return
        val level = request.getAttribute(McpLevelGuard.LEVEL_ATTR)
        if (level != null && level != "admin") {
            throw Forbidden("Only you change the Supervisor's policy", "Change it in keel (the flow's Supervisor panel), or with keel2 mcp --admin.")
        }
        if (policy["rung"] == "may" && body["i_understand"] != RUNG_OK) {
            throw BadRequest("rung: may lets the Supervisor run commands on this machine without you",
                "If you want that, send i_understand: \"$RUNG_OK\" with it.")
        }
    }

    /** The project's flow that runs or waits now, else null. */
    private fun flowNow(pid: String): String? {
        val t = flows.flow(pid).thread ?: return null
        return t.path("thread_id").asText().takeIf { it.isNotBlank() && t.path("status").asText() in setOf("running", "waiting") }
    }

    /** A flow of another project is a 404 here. */
    private fun own(pid: String, tid: String) {
        projects.require(pid)
        if (engine.get("/threads/${enc(tid)}").path("project_id").asText() != pid)
            throw NotFound("No flow $tid in this project", "Open the flow from its own project.")
    }

    private fun enc(s: String) = URLEncoder.encode(s, StandardCharsets.UTF_8)

    companion object {
        val NO_FLOW = mapOf("thread_id" to null, "note" to "No flow runs in this project: start one first.")
        /** 0.18.64 what a body must say to set policy rung: may ([checkPolicy]). */
        const val RUNG_OK = "commands run without you"
    }
}

/**
 * The Supervisor's engine events. Each model turn is an agent call (agent "supervisor": the Budget page, Live agents and
 * the flow's agent list count it). An escalation the person did not expect anyway (it was unsure, stuck, looped, or out
 * of budget) is a notification with its short summary; the gate's own notification already covers a gate only the
 * person answers, and shadow mode's suggestions.
 */
@Component
class SupervisorEvents(private val calls: AgentCalls, private val notifications: NotificationService) : EngineEventHandler {
    override val prefix = "supervisor."

    override fun handle(event: EngineEvent) {
        val at = event.at ?: Time.now()
        val d = event.data
        when (event.type) {
            "supervisor.started" -> calls.start(event, at, "supervisor", null)
            "supervisor.finished" -> calls.finish(event, at)
            "supervisor.escalated" -> if (d.text("code") !in QUIET && event.projectId.isNotBlank())
                notifications.create("review", event.projectId, "Supervisor: ${d.text("reason") ?: "it needs you"}",
                    d.text("summary")?.take(600) ?: "", "/projects/${event.projectId}/flow", threadId = event.threadId, step = event.step)
        }
    }

    companion object {
        /** Escalations that need no notification of their own (the waiting gate has one). */
        val QUIET = setOf("shadow", "never-kind", "policy-escalate")
    }
}

private fun Map<String, Any?>.text(key: String): String? =
    this[key]?.let { if (it is String) it else it.toString() }?.takeIf { it.isNotEmpty() }
