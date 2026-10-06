package keel.api.helper

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentService
import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.flow.AgentStart
import keel.api.flow.FlowService
import keel.api.mcp.McpService
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PatchMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

/** A new Helper session (v0.6.0): mode "ask" for now; the model defaults to the helper agent's. */
data class HelperCreate(val mode: String = "ask", val model: Model? = null, val title: String = "")
data class HelperPatch(val title: String? = null, val model: Model? = null)

/** What the person points at in the Repo page: a file, a code-graph symbol or a criterion. */
data class HelperMention(val kind: String = "", val value: String = "", val file: String? = null, val line: Int? = null)
data class HelperSelection(val path: String = "", val from: Int? = null, val to: Int? = null, val text: String = "")

/** One message to the Helper; the api adds the logins, the MCP servers, the agent's settings and the project's flow. */
data class HelperTurn(
    val text: String = "",
    val model: Model? = null,
    val mentions: List<HelperMention> = emptyList(),
    val selection: HelperSelection? = null,
    val openFile: String? = null,
)

/**
 * keel's Helper: chat sessions in the Repo page. The engine runs them (engine runtime/helper.py) with keel's own
 * harness; the api owns who may call, what the engine gets (logins, MCP, knowledge, the flow) and the records (the
 * helper.* events become agent calls in EventService, so the budget, Live agents and Jobs count them).
 */
@Service
class HelperService(
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val settings: SettingsService,
    private val agents: AgentService,
    private val secrets: SecretService,
    private val mcp: McpService,
    private val skills: SkillService,
    private val flows: FlowService,
    private val mapper: ObjectMapper,
) {
    private fun helperAgent(pid: String) = agents.list(pid).firstOrNull { it.id == AGENT }

    /** The Helper's model: the helper agent's (Agents page), else the project's default model. */
    fun defaultModel(pid: String): Model = helperAgent(pid)?.model ?: settings.effective(pid).defaultModel

    fun create(pid: String, body: HelperCreate): JsonNode {
        val project = projects.require(pid)
        if (body.mode != "ask") throw BadRequest("Unknown Helper mode ${body.mode}", "This keel has the Ask mode.")
        return engine.post("/helper/sessions", mapOf(
            "project_id" to pid, "root" to project.root, "mode" to body.mode,
            "model" to (body.model ?: defaultModel(pid)), "title" to body.title.take(120),
        ))
    }

    fun list(pid: String): JsonNode {
        projects.require(pid)
        return engine.get("/helper/sessions?project=$pid")
    }

    /** The session, if it belongs to this project (a session id from another project is a 404 here). */
    fun get(pid: String, sid: String): JsonNode {
        projects.require(pid)
        val s = engine.get("/helper/sessions/$sid")
        if (s.path("project").asText() != pid) throw NotFound("No Helper session $sid in project $pid")
        return s
    }

    fun patch(pid: String, sid: String, body: HelperPatch): JsonNode {
        get(pid, sid)
        return engine.patch("/helper/sessions/$sid", mapOf("title" to body.title, "model" to body.model).filterValues { it != null })
    }

    fun delete(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.delete("/helper/sessions/$sid")
    }

    fun stop(pid: String, sid: String): JsonNode {
        get(pid, sid)
        return engine.post("/helper/sessions/$sid/stop", emptyMap<String, Any>())
    }

    fun turn(pid: String, sid: String, body: HelperTurn): JsonNode {
        val text = body.text.trim()
        if (text.isEmpty()) throw BadRequest("The message is empty", "Write a question, or pick a command with /.")
        if (text.length > 20_000) throw BadRequest("The message is too long", "Keep it under 20,000 characters; point at files with @ instead.")
        val s = get(pid, sid)
        val model = body.model ?: mapper.treeToValue(s.path("model"), Model::class.java)
        val eff = settings.effective(pid)
        val helper = helperAgent(pid)
        val payload = linkedMapOf<String, Any?>(
            "text" to text,
            "model" to body.model,
            "keys" to secrets.engineKeys(model.provider, model.mode).takeIf { it.isNotEmpty() },
            "mcp" to mcp.specsFor(eff.mcp),
            "tools_allow" to (mcp.allow(pid)[AGENT] ?: emptyList()),
            "agents" to (helper?.let { mapOf(AGENT to AgentStart(it.knowledge)) } ?: emptyMap()),
            "skills" to (helper?.skills?.takeIf { it.isNotEmpty() }?.let { mapOf(AGENT to skills.textFor(pid, it)) } ?: emptyMap()),
            "flow" to flowContext(pid),
            "mentions" to body.mentions.filter { it.value.isNotBlank() }.take(30),
            "selection" to body.selection?.takeIf { it.path.isNotBlank() && it.text.isNotBlank() }?.let { it.copy(text = it.text.take(8000)) },
            "open_file" to body.openFile?.takeIf { it.isNotBlank() },
        ).filterValues { it != null }
        return engine.post("/helper/sessions/$sid/turn", payload)
    }

    fun commands(pid: String): JsonNode = engine.post("/helper/commands", mapOf("root" to projects.require(pid).root))

    /** The flow that runs or waits in the project, as the Helper's prompt shows it; null when none does. */
    private fun flowContext(pid: String): Map<String, Any?>? {
        val t = runCatching { flows.flow(pid).thread }.getOrNull() ?: return null
        val status = t.path("status").asText()
        if (status != "running" && status != "waiting") return null
        val waiting = t.path("waiting").takeIf { it.isObject }
        return mapOf(
            "title" to t.path("title").asText(),
            "status" to (if (status == "waiting") "waits" else "runs"),
            "phase" to t.path("phase").asText(""),
            "spec" to t.path("spec").asText("").ifBlank { null },
            "acs" to t.path("acs").map { a -> mapOf("id" to a.path("id").asText(), "layer" to a.path("layer").asText(),
                "title" to a.path("title").asText(), "status" to a.path("status").asText()) },
            "waiting" to waiting?.let { mapOf("title" to it.path("title").asText(), "detail" to it.path("detail").asText("").take(3000)) },
        )
    }

    companion object {
        const val AGENT = "helper"
    }
}

@RestController
@RequestMapping("/api/projects/{pid}/helper")
class HelperController(private val helper: HelperService) {
    @GetMapping("/sessions")
    fun list(@PathVariable pid: String): JsonNode = helper.list(pid)

    @PostMapping("/sessions")
    fun create(@PathVariable pid: String, @RequestBody(required = false) body: HelperCreate?): JsonNode = helper.create(pid, body ?: HelperCreate())

    @GetMapping("/sessions/{sid}")
    fun get(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.get(pid, sid)

    @PatchMapping("/sessions/{sid}")
    fun patch(@PathVariable pid: String, @PathVariable sid: String, @RequestBody body: HelperPatch): JsonNode = helper.patch(pid, sid, body)

    @DeleteMapping("/sessions/{sid}")
    fun delete(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.delete(pid, sid)

    @PostMapping("/sessions/{sid}/turn")
    fun turn(@PathVariable pid: String, @PathVariable sid: String, @RequestBody body: HelperTurn): JsonNode = helper.turn(pid, sid, body)

    @PostMapping("/sessions/{sid}/stop")
    fun stop(@PathVariable pid: String, @PathVariable sid: String): JsonNode = helper.stop(pid, sid)

    @GetMapping("/commands")
    fun commands(@PathVariable pid: String): JsonNode = helper.commands(pid)
}
