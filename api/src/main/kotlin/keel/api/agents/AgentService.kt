package keel.api.agents

import com.fasterxml.jackson.databind.JsonNode
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Json
import keel.api.common.NotFound
import keel.api.common.Slug
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.projects.ProjectService
import keel.api.settings.Model
import keel.api.settings.Settings
import keel.api.settings.SettingsService
import keel.api.skills.SkillService
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Paths

data class Agent(
    val id: String,
    val label: String,
    val about: String,
    val custom: Boolean,
    val phases: List<String>,
    val model: Model,
    val tools: List<String>,
    val skills: List<String>,
    val prompt: String,
    val enabled: Boolean,
    val overridden: List<String>,
    /** Which lane this agent works in: "follow" (the workflow decides), "api" or "web". */
    val lane: String = "follow",
    /** The project knowledge it uses (default from its front matter, then the project's override). */
    val knowledge: Knowledge = Knowledge.FALLBACK,
    /** Rough tokens of its allowed sections that exist in the project (file size / 4). */
    val knowledgeTokens: Int = 0,
    /** The project's docs/knowledge sections that exist, with their rough token cost. */
    val knowledgeFiles: Map<String, Int> = emptyMap(),
)

/** Project override (PUT /api/projects/{pid}/agents/{aid}); null = keep the default. */
data class AgentOverride(
    val model: Model? = null,
    val tools: List<String>? = null,
    val skills: List<String>? = null,
    val prompt: String? = null,
    val enabled: Boolean? = null,
    val lane: String? = null,
    val knowledge: KnowledgePatch? = null,
)

data class CustomAgent(
    val id: String? = null,
    val label: String? = null,
    val name: String? = null,
    val about: String = "",
    val phases: List<String> = listOf("any"),
    val model: Model? = null,
    val tools: List<String> = listOf("Read", "Grep", "Glob"),
    val skills: List<String> = emptyList(),
    val prompt: String = "",
    val enabled: Boolean = true,
)

@Service
class AgentService(
    private val jdbc: JdbcTemplate,
    private val catalog: AgentCatalog,
    private val settings: SettingsService,
    private val skills: SkillService,
    private val projects: ProjectService,
    private val engine: EngineClient,
    private val secrets: SecretService,
) {
    /** The model an agent gets from settings: implementer / reviewers / everyone else. */
    fun baseModel(id: String, s: Settings): Model = when {
        id == "implementer" -> s.implementerModel
        id in AgentCatalog.REVIEWERS -> s.reviewerModel
        else -> s.defaultModel
    }

    private fun overrides(pid: String): Map<String, AgentOverride> =
        jdbc.query("SELECT agent_id, json FROM agent_overrides WHERE project_id = ?", { rs, _ ->
            rs.getString(1) to (runCatching { Json.read<AgentOverride>(rs.getString(2)) }.getOrNull() ?: AgentOverride())
        }, pid).toMap()

    private fun customs(pid: String): List<CustomAgent> =
        jdbc.query("SELECT json FROM custom_agents WHERE project_id = ? ORDER BY id", { rs, _ -> Json.read<CustomAgent>(rs.getString(1)) }, pid)

    /** The docs/knowledge sections that exist in the project → file size / 4. */
    fun knowledgeFiles(root: String): Map<String, Int> {
        val dir = Paths.get(root).resolve("docs").resolve("knowledge")
        return Knowledge.SECTIONS.mapNotNull { sec ->
            val f = dir.resolve("$sec.md")
            if (Files.isRegularFile(f)) sec to (Files.size(f) / 4).toInt() else null
        }.toMap()
    }

    private fun withKnowledge(a: Agent, k: Knowledge, files: Map<String, Int>) =
        a.copy(knowledge = k, knowledgeTokens = k.sections.sumOf { files[it] ?: 0 }, knowledgeFiles = files)

    fun list(pid: String): List<Agent> {
        val files = knowledgeFiles(projects.require(pid).root)
        val s = settings.effective(pid)
        val ov = overrides(pid)
        val assigned = skills.assignedByAgent(pid)
        val defaults = catalog.defaults().map { d ->
            val o = ov[d.id] ?: AgentOverride()
            Agent(
                id = d.id, label = d.label, about = d.about, custom = false, phases = d.phases,
                model = o.model ?: baseModel(d.id, s), tools = o.tools ?: d.tools,
                skills = o.skills ?: assigned[d.id].orEmpty(), prompt = o.prompt ?: d.prompt,
                enabled = o.enabled ?: true, overridden = overriddenKeys(o), lane = o.lane ?: "follow",
            ).let { withKnowledge(it, d.knowledge.with(o.knowledge), files) }
        }
        val custom = customs(pid).map { c ->
            val id = c.id!!
            val o = ov[id] ?: AgentOverride()
            Agent(
                id = id, label = c.label ?: c.name ?: id, about = c.about, custom = true, phases = c.phases,
                model = o.model ?: c.model ?: s.defaultModel, tools = o.tools ?: c.tools,
                skills = o.skills ?: (c.skills + assigned[id].orEmpty()).distinct(), prompt = o.prompt ?: c.prompt,
                enabled = o.enabled ?: c.enabled, overridden = overriddenKeys(o), lane = o.lane ?: "follow",
            ).let { withKnowledge(it, Knowledge.FALLBACK.with(o.knowledge), files) }
        }
        return defaults + custom
    }

    private fun overriddenKeys(o: AgentOverride) = listOfNotNull(
        "model".takeIf { o.model != null }, "tools".takeIf { o.tools != null }, "skills".takeIf { o.skills != null },
        "prompt".takeIf { o.prompt != null }, "enabled".takeIf { o.enabled != null }, "lane".takeIf { o.lane != null },
        "knowledge".takeIf { o.knowledge != null },
    )

    fun get(pid: String, aid: String): Agent = list(pid).firstOrNull { it.id == aid } ?: throw NotFound("No agent called \"$aid\"")

    /** Merges the given fields into the project's override. Sending a field as JSON null clears it. */
    fun override(pid: String, aid: String, patch: Map<String, Any?>): Agent {
        get(pid, aid)
        val allowed = setOf("model", "tools", "skills", "prompt", "enabled", "lane", "knowledge")
        val unknown = patch.keys - allowed
        if (unknown.isNotEmpty()) throw BadRequest("Unknown field: ${unknown.joinToString()}", "You can change: ${allowed.joinToString()}")
        val lane = patch["lane"]
        if (lane != null && lane.toString() !in LANES) throw BadRequest("lane cannot be \"$lane\"", "Pick one of: ${LANES.joinToString()}")
        patch["knowledge"]?.let { checkKnowledge(it) }
        val prev = jdbc.query("SELECT json FROM agent_overrides WHERE project_id = ? AND agent_id = ?", { rs, _ -> rs.getString(1) }, pid, aid)
            .firstOrNull()?.let { Json.readMap(it) } ?: emptyMap()
        val next = prev.toMutableMap()
        for ((k, v) in patch) if (v == null) next.remove(k) else next[k] = v
        val parsed = try { Json.mapper.convertValue(next, AgentOverride::class.java) } catch (e: IllegalArgumentException) {
            throw BadRequest("A field has the wrong type", e.message?.take(200))
        }
        jdbc.update(
            "INSERT INTO agent_overrides(project_id, agent_id, json) VALUES (?, ?, ?) ON CONFLICT(project_id, agent_id) DO UPDATE SET json = excluded.json",
            pid, aid, Json.write(parsed),
        )
        return get(pid, aid)
    }

    fun createCustom(pid: String, body: CustomAgent): Agent {
        projects.require(pid)
        val label = body.label ?: body.name ?: body.id ?: throw BadRequest("Give the agent a name")
        val id = Slug.of(body.id ?: label)
        if (catalog.find(id) != null || customs(pid).any { it.id == id }) throw Conflict("An agent called \"$id\" already exists")
        val saved = body.copy(id = id, label = label)
        jdbc.update("INSERT INTO custom_agents(id, project_id, json) VALUES (?, ?, ?)", id, pid, Json.write(saved))
        return get(pid, id)
    }

    fun deleteCustom(pid: String, aid: String) {
        projects.require(pid)
        if (catalog.find(aid) != null) throw Conflict("keel agents cannot be deleted", "Turn it off for this project instead.")
        val n = jdbc.update("DELETE FROM custom_agents WHERE project_id = ? AND id = ?", pid, aid)
        if (n == 0) throw NotFound("No custom agent called \"$aid\"")
        jdbc.update("DELETE FROM agent_overrides WHERE project_id = ? AND agent_id = ?", pid, aid)
    }

    /** "Reply with exactly: OK" through the engine, with the agent's model for that project. */
    fun test(aid: String, pid: String): JsonNode {
        val agent = get(pid, aid)
        val m = agent.model
        val body = mutableMapOf<String, Any?>("provider" to m.provider, "mode" to m.mode, "model" to m.model)
        (if (m.mode == "api") secrets.keyForProvider(m.provider) else secrets.loginFor(m.provider))?.let { body["key"] = it }
        return engine.providerTest(body)
    }

    private fun checkKnowledge(v: Any) {
        val m = v as? Map<*, *> ?: throw BadRequest("knowledge must be an object", "Send { sections, code_graph, memory, strict, hints }.")
        val unknown = m.keys.map { it.toString() } - KNOWLEDGE_KEYS
        if (unknown.isNotEmpty()) throw BadRequest("Unknown knowledge field: ${unknown.joinToString()}", "You can change: ${KNOWLEDGE_KEYS.joinToString()}")
        val sections = m["sections"] ?: return
        val list = sections as? List<*> ?: throw BadRequest("knowledge.sections must be a list", "For example [\"architecture\", \"conventions\"].")
        val bad = list.map { it.toString() }.filter { it !in Knowledge.SECTIONS }
        if (bad.isNotEmpty()) throw BadRequest("Unknown knowledge section: ${bad.joinToString()}", "Pick from: ${Knowledge.SECTIONS.joinToString()}")
    }

    companion object {
        val LANES = setOf("follow", "api", "web")
        val KNOWLEDGE_KEYS = setOf("sections", "code_graph", "memory", "strict", "hints")
    }
}
