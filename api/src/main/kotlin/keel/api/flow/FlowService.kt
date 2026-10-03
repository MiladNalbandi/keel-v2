package keel.api.flow

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentService
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.events.EventHub
import keel.api.mcp.McpServerSpec
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service

data class Ac(val id: String = "", val layer: String = "API", val title: String = "")

data class ThreadSettings(val gatesMode: String, val capTokens: Int, val onCap: String, val cheaperModel: Model?)

/** The engine's StartThread (CONTRACT "Shared types"). */
data class StartThread(
    val projectId: String,
    val root: String,
    val workflow: Workflow,
    val title: String,
    val acs: List<Ac>?,
    val models: Map<String, Model>,
    val settings: ThreadSettings,
    val mcp: List<McpServerSpec>,
    val skills: Map<String, String>,
    /** provider -> API key for models that run in "api" mode; the engine keeps them in memory only. */
    val keys: Map<String, String>? = null,
)

data class FlowView(val thread: JsonNode?, val workflow: Workflow?, val keelState: JsonNode?)

@Service
class FlowService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val workflows: WorkflowService,
    private val settings: SettingsService,
    private val agents: AgentService,
    private val skills: SkillService,
    private val mcp: McpService,
    private val hub: EventHub,
    private val mapper: ObjectMapper,
    private val secrets: SecretService,
) {
    /** Builds StartThread from the workflow + effective settings + agent models + MCP + skills. */
    fun buildStart(pid: String, workflowId: String, title: String, acs: List<Ac>?): StartThread {
        val project = projects.require(pid)
        val wf = workflows.get(workflowId)
        val s = settings.effective(pid)
        val all = agents.list(pid)
        val byId = all.associateBy { it.id }
        val used = wf.steps.flatMap { st -> listOfNotNull(st.agent) + st.lanes.orEmpty().filter { it.kind == "agent" }.mapNotNull { it.sub } }.distinct()
        val off = used.filter { byId[it]?.enabled == false }
        if (off.isNotEmpty()) throw BadRequest("These agents are turned off for this project: ${off.joinToString()}", "Turn them on in Agents, or change the workflow.")

        val models = linkedMapOf("default" to s.defaultModel)
        all.filter { it.enabled }.forEach { models[it.id] = it.model }

        val allow = mcp.allow(pid)
        val steps = wf.steps.map { st ->
            val a = st.agent
            if (st.tools == null && a != null && allow[a] != null) st.copy(tools = allow[a]) else st
        }
        val skillText = used.mapNotNull { id ->
            val ids = byId[id]?.skills.orEmpty()
            if (ids.isEmpty()) null else id to skills.textFor(pid, ids)
        }.filter { it.second.isNotBlank() }.toMap()

        return StartThread(
            projectId = pid, root = project.root, workflow = wf.copy(steps = steps), title = title, acs = acs?.takeIf { it.isNotEmpty() },
            models = models,
            settings = ThreadSettings(s.gatesMode, s.capTokens, s.onCap, s.cheaperModel),
            mcp = mcp.specsFor(s.mcp), skills = skillText,
        )
    }

    fun start(pid: String, workflowId: String, title: String, acs: List<Ac>?): JsonNode {
        if (title.isBlank()) throw BadRequest("Give the flow a title", "One short line: what should this flow build or fix?")
        val start = buildStart(pid, workflowId, title, acs)
        val keys = start.models.values.filter { it.mode == "api" }.map { it.provider }.distinct()
            .mapNotNull { p -> secrets.keyForProvider(p)?.let { p to it } }.toMap()
        val body = if (keys.isEmpty()) start else start.copy(keys = keys)
        val res = engine.startThread(body)
        val tid = res.get("thread_id")?.asText() ?: throw ApiException(HttpStatus.BAD_GATEWAY, "The engine did not return a thread id")
        val now = Time.now()
        jdbc.update(
            "INSERT OR IGNORE INTO threads(id, project_id, workflow_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
            tid, pid, workflowId, title, now, now,
        )
        jdbc.update("UPDATE threads SET workflow_id = ?, title = ?, project_id = ? WHERE id = ?", workflowId, title, pid, tid)
        val state = runCatching { engine.thread(tid) }.getOrElse { res }
        save(tid, state)
        hub.publish(pid, "project.changed", mapOf("id" to pid))
        return state
    }

    /** Stores the latest ThreadState we saw, so the UI has something when the engine is down. */
    fun save(tid: String, state: JsonNode) {
        if (!state.isObject || state.get("status") == null) return
        jdbc.update(
            "UPDATE threads SET status = ?, current = ?, phase = ?, ac = ?, state_json = ?, error = ?, updated_at = ? WHERE id = ?",
            state.get("status")?.asText(), state.get("current")?.takeIf { !it.isNull }?.asText(), state.get("phase")?.asText(),
            state.get("ac")?.takeIf { !it.isNull }?.asText(), mapper.writeValueAsString(state),
            state.get("error")?.takeIf { !it.isNull }?.asText(), Time.now(), tid,
        )
    }

    private fun threadProject(tid: String): String? =
        jdbc.query("SELECT project_id FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, tid).firstOrNull()

    fun resume(tid: String, decision: String, why: String?, payload: Map<String, Any?>?): JsonNode {
        if (decision !in setOf("approve", "reject")) throw BadRequest("decision must be approve or reject")
        val state = engine.resume(tid, mapOf("decision" to decision, "why" to why, "payload" to payload).filterValues { it != null })
        save(tid, state)
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    fun stop(tid: String): JsonNode {
        val state = engine.stop(tid)
        save(tid, state)
        jdbc.update("UPDATE agent_calls SET status = 'stopped', ended_at = COALESCE(ended_at, ?) WHERE thread_id = ? AND status = 'running'", Time.now(), tid)
        threadProject(tid)?.let { hub.publish(it, "project.changed", mapOf("id" to it)) }
        return state
    }

    fun history(tid: String): JsonNode = engine.history(tid)

    fun rewind(tid: String, checkpointId: String): JsonNode {
        if (checkpointId.isBlank()) throw BadRequest("checkpoint_id is missing")
        val state = engine.rewind(tid, checkpointId)
        save(tid, state)
        return state
    }

    fun flow(pid: String): FlowView {
        val root = projects.root(pid)
        val row = jdbc.query(
            "SELECT id, workflow_id, state_json FROM threads WHERE project_id = ? ORDER BY CASE WHEN status IN ('running','waiting') THEN 0 ELSE 1 END, updated_at DESC LIMIT 1",
            { rs, _ -> Triple(rs.getString(1), rs.getString(2), rs.getString(3)) }, pid,
        ).firstOrNull()
        var thread: JsonNode? = null
        var workflow: Workflow? = null
        if (row != null) {
            thread = try {
                engine.thread(row.first).also { save(row.first, it) }
            } catch (e: EngineDown) {
                row.third?.let { mapper.readTree(it) }
            } catch (e: ApiException) {
                row.third?.let { mapper.readTree(it) }
            }
            workflow = row.second?.let { wid -> try { workflows.get(wid) } catch (e: NotFound) { null } }
        }
        return FlowView(thread, workflow, projects.keelState(root))
    }

    /** Engine estimate, fed with this project's job history. */
    fun estimate(pid: String, workflowId: String, acs: Int): JsonNode {
        projects.require(pid)
        val wf = workflows.get(workflowId)
        val history = jdbc.query(
            "SELECT agent, tokens_in, tokens_out FROM agent_calls WHERE project_id = ? AND status = 'done' AND agent IS NOT NULL ORDER BY started_at DESC LIMIT 300",
            { rs, _ -> mapOf("agent" to rs.getString(1), "tokens_in" to rs.getLong(2), "tokens_out" to rs.getLong(3), "retries" to 0) }, pid,
        )
        val models = linkedMapOf<String, Model>("default" to settings.effective(pid).defaultModel)
        agents.list(pid).filter { it.enabled }.forEach { models[it.id] = it.model }
        return engine.estimate(mapOf("yaml" to wf.yaml, "acs" to acs.coerceIn(0, 50), "history" to history, "models" to models))
    }
}
