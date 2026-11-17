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

/** market*: where a workflow installed from a catalog came from (V17; null for every other workflow). */
internal data class WorkflowRow(
    val id: String, val projectId: String?, val version: Int, val yaml: String, val source: String, val libraryId: String?,
    val marketCatalog: String? = null, val marketName: String? = null, val marketVersion: String? = null,
    val marketSha256: String? = null,
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
    private val marketplace: keel.api.marketplace.Marketplace,
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
            needsPlugins = node.get("needs_plugins")?.takeIf { it.isArray }?.map { it.asText() }?.takeIf { it.isNotEmpty() },
            about = node.text("about"), group = node.text("group"), start = node.text("start"))
    }

    // ---- stored workflows -----------------------------------------------------------------

    internal fun rows(where: String, vararg args: Any?): List<WorkflowRow> =
        jdbc.query("SELECT id, project_id, version, yaml, source, library_id, market_catalog, market_name, market_version, market_sha256 " +
            "FROM workflows WHERE $where ORDER BY name", { rs, _ ->
            WorkflowRow(rs.getString(1), rs.getString(2), rs.getInt(3), rs.getString(4), rs.getString(5), rs.getString(6),
                rs.getString(7), rs.getString(8), rs.getString(9), rs.getString(10))
        }, *args)

    private fun toWorkflow(r: WorkflowRow): Workflow {
        val doc = runCatching { WorkflowDoc.parse(r.yaml) }.getOrElse { WorkflowDoc(name = r.id) }
        return Workflow(r.id, doc.name, doc.basedOn, doc.keelRules, r.version, doc.steps, r.yaml, r.source,
            needsPlugins = doc.needsPlugins?.takeIf { it.isNotEmpty() },
            market = r.marketName?.let { MarketOrigin(r.marketCatalog.orEmpty(), it, r.marketVersion.orEmpty()) },
            about = doc.about?.takeIf { it.isNotBlank() }, group = doc.group?.takeIf { it.isNotBlank() }, start = doc.start?.takeIf { it.isNotBlank() })
    }

    private fun JsonNode.text(k: String): String? = get(k)?.takeIf { it.isTextual }?.asText()?.takeIf { it.isNotBlank() }

    /**
     * The newest fitting version of each marketplace workflow, by name (the engine's search of the catalogs keel has on
     * disk). Empty when the engine is down or answers an error: the Workflows page still lists everything.
     */
    internal fun marketNewest(): Map<String, String> = try {
        marketplace.workflows(null, null).path("workflows")
            .filter { it.path("version").isTextual }
            .associate { it.path("name").asText() to it.path("version").asText() }
    } catch (e: ApiException) {
        emptyMap()
    }

    private fun withUpdates(all: List<Workflow>): List<Workflow> {
        if (all.none { it.market != null }) return all
        val newest = marketNewest()
        return all.map { w ->
            val m = w.market ?: return@map w
            w.copy(market = m.copy(update = newest[m.name]?.takeIf { MarketVersions.newer(it, m.version) }))
        }
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
        return withUpdates(all).map { it.copy(folder = folders[it.id], runs = runs[it.id] ?: 0, lastRun = last[it.id]) }
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
                errors = listOfNotNull(e.message, e.hint), warnings = emptyList(), lines = parseLines(yaml, e))
        }
        val res = engine.validate(yaml)
        return review(doc, "keelbot", res.get("ok")?.asBoolean() == true, res.get("errors")?.map { it.asText() } ?: emptyList())
            .copy(lines = linesOf(res))
    }

    /** The engine's `lines` of a validate answer: each error at its line of the YAML. */
    private fun linesOf(res: JsonNode): List<LineError> =
        res.get("lines")?.filter { it.has("line") }?.map { LineError(it.get("line").asInt(1), it.path("message").asText()) }.orEmpty()

    /**
     * The lines of YAML keel's own reading refused (WorkflowDoc.parse): the engine's lines when it finds the problem too,
     * else the refusal on line 1 (or the line the YAML parser names).
     */
    private fun parseLines(yaml: String, e: ApiException): List<LineError> {
        val engineLines = runCatching { linesOf(engine.validate(yaml)) }.getOrDefault(emptyList())
        if (engineLines.isNotEmpty()) return engineLines
        val at = Regex("line: (\\d+)").find(e.hint.orEmpty())?.groupValues?.get(1)?.toIntOrNull() ?: 1
        return listOf(LineError(at, listOfNotNull(e.message, e.hint?.takeIf { at == 1 }).joinToString(": ")))
    }

    /** WorkflowDoc.parse, and a refusal carries its lines (the editor marks them). */
    private fun parseWithLines(yaml: String): WorkflowDoc = try {
        WorkflowDoc.parse(yaml)
    } catch (e: BadRequest) {
        throw ApiException(HttpStatus.BAD_REQUEST, e.message, e.hint, mapOf("lines" to parseLines(yaml, e)))
    }

    /** v0.17.4 a new workflow's YAML (New workflow shows it before it is made): the engine's starter, with its id here. */
    fun starter(pid: String, name: String, keelRules: Boolean): Map<String, String> {
        projects.require(pid)
        val title = name.trim().ifBlank { "My workflow" }
        val id = newId("$pid-$title")
        return mapOf("id" to id, "yaml" to engine.starter(title, id, keelRules).path("yaml").asText())
    }

    fun get(wid: String): Workflow {
        rows("id = ?", wid).firstOrNull()?.let { return toWorkflow(it) }
        // Not ours: it can only be an engine template. Engine down → 503 (EngineDown).
        return templates().firstOrNull { it.id == wid } ?: throw NotFound("No workflow called \"$wid\"")
    }

    private fun isTemplate(wid: String) = rows("id = ?", wid).isEmpty()

    internal fun newId(base: String): String {
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

    // ---- the workflow marketplace (MarketWorkflows): rows that remember their catalog, name, version and sha256

    /** A new row from a catalog: source "marketplace", the origin and the sha256 of the YAML keel wrote. */
    internal fun insertMarket(projectId: String?, base: String, doc: WorkflowDoc, yaml: String, origin: MarketOrigin, sha256: String): Workflow {
        val id = newId(base)
        insert(id, projectId, doc.copy(id = null), yaml, "marketplace", null)
        jdbc.update("UPDATE workflows SET market_catalog = ?, market_name = ?, market_version = ?, market_sha256 = ? WHERE id = ?",
            origin.catalog, origin.name, origin.version, sha256, id)
        return get(id)
    }

    /** A new version of a marketplace row with the catalog's YAML; the old one stays in workflow_versions (Roll back). */
    internal fun replaceFromMarket(wid: String, doc: WorkflowDoc, yaml: String, origin: MarketOrigin, sha256: String): Workflow {
        val row = rows("id = ?", wid).firstOrNull() ?: throw NotFound("No workflow called \"$wid\"")
        val version = row.version + 1
        val now = Time.now()
        jdbc.update(
            "UPDATE workflows SET name = ?, based_on = ?, keel_rules = ?, version = ?, yaml = ?, updated_at = ?, source = 'marketplace', " +
                "market_catalog = ?, market_name = ?, market_version = ?, market_sha256 = ? WHERE id = ?",
            doc.name, doc.basedOn, if (doc.keelRules) 1 else 0, version, yaml, now, origin.catalog, origin.name, origin.version, sha256, wid,
        )
        jdbc.update("INSERT OR REPLACE INTO workflow_versions(workflow_id, version, yaml, at) VALUES (?, ?, ?, ?)", wid, version, yaml, now)
        return get(wid)
    }

    /** The person's own changed copy after "keep my changes": it is theirs now (source yours, no catalog). */
    internal fun detachFromMarket(wid: String) {
        jdbc.update("UPDATE workflows SET source = 'yours', market_catalog = NULL, market_name = NULL, market_version = NULL, " +
            "market_sha256 = NULL WHERE id = ?", wid)
    }

    fun create(pid: String, name: String, from: String, keelRules: Boolean?): Workflow {
        projects.require(pid)
        if (name.isBlank()) throw BadRequest("Give the workflow a name")
        val doc = when {
            // v0.17.4 a small valid example (the engine's starter), so Save works as it is; the old empty one if the
            // engine cannot say
            from == "blank" -> {
                val id = newId("$pid-$name")
                val text = try {
                    engine.starter(name.trim(), id, keelRules ?: false).path("yaml").asText("")
                } catch (e: ApiException) {
                    ""
                }
                val starter = text.takeIf { it.isNotBlank() }?.let { t -> runCatching { WorkflowDoc.parse(t) }.getOrNull()?.let { it to t } }
                if (starter != null) return insert(id, pid, starter.first.copy(id = null), starter.second, "yours", null)
                WorkflowDoc(name = name, keelRules = keelRules ?: false, steps = emptyList())
            }
            from.startsWith("template:") -> {
                val t = get(from.removePrefix("template:"))
                WorkflowDoc(name = name, basedOn = "keel/${t.id}", keelRules = keelRules ?: t.keelRules, needsPlugins = t.needsPlugins,
                    ownBranch = if (wantsOwnBranch(t)) null else false, steps = t.steps)
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
        val (doc, given) = if (yamlChanged) {
            parseWithLines(body.yaml!!) to body.yaml
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
        if (yamlChanged) refuseOtherWorkflow(wid, row.yaml, doc)
        refuseLockedRemoval(old, doc)
        // saved as keel writes it (the engine's formatted: documented key order, 2-space indent, comments kept)
        val yaml = validate(given).path("formatted").asText("").ifBlank { given }
        val version = row.version + 1
        val now = Time.now()
        jdbc.update(
            "UPDATE workflows SET name = ?, based_on = ?, keel_rules = ?, version = ?, yaml = ?, updated_at = ? WHERE id = ?",
            doc.name, doc.basedOn, if (doc.keelRules) 1 else 0, version, yaml, now, wid,
        )
        jdbc.update("INSERT OR REPLACE INTO workflow_versions(workflow_id, version, yaml, at) VALUES (?, ?, ?, ?)", wid, version, yaml, now)
        return get(wid)
    }

    /**
     * A YAML whose `id:` is neither this workflow's id nor the id its own YAML carries is another workflow pasted here
     * (keel tells workflows apart by id; a new name alone is a rename). Saving it would replace this one: Import it.
     */
    internal fun refuseOtherWorkflow(wid: String, storedYaml: String, next: WorkflowDoc) {
        val other = next.id?.trim()?.takeIf { it.isNotEmpty() } ?: return
        val own = runCatching { WorkflowDoc.parse(storedYaml).id }.getOrNull()
        if (other == wid || other == own) return
        throw Conflict(
            "This YAML is a different workflow (id $other). Use Import to add it as a new workflow.",
            "Or remove its id: line to save it over this workflow.",
        )
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

    /** Asks the engine. Not valid → 422 with the engine's reasons (hint, as before) and each at its line (`lines`). */
    fun validate(yaml: String): JsonNode {
        val res = engine.validate(yaml)
        if (res.get("ok")?.asBoolean() != true) {
            val errors = res.get("errors")?.map { it.asText() } ?: emptyList()
            throw ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "The workflow is not valid", errors.joinToString("; ").ifBlank { null },
                mapOf("lines" to linesOf(res)))
        }
        return res
    }

    fun delete(wid: String) {
        if (rows("id = ?", wid).isEmpty()) {
            if (templatesOrEmpty().any { it.id == wid }) throw Conflict("keel templates cannot be deleted")
            throw NotFound("No workflow called \"$wid\"")
        }
        refuseWhileRunning(wid)
        jdbc.update("DELETE FROM workflows WHERE id = ?", wid)
        jdbc.update("DELETE FROM workflow_versions WHERE workflow_id = ?", wid)
    }

    /**
     * A workflow cannot go while one of its flows runs or waits at a gate: that flow would lose the workflow it reads.
     * Finished, stopped and failed flows keep their history (they do not need the row).
     */
    private fun refuseWhileRunning(wid: String) {
        val live = jdbc.query(
            "SELECT title FROM threads WHERE workflow_id = ? AND status IN ('running','waiting') ORDER BY updated_at DESC LIMIT 1",
            { rs, _ -> rs.getString(1) }, wid,
        ).firstOrNull() ?: return
        val name = runCatching { get(wid).name }.getOrDefault(wid)
        throw Conflict("$name has a flow running (${live.ifBlank { "untitled" }}): stop it or let it finish first.")
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
        val doc = parseWithLines(text)
        val res = engine.validate(text)
        val valid = res.get("ok")?.asBoolean() == true
        val errors = res.get("errors")?.map { it.asText() } ?: emptyList()
        if (!valid) throw ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "The workflow is not valid", errors.joinToString("; ").ifBlank { null },
            mapOf("lines" to linesOf(res)))
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

    private fun fetch(url: String): String = keel.api.common.HttpFetch.get(url, MAX_IMPORT_BYTES, keel.api.common.FetchTexts(
        notHttp = { BadRequest("The url must start with http:// or https://") },
        failed = { BadRequest("Could not download the workflow", it) },
        status = { BadRequest("The url answered $it") },
        tooBig = { BadRequest("The file is too big", "A workflow file must be under ${MAX_IMPORT_BYTES / 1024} KB.") },
    ), connectTimeout = Duration.ofSeconds(5), totalTimeout = Duration.ofSeconds(15))

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
