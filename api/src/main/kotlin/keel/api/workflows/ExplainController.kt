package keel.api.workflows

import com.fasterxml.jackson.databind.JsonNode
import keel.api.agents.AgentService
import keel.api.common.BadRequest
import keel.api.engine.EngineClient
import keel.api.flow.AgentStart
import keel.api.projects.ProjectService
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

/** A saved workflow (workflow_id), a draft from the builder (workflow), or only a thread (its own workflow). */
data class ExplainStep(
    val workflowId: String? = null,
    val workflow: Map<String, Any?>? = null,
    val stepId: String = "",
    val threadId: String? = null,
)

/**
 * "What this step does": a thin pass-through to the engine's POST /steps/explain (docs/CONTRACT.md, v0.4.1). The api
 * adds what only it knows: the project's folder and each agent's knowledge setting, so the prompt is the one keel sends.
 */
@RestController
class ExplainController(
    private val projects: ProjectService,
    private val workflows: WorkflowService,
    private val agents: AgentService,
    private val engine: EngineClient,
) {
    @PostMapping("/api/projects/{pid}/workflows/explain-step")
    fun explain(@PathVariable pid: String, @RequestBody body: ExplainStep): JsonNode {
        val project = projects.require(pid)
        if (body.stepId.isBlank()) throw BadRequest("Say which step to explain (step_id)")
        val threadId = body.threadId?.takeIf { it.isNotBlank() }
        val workflow: Any? = body.workflow ?: body.workflowId?.takeIf { it.isNotBlank() }?.let { workflows.get(it) }
        if (workflow == null && threadId == null) throw BadRequest("Send workflow_id, a workflow or a thread_id")
        val knowledge = agents.list(pid).filter { it.enabled }.associate { it.id to AgentStart(it.knowledge) }
        return engine.explainStep(
            mapOf(
                "workflow" to workflow, "step_id" to body.stepId, "thread_id" to threadId, "root" to project.root,
                "project_id" to pid, "agents" to knowledge,
            ).filterValues { it != null },
        )
    }
}
