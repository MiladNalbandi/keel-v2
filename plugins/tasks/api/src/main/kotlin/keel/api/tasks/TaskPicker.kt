package keel.api.tasks

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.events.AgentCalls
import keel.api.events.EngineEvent
import keel.api.knowledge.KnowledgeService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.workflows.Workflow
import keel.api.workflows.WorkflowService
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Component
import java.util.UUID

/**
 * "Pick a flow for me": one cheap model call through the engine (POST /tasks/pick, the same no-tools path as the
 * review check), with the ticket (title, description, Jira comments, its PR), a few facts keel knows about the project
 * and the project's workflows. The cheap model (Settings' cheaper model when it can run, else the default) at low
 * effort; a call whose estimate passes the project's pick cap does not run. No model (the fake one), an engine that
 * cannot be reached or a bad answer: the type's default flow, so there is always a proposal. Tokens are booked as an
 * agent call (Budget).
 */
@Component
class TaskPicker(
    private val workflows: WorkflowService,
    private val engine: EngineClient,
    private val settings: SettingsService,
    private val secrets: SecretService,
    private val calls: AgentCalls,
    private val knowledge: KnowledgeService,
    private val trackers: ObjectProvider<TicketTracker>,
    private val mapper: ObjectMapper,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        /** Workflows a ticket is never implemented with (set-up, keel's own, follow-ups of another flow). */
        val NOT_FOR_TICKETS = setOf("init", "knowledge-refresh", "plugin", "hunt-next", "lint", "cover", "clarify")
        const val REVIEW_DEFAULT = "review-pr"
        private val COMMENT = Regex("^\\s*#\\s?(.*)$")
    }

    /** The workflows a [kind] (flow | review) can pick from, as {id, name, about}. */
    fun candidates(pid: String, kind: String): List<Map<String, String>> {
        val all = runCatching { workflows.list(pid) }.getOrDefault(emptyList())
        val review = all.filter { it.id.startsWith("review") }
        val list = if (kind == "review") review else all.filter { it.id !in NOT_FOR_TICKETS && !it.id.startsWith("review") }
        return list.map { mapOf("id" to it.id, "name" to it.name, "about" to about(it)) }
    }

    /** The comment lines at the top of a workflow's YAML: what it is for. */
    private fun about(w: Workflow): String = w.yaml.lineSequence().mapNotNull { COMMENT.matchEntire(it)?.groupValues?.get(1)?.trim() }
        .take(6).joinToString(" ").take(300)

    fun defaultFor(t: Task, kind: String, ids: Collection<String>): String {
        val want = if (kind == "review") REVIEW_DEFAULT else TaskTypes.defaultWorkflow(t.type)
        return if (ids.isEmpty() || want in ids) want else ids.first()
    }

    fun pick(t: Task, kind: String, capTokens: Int): FlowPick {
        val list = candidates(t.projectId, kind)
        val ids = list.map { it["id"]!! }
        val default = defaultFor(t, kind, ids)
        val comments = t.externalKey?.let { key ->
            runCatching { (trackers.bySource(t.source) ?: trackers.forProject(t.projectId))?.client(t.projectId)?.comments(key, 8) }.getOrNull()
        }.orEmpty()
        val facts = runCatching { knowledge.memory(t.projectId).facts.take(10).map { "${it.title}: ${it.text}".take(300) } }.getOrDefault(emptyList())
        val ticket = linkedMapOf<String, Any?>("key" to t.externalKey, "type" to t.type, "status" to (t.externalStatus ?: TaskStatus.LABELS[t.status]),
            "title" to t.title, "description" to t.description.take(4000), "comments" to comments, "pr_url" to t.prUrl)
        val local = fallback(t, default, "keel's default flow for a ${t.type}${if (kind == "review") " review" else ""}.")
        val model = modelFor(t.projectId)
        if (model.provider == "fake") return local.copy(why = "keel's default flow for a ${t.type} (no model is set up to pick one).")
        val estimate = (jsonOf(ticket).length + list.sumOf { it.values.sumOf { v -> v.length } } + facts.sumOf { it.length } + 2_000) / 4 + 800
        if (estimate > capTokens) return local.copy(why = "keel's default flow: picking one would need about $estimate tokens, more than the cap of $capTokens.")
        runCatching { keel.api.settings.LocalOnly.check(settings.effective(t.projectId), listOf(model), "the flow pick") }
            .onFailure { return local.copy(why = "keel's default flow: ${it.message}") }
        val body = mapOf("model" to model, "keys" to secrets.engineKeys(model.provider, model.mode), "timeout" to 120, "kind" to kind,
            "ticket" to ticket, "workflows" to list, "default" to default, "facts" to facts)
        val callRun = "tp_" + UUID.randomUUID().toString().replace("-", "").take(12)
        val at = Time.now()
        val ev = { type: String, data: Map<String, Any?> -> EngineEvent(type, callRun, t.projectId, "task-pick", at, "tp_$callRun", data) }
        calls.start(ev("agent.started", mapOf("provider" to model.provider, "model" to model.model, "mode" to model.mode, "effort" to model.effort, "phase" to "none")),
            at, "flow-picker", null)
        var tin = 0L
        var tout = 0L
        var error: String? = null
        return try {
            val answer = engine.post("/tasks/pick", body, long = true)
            tin = answer.path("tokens_in").asLong(0)
            tout = answer.path("tokens_out").asLong(0)
            error = answer.path("error").asText("").ifBlank { null }
            read(answer.path("pick"), ids, local).copy(model = "${model.provider} · ${model.model}", tokens = tin + tout)
        } catch (e: Exception) {
            log.warn("flow pick for {}: {}", t.id, e.message)
            error = e.message ?: e.javaClass.simpleName
            local.copy(why = "keel's default flow: the engine could not pick one (${error.take(120)}).")
        } finally {
            calls.finish(ev("agent.finished", mapOf("status" to if (error == null) "done" else "failed", "tokens_in" to tin, "tokens_out" to tout,
                "result" to (error ?: "picked a flow for ${t.label}"))), Time.now())
        }
    }

    private fun jsonOf(v: Any?): String = runCatching { mapper.writeValueAsString(v) }.getOrDefault("")

    /** The engine's pick, checked once more here: an unknown workflow is the default. */
    private fun read(p: JsonNode, ids: List<String>, local: FlowPick): FlowPick {
        if (p.isMissingNode || p.isNull) return local
        val wid = p.path("workflow").asText("")
        if (wid !in ids) return local
        @Suppress("UNCHECKED_CAST")
        val questions = (mapper.convertValue(p.path("questions"), List::class.java) as? List<Map<String, Any?>>).orEmpty().take(3)
        @Suppress("UNCHECKED_CAST")
        val settingsOut = (mapper.convertValue(p.path("settings"), Map::class.java) as? Map<String, Any?>).orEmpty()
        return FlowPick(wid, p.path("why").asText("").ifBlank { local.why }.take(500), p.path("title").asText("").ifBlank { local.title }.take(80),
            p.path("request").asText("").ifBlank { local.request }.take(1500), questions, settingsOut, p.path("fallback").asBoolean(false))
    }

    fun fallback(t: Task, workflowId: String, why: String): FlowPick {
        val request = buildString {
            append(t.title)
            if (t.description.isNotBlank()) append("\n\n").append(t.description.take(1500))
        }
        return FlowPick(workflowId, why, t.title.take(80), request, fallback = true)
    }

    /** The cheapest model keel can run here, at low effort (the review check's rule). */
    fun modelFor(pid: String): Model {
        val s = settings.effective(pid)
        val cheapest = s.cheaperModel.takeIf {
            it.provider != "fake" && (it.provider == s.defaultModel.provider || secrets.engineKeys(it.provider, it.mode).isNotEmpty())
        } ?: s.defaultModel
        return cheapest.copy(effort = "low")
    }
}
