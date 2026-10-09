package keel.api.workflows

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.agents.AgentCatalog
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Slug
import keel.api.common.Time
import keel.api.engine.EngineClient
import keel.api.engine.EngineDown
import keel.api.flow.FlowContributor
import keel.api.projects.ProjectService
import org.springframework.beans.factory.ObjectProvider
import org.springframework.core.io.support.PathMatchingResourcePatternResolver
import org.springframework.http.HttpStatus
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

private data class WorkflowRow(
    val id: String, val projectId: String?, val version: Int, val yaml: String, val source: String, val libraryId: String?,
)

@Service
class WorkflowService(
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val projects: ProjectService,
    private val agents: AgentCatalog,
    private val mapper: ObjectMapper,
    private val contributors: ObjectProvider<FlowContributor>,
    private val features: keel.api.addons.FeatureService,
) {
    // ---- templates (engine) ---------------------------------------------------------------

    fun templates(): List<Workflow> = engine.templates().mapNotNull { fromEngine(it) }

    private fun templatesOrEmpty(): List<Workflow> = try { templates() } catch (e: EngineDown) { emptyList() }

    private fun fromEngine(node: JsonNode): Workflow? {
        val id = node.get("id")?.asText() ?: return null
        val steps: List<Step> = node.get("steps")?.let { mapper.convertValue(it, Array<Step>::class.java).toList() } ?: emptyList()
        val keelRules = node.get("keel_rules")?.asBoolean(true) ?: true
        val name = node.get("name")?.asText() ?: id
        val yaml = node.get("yaml")?.asText()?.takeIf { it.isNotBlank() }
            ?: WorkflowDoc(name = name, basedOn = node.get("based_on")?.asText(), keelRules = keelRules, steps = steps).toYaml()
        return Workflow(id, name, node.get("based_on")?.takeIf { !it.isNull }?.asText(), keelRules, node.get("version")?.asInt(1) ?: 1, steps, yaml, "keel",
            plugin = node.get("plugin")?.takeIf { !it.isNull }?.asText(), addon = node.get("addon")?.takeIf { !it.isNull }?.asText(),
            needsPlugins = node.get("needs_plugins")?.takeIf { it.isArray }?.map { it.asText() }?.takeIf { it.isNotEmpty() })
    }

    // ---- stored workflows -----------------------------------------------------------------

    private fun rows(where: String, vararg args: Any?): List<WorkflowRow> =
        jdbc.query("SELECT id, project_id, version, yaml, source, library_id FROM workflows WHERE $where ORDER BY name", { rs, _ ->
            WorkflowRow(rs.getString(1), rs.getString(2), rs.getInt(3), rs.getString(4), rs.getString(5), rs.getString(6))
        }, *args)

    private fun toWorkflow(r: WorkflowRow): Workflow {
        val doc = runCatching { WorkflowDoc.parse(r.yaml) }.getOrElse { WorkflowDoc(name = r.id) }
        return Workflow(r.id, doc.name, doc.basedOn, doc.keelRules, r.version, doc.steps, r.yaml, r.source,
            needsPlugins = doc.needsPlugins?.takeIf { it.isNotEmpty() })
    }

    /**
     * Whether a plugin's templates show for this project: the first FlowContributor that answers decides, and a plugin no
     * contributor claims stays hidden. Asked once per plugin.
     */
    private fun pluginTemplatesOn(pid: String): (String) -> Boolean {
        val found = contributors.orderedStream().toList()
        val asked = mutableMapOf<String, Boolean>()
        return { plugin -> asked.getOrPut(plugin) { found.firstNotNullOfOrNull { it.templateOn(pid, plugin) } == true } }
    }

    fun list(pid: String): List<Workflow> {
        projects.require(pid)
        val on = pluginTemplatesOn(pid)
        val all = templatesOrEmpty().filter { (it.plugin == null || on(it.plugin)) && (it.addon == null || features.addonOn(it.addon)) } +
            rows("project_id = ? OR project_id IS NULL", pid).map(::toWorkflow)
        val folders = jdbc.query("SELECT workflow_id, folder FROM workflow_folders WHERE project_id = ?",
            { rs, _ -> rs.getString(1) to rs.getString(2) }, pid).toMap()
        val runs = mutableMapOf<String, Int>()
        val last = mutableMapOf<String, LastRun>()
        jdbc.query("SELECT workflow_id, id, title, status, updated_at FROM threads WHERE project_id = ? AND workflow_id IS NOT NULL AND hidden_at IS NULL ORDER BY updated_at DESC",
            { rs, _ ->
                val wid = rs.getString(1)
                runs.merge(wid, 1, Int::plus)
                last.putIfAbsent(wid, LastRun(rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5)))
            }, pid)
        return all.map { it.copy(folder = folders[it.id], runs = runs[it.id] ?: 0, lastRun = last[it.id]) }
    }

    /** v0.9.0: the folder a workflow sits in on this project's Workflows page; blank takes it out of its folder. */
    fun setFolder(pid: String, wid: String, folder: String?): String? {
        if (list(pid).none { it.id == wid }) throw NotFound("No workflow called \"$wid\" in this project")
        val name = folder?.trim()?.replace(Regex("\\s+"), " ").orEmpty()
        if (name.length > 40) throw BadRequest("The folder name is too long", "Keep it under 40 characters.")
        if (name.isEmpty()) {
            jdbc.update("DELETE FROM workflow_folders WHERE project_id = ? AND workflow_id = ?", pid, wid)
            return null
        }
        jdbc.update("INSERT INTO workflow_folders(project_id, workflow_id, folder) VALUES (?, ?, ?) " +
            "ON CONFLICT(project_id, workflow_id) DO UPDATE SET folder = excluded.folder", pid, wid, name)
        return name
    }

    /** v0.9.0: a workflow YAML's review (KeelBot's new workflow before Save): what it does and whether keel can run it. */
    fun check(pid: String, yaml: String): InstallReview {
        projects.require(pid)
        val doc = try {
            WorkflowDoc.parse(yaml)
        } catch (e: BadRequest) {
            return InstallReview(name = "", source = "keelbot", steps = 0, gates = 0, keelRules = false, locked = 0, agents = emptyList(),
                mcp = emptyList(), tools = emptyList(), commands = emptyList(), editsFiles = false, valid = false,
                errors = listOfNotNull(e.message, e.hint), warnings = emptyList())
        }
        val res = engine.validate(yaml)
        return review(doc, "keelbot", res.get("ok")?.asBoolean() == true, res.get("errors")?.map { it.asText() } ?: emptyList())
    }

    fun get(wid: String): Workflow {
        rows("id = ?", wid).firstOrNull()?.let { return toWorkflow(it) }
        // Not ours: it can only be an engine template. Engine down → 503 (EngineDown).
        return templates().firstOrNull { it.id == wid } ?: throw NotFound("No workflow called \"$wid\"")
    }

    private fun isTemplate(wid: String) = rows("id = ?", wid).isEmpty()

    private fun newId(base: String): String {
        val b = Slug.of(base)
        var id = b
        var n = 2
        val templateIds = templatesOrEmpty().map { it.id }.toSet()
        while (rows("id = ?", id).isNotEmpty() || id in templateIds) id = "$b-${n++}"
        return id
    }

    private fun insert(id: String, projectId: String?, doc: WorkflowDoc, yaml: String, source: String, libraryId: String?): Workflow {
        val now = Time.now()
        jdbc.update(
            "INSERT INTO workflows(id, project_id, name, based_on, keel_rules, version, yaml, source, library_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            id, projectId, doc.name, doc.basedOn, if (doc.keelRules) 1 else 0, yaml, source, libraryId, now, now,
        )
        jdbc.update("INSERT INTO workflow_versions(workflow_id, version, yaml, at) VALUES (?, 1, ?, ?)", id, yaml, now)
        return get(id)
    }

    fun create(pid: String, name: String, from: String, keelRules: Boolean?): Workflow {
        projects.require(pid)
        if (name.isBlank()) throw BadRequest("Give the workflow a name")
        val doc = when {
            from == "blank" -> WorkflowDoc(name = name, keelRules = keelRules ?: false, steps = emptyList())
            from.startsWith("template:") -> {
                val t = get(from.removePrefix("template:"))
                WorkflowDoc(name = name, basedOn = "keel/${t.id}", keelRules = keelRules ?: t.keelRules, needsPlugins = t.needsPlugins, steps = t.steps)
            }
            from.startsWith("library:") -> {
                val lib = libraryDoc(from.removePrefix("library:"))
                lib.copy(id = null, name = name, keelRules = keelRules ?: lib.keelRules)
            }
            else -> throw BadRequest("from must be template:<id>, blank or library:<id>")
        }
        return insert(newId("$pid-$name"), pid, doc, doc.toYaml(), if (from.startsWith("library:")) "library" else "yours", null)
    }

    /**
     * Saves a new version. The body's YAML wins when it changed; otherwise the YAML is written from
     * the steps. The engine validates it, and locked steps cannot go while keel rules are on.
     */
    fun update(wid: String, body: WorkflowUpdate): Workflow {
        val row = rows("id = ?", wid).firstOrNull()
            ?: if (isTemplate(wid)) throw Conflict("keel templates cannot change", "Make your own workflow from this template first.") else throw NotFound("No workflow called \"$wid\"")
        val old = toWorkflow(row)
        val yamlChanged = !body.yaml.isNullOrBlank() && body.yaml.trim() != row.yaml.trim()
        val (doc, yaml) = if (yamlChanged) {
            WorkflowDoc.parse(body.yaml!!) to body.yaml
        } else {
            val prev = runCatching { WorkflowDoc.parse(row.yaml) }.getOrElse { WorkflowDoc(name = old.name) }
            val d = prev.copy(
                name = body.name?.ifBlank { null } ?: prev.name,
                basedOn = body.basedOn ?: prev.basedOn,
                keelRules = body.keelRules ?: prev.keelRules,
                steps = body.steps ?: prev.steps,
            )
            WorkflowDoc.parse(d.toYaml()) to d.toYaml()
        }
        refuseLockedRemoval(old, doc)
        validate(yaml)
        val version = row.version + 1
        val now = Time.now()
        jdbc.update(
            "UPDATE workflows SET name = ?, based_on = ?, keel_rules = ?, version = ?, yaml = ?, updated_at = ? WHERE id = ?",
            doc.name, doc.basedOn, if (doc.keelRules) 1 else 0, version, yaml, now, wid,
        )
        jdbc.update("INSERT OR REPLACE INTO workflow_versions(workflow_id, version, yaml, at) VALUES (?, ?, ?, ?)", wid, version, yaml, now)
        return get(wid)
    }

    fun refuseLockedRemoval(old: Workflow, next: WorkflowDoc) {
        if (!next.keelRules) return
        val kept = next.steps.map { it.id }.toSet()
        val removed = old.steps.filter { it.locked && it.id !in kept }
        if (removed.isNotEmpty()) {
            throw Conflict(
                "Locked steps cannot be removed while keel rules are on: ${removed.joinToString { it.name.ifBlank { it.id } }}",
                "Turn keel rules off for this workflow",
            )
        }
    }

    /** Asks the engine. Not valid → 422 with the engine's reasons. */
    fun validate(yaml: String): JsonNode {
        val res = engine.validate(yaml)
        if (res.get("ok")?.asBoolean() != true) {
            val errors = res.get("errors")?.map { it.asText() } ?: emptyList()
            throw ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "The workflow is not valid", errors.joinToString("; ").ifBlank { null })
        }
        return res
    }

    fun delete(wid: String) {
        if (rows("id = ?", wid).isEmpty()) {
            if (templatesOrEmpty().any { it.id == wid }) throw Conflict("keel templates cannot be deleted")
            throw NotFound("No workflow called \"$wid\"")
        }
        jdbc.update("DELETE FROM workflows WHERE id = ?", wid)
        jdbc.update("DELETE FROM workflow_versions WHERE workflow_id = ?", wid)
    }

    fun export(wid: String): String = get(wid).yaml

    // ---- import ---------------------------------------------------------------------------

    fun import(pid: String, yaml: String?, url: String?): Pair<Workflow, InstallReview> {
        projects.require(pid)
        val (text, source) = when {
            !yaml.isNullOrBlank() -> yaml to "file"
            !url.isNullOrBlank() -> fetch(url) to url
            else -> throw BadRequest("Send yaml or url")
        }
        val doc = WorkflowDoc.parse(text)
        val res = engine.validate(text)
        val valid = res.get("ok")?.asBoolean() == true
        val errors = res.get("errors")?.map { it.asText() } ?: emptyList()
        if (!valid) throw ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "The workflow is not valid", errors.joinToString("; ").ifBlank { null })
        val wf = insert(newId("$pid-${doc.name}"), pid, doc.copy(id = null), text, "yours", null)
        return wf to review(doc, source, valid, errors)
    }

    fun review(doc: WorkflowDoc, source: String, valid: Boolean = true, errors: List<String> = emptyList()): InstallReview {
        val agentIds = doc.steps.mapNotNull { it.agent } + doc.steps.flatMap { s -> s.lanes.orEmpty().filter { it.kind == "agent" }.mapNotNull { it.sub } }
        val tools = doc.steps.flatMap { it.tools.orEmpty() }.distinct()
        val mcp = (doc.mcp.orEmpty() + tools.filter { it.startsWith("mcp:") }.map { it.split(':').getOrElse(1) { "" } }).filter { it.isNotBlank() }.distinct()
        val commands = doc.steps.mapNotNull { it.action }.filter { it.startsWith("run:") }.map { it.removePrefix("run:").trim() }
        val edits = agentIds.any { agents.writes(it) } || doc.steps.any { it.action == "commit" || it.action == "write_config" } || commands.isNotEmpty()
        val warnings = buildList {
            if (commands.isNotEmpty()) add("It runs ${commands.size} shell command(s) in your repo.")
            if (!doc.keelRules) add("keel rules are off: locked steps such as gates can be removed.")
            val unknown = agentIds.filter { agents.find(it) == null }.distinct()
            if (unknown.isNotEmpty()) add("Unknown agents (add them as custom agents): ${unknown.joinToString()}")
            if (mcp.isNotEmpty()) add("It wants MCP servers: ${mcp.joinToString()}")
        }
        return InstallReview(
            name = doc.name, source = source, steps = doc.steps.size, gates = doc.steps.count { it.kind == "gate" },
            keelRules = doc.keelRules, locked = doc.steps.count { it.locked }, agents = agentIds.distinct(), mcp = mcp,
            tools = tools, commands = commands, editsFiles = edits, valid = valid, errors = errors, warnings = warnings,
        )
    }

    private fun fetch(url: String): String {
        val uri = runCatching { URI(url) }.getOrNull()
        if (uri == null || uri.scheme !in setOf("http", "https") || uri.host.isNullOrBlank()) {
            throw BadRequest("The url must start with http:// or https://")
        }
        val client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).followRedirects(HttpClient.Redirect.NORMAL).build()
        val res = try {
            client.send(HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(10)).GET().build(), HttpResponse.BodyHandlers.ofInputStream())
        } catch (e: Exception) {
            throw BadRequest("Could not download the workflow", e.message)
        }
        if (res.statusCode() !in 200..299) throw BadRequest("The url answered ${res.statusCode()}")
        val bytes = res.body().use { it.readNBytes(MAX_IMPORT_BYTES + 1) }
        if (bytes.size > MAX_IMPORT_BYTES) throw BadRequest("The file is too big", "A workflow file must be under ${MAX_IMPORT_BYTES / 1024} KB.")
        return String(bytes, Charsets.UTF_8)
    }

    // ---- library --------------------------------------------------------------------------

    private val libraryDocs: Map<String, Pair<WorkflowDoc, String>> by lazy {
        PathMatchingResourcePatternResolver().getResources("classpath:library/*.yaml").associate { r ->
            val text = r.inputStream.use { String(it.readAllBytes()) }
            val doc = WorkflowDoc.parse(text)
            (doc.id ?: r.filename!!.removeSuffix(".yaml")) to (doc to text)
        }.toSortedMap()
    }

    private fun libraryDoc(id: String): WorkflowDoc = libraryDocs[id]?.first ?: throw NotFound("No library item called \"$id\"")

    fun library(pid: String?): List<LibraryItem> = libraryDocs.map { (id, pair) ->
        val doc = pair.first
        val installed = if (pid == null) rows("library_id = ?", id).isNotEmpty()
        else rows("library_id = ? AND (project_id = ? OR project_id IS NULL)", id, pid).isNotEmpty()
        val r = review(doc, doc.source ?: "keel")
        LibraryItem(
            id = id, name = doc.name, source = doc.source ?: "keel", version = doc.version ?: "1.0.0", about = doc.about ?: "",
            steps = r.steps, gates = r.gates, estTokens = doc.estTokens ?: 0, agents = r.agents, mcp = r.mcp,
            editsFiles = r.editsFiles, installed = installed,
        )
    }

    fun install(pid: String, id: String, scope: String): Workflow {
        projects.require(pid)
        val (doc, text) = libraryDocs[id] ?: throw NotFound("No library item called \"$id\"")
        val projectId = when (scope) {
            "project" -> pid
            "all" -> null
            else -> throw BadRequest("scope must be project or all")
        }
        val existing = if (projectId == null) rows("library_id = ? AND project_id IS NULL", id) else rows("library_id = ? AND project_id = ?", id, projectId)
        existing.firstOrNull()?.let { return toWorkflow(it) }
        val wid = newId(if (projectId == null) "lib-$id" else "$pid-$id")
        return insert(wid, projectId, doc, text, "library", id)
    }

    companion object {
        const val MAX_IMPORT_BYTES = 256 * 1024
    }
}
