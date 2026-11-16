package keel.api.workflows

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.agents.AgentCatalog
import keel.api.agents.AgentNames
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.Hash
import keel.api.common.NotFound
import keel.api.common.Slug
import keel.api.marketplace.Marketplace
import keel.api.pluginhost.PluginHost
import keel.api.projects.ProjectService
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service
import java.io.ByteArrayOutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

data class MarketInstallBody(val name: String = "", val version: String? = null, val scope: String = "project")
data class MarketUpdateBody(val version: String? = null, val mode: String = "keep")
data class MarketInstallResult(val workflow: Workflow, val review: InstallReview)

/** kept: the person's changed copy that "keep" left as theirs (null when the row itself got the new version). */
data class MarketUpdateResult(val workflow: Workflow, val review: InstallReview, val kept: String? = null)

/** Versions in semver's order (a pre-release before its release; build metadata after '+' does not count). */
object MarketVersions {
    private fun key(v: String): Triple<List<Int>, Int, List<Pair<Int, String>>> {
        val (core, pre) = v.substringBefore('+').let { it.substringBefore('-') to it.substringAfter('-', "") }
        val numbers = core.split('.').map { it.toIntOrNull() ?: 0 }
        val parts = if (pre.isEmpty()) emptyList() else pre.split('.').map { p -> p.toIntOrNull()?.let { 0 to it.toString().padStart(12, '0') } ?: (1 to p) }
        return Triple(numbers, if (pre.isEmpty()) 1 else 0, parts)
    }

    private fun <T : Comparable<T>> cmpLists(a: List<T>, b: List<T>): Int {
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrNull(i) ?: return -1
            val y = b.getOrNull(i) ?: return 1
            val c = x.compareTo(y)
            if (c != 0) return c
        }
        return 0
    }

    fun compare(a: String, b: String): Int {
        val (an, ar, ap) = key(a)
        val (bn, br, bp) = key(b)
        val n = cmpLists(an.map { it.toString().padStart(12, '0') }, bn.map { it.toString().padStart(12, '0') })
        if (n != 0) return n
        if (ar != br) return ar.compareTo(br)
        return cmpLists(ap.map { "${it.first}:${it.second}" }, bp.map { "${it.first}:${it.second}" })
    }

    fun newer(a: String, b: String): Boolean = compare(a, b) > 0
}

/**
 * The workflow marketplace, api side (docs/plugins/21-workflow-marketplace.md): the engine finds a workflow in the signed
 * catalogs and fetches its verified YAML; keel checks what it needs, computes its Install review again (it must be the
 * catalog's), validates it with the engine and saves it as a workflow of one project or of all projects. Update writes a
 * new version of the same row, or (when the person changed their copy and wants to keep it) a new copy. Nothing restarts.
 */
@Service
class MarketWorkflows(
    private val workflows: WorkflowService,
    private val marketplace: Marketplace,
    private val agents: AgentCatalog,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
) {
    // ---- what this project has installed --------------------------------------------------

    /** The marketplace rows a project sees (its own and the all-projects ones; only the latter without a project). */
    private fun installedRows(project: String?): List<WorkflowRow> =
        if (project.isNullOrBlank()) workflows.rows("market_name IS NOT NULL AND project_id IS NULL")
        else workflows.rows("market_name IS NOT NULL AND (project_id = ? OR project_id IS NULL)", project)

    /** {version, scope, wid} of this name: the project's own copy first, then the all-projects one; or null. */
    private fun installedOf(name: String, rows: List<WorkflowRow>): ObjectNode? {
        val row = rows.filter { it.marketName == name }.sortedBy { if (it.projectId != null) 0 else 1 }.firstOrNull() ?: return null
        return mapper.createObjectNode().put("version", row.marketVersion).put("scope", if (row.projectId == null) "all" else "project").put("wid", row.id)
    }

    /** A hit + installed and update for this project. */
    private fun decorate(hit: ObjectNode, rows: List<WorkflowRow>) {
        val installed = installedOf(hit.path("name").asText(), rows)
        hit.set<JsonNode>("installed", installed ?: mapper.nullNode())
        val best = hit.path("version").takeIf { it.isTextual }?.asText()
        val update = if (installed != null && best != null && MarketVersions.newer(best, installed.path("version").asText())) best else null
        if (update == null) hit.putNull("update") else hit.put("update", update)
    }

    private fun checkProject(project: String?) {
        if (!project.isNullOrBlank()) projects.require(project)
    }

    fun search(q: String?, category: String?, project: String?): JsonNode {
        checkProject(project)
        val out = marketplace.workflows(q, category).deepCopy<JsonNode>() as ObjectNode
        val rows = installedRows(project)
        out.path("workflows").forEach { decorate(it as ObjectNode, rows) }
        return out
    }

    /**
     * The engine's detail + installed/update + the YAML of the newest fitting version (fetched and verified), its steps
     * (the shape of a Workflow's steps) and keel's own Install review (the catalog's stays as catalog_review). A workflow
     * keel would refuse (an old catalog, no version for this keel) has yaml null and no steps. 409 when keel's review is
     * not the catalog's.
     */
    fun detail(name: String, project: String?): JsonNode {
        checkProject(project)
        val out = marketplace.workflow(name).deepCopy<JsonNode>() as ObjectNode
        decorate(out, installedRows(project))
        out.set<JsonNode>("catalog_review", out.path("review").deepCopy())
        val version = out.path("version").takeIf { it.isTextual }?.asText()
        if (version == null || !out.path("refused").isNull && !out.path("refused").isMissingNode) {
            out.putNull("yaml")
            out.putArray("steps")
            return out
        }
        val checked = check(marketplace.fetchWorkflow(name, version), needs = false)
        out.put("yaml", checked.yaml)
        out.set<JsonNode>("steps", mapper.valueToTree(checked.doc.steps))
        out.set<JsonNode>("review", (mapper.valueToTree<JsonNode>(checked.review) as ObjectNode).put("est_tokens", checked.doc.estTokens ?: 0))
        return out
    }

    // ---- install and update ---------------------------------------------------------------

    private class Checked(val name: String, val version: String, val catalog: String, val title: String, val yaml: String,
                          val doc: WorkflowDoc, val review: InstallReview, val sha256: String) {
        fun origin() = MarketOrigin(catalog, name, version)
    }

    private fun scopeOf(scope: String, pid: String): String? = when (scope) {
        "project" -> pid
        "all" -> null
        else -> throw BadRequest("scope must be project or all", "project: only this project; all: every project.")
    }

    /**
     * What a fetched workflow needs and what it does: the plugins it needs run in this keel (409 missing_plugins), the
     * agents it names are keel's (409 missing_agents), keel's review is the catalog's in agents, MCP servers, commands
     * and edits (409), and the engine validates it (422). [needs] false: only the review (a workflow's page shows what
     * it needs; only an install refuses it).
     */
    private fun check(fetched: JsonNode, needs: Boolean = true): Checked {
        val name = fetched.path("name").asText()
        val version = fetched.path("version").asText()
        val title = fetched.path("title").asText(name)
        val yaml = fetched.path("yaml").asText("")
        if (needs) {
            val missing = fetched.path("missing_plugins").map { it.asText() }
            if (missing.isNotEmpty()) {
                throw ApiException(HttpStatus.CONFLICT, "$title needs ${and(missing)}, which this keel does not run.",
                    "Install it in Settings › Plugins (or turn it on), restart keel, then install this workflow.",
                    mapOf("missing_plugins" to missing))
            }
        }
        val doc = try {
            WorkflowDoc.parse(yaml)
        } catch (e: BadRequest) {
            throw ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "$title $version is not a workflow keel can read: ${e.message}", e.hint)
        }
        val review = workflows.review(doc, "marketplace")
        if (needs) {
            val wanted = (fetched.path("requires").path("agents").map { AgentNames.canonical(it.asText()) } + review.agents).distinct()
            val missing = wanted.filter { agents.find(it) == null && it !in AgentCatalog.BUILTIN }
            if (missing.isNotEmpty()) {
                throw ApiException(HttpStatus.CONFLICT, "$title needs agents keel does not have: ${missing.joinToString()}.",
                    "Agents come with plugins, or add them as custom agents; then install this workflow.",
                    mapOf("missing_agents" to missing))
            }
        }
        val differences = differences(review, fetched.path("review"))
        if (differences.isNotEmpty()) {
            throw ApiException(HttpStatus.CONFLICT, "keel's review of $title $version is not the catalog's.",
                "keel installs only what the catalog shows. Tell the catalog's maintainers. ${differences.joinToString("; ")}.",
                mapOf("differences" to differences, "review" to review, "catalog_review" to fetched.path("review")))
        }
        val sha = sha256(yaml)
        if (needs) workflows.validate(yaml)
        return Checked(name, version, fetched.path("catalog").asText(), title, yaml, doc, review, sha)
    }

    /**
     * Where keel's review and the catalog's differ: the agents, the MCP servers and the commands (as sets), and a workflow
     * that changes files while the catalog says it does not (the other way round only says more than it does: allowed,
     * since a publishing tool cannot know every custom agent).
     */
    private fun differences(keel: InstallReview, catalog: JsonNode): List<String> = buildList {
        fun words(key: String) = catalog.path(key).map { it.asText() }.toSet()
        fun compare(what: String, mine: List<String>, theirs: Set<String>) {
            if (mine.toSet() != theirs) add("$what: keel finds ${mine.toSet().sorted().ifEmpty { listOf("none") }.joinToString()}, " +
                "the catalog says ${theirs.sorted().ifEmpty { listOf("none") }.joinToString()}")
        }
        // a catalog that still says an old agent name (AgentNames.RENAMED) means the new agent
        compare("agents", keel.agents.map(AgentNames::canonical), words("agents").map(AgentNames::canonical).toSet())
        compare("MCP servers", keel.mcp, words("mcp"))
        compare("commands", keel.commands, words("commands"))
        if (keel.editsFiles && !catalog.path("edits_files").asBoolean(false)) add("edits files: keel finds it changes files, the catalog says it does not")
    }

    fun install(pid: String, body: MarketInstallBody): MarketInstallResult {
        projects.require(pid)
        val name = Marketplace.checkName(body.name, "workflow")
        val projectId = scopeOf(body.scope, pid)
        val same = if (projectId == null) workflows.rows("market_name = ? AND project_id IS NULL", name)
        else workflows.rows("market_name = ? AND project_id = ?", name, projectId)
        same.firstOrNull()?.let { row ->
            throw ApiException(HttpStatus.CONFLICT, "$name ${row.marketVersion} is installed already " +
                (if (projectId == null) "for all projects." else "in this project."), "Update it instead (Build › Workflows).",
                mapOf("installed" to mapOf("version" to row.marketVersion, "scope" to body.scope, "wid" to row.id)))
        }
        val c = check(marketplace.fetchWorkflow(name, body.version))
        val wf = workflows.insertMarket(projectId, if (projectId == null) "mkt-$name" else "$pid-$name", c.doc, c.yaml, c.origin(), c.sha256)
        return MarketInstallResult(wf, c.review)
    }

    /**
     * A newer (or another) version. An unchanged copy, or mode replace: a new version of the same row (the old one stays
     * in workflow_versions). A copy the person changed, with mode keep: a new row "<name> <version>" from the new version,
     * and their copy stays as it is, as their own workflow.
     */
    fun update(pid: String, wid: String, body: MarketUpdateBody): MarketUpdateResult {
        projects.require(pid)
        if (body.mode !in setOf("keep", "replace")) throw BadRequest("mode must be keep or replace", "keep: your changed copy stays and the new version comes as a new copy; replace: the new version replaces your copy.")
        val row = workflows.rows("id = ? AND (project_id = ? OR project_id IS NULL)", wid, pid).firstOrNull()
            ?: throw NotFound("No workflow called \"$wid\" in this project")
        val name = row.marketName ?: throw Conflict("$wid did not come from the marketplace", "Only a marketplace workflow has updates.")
        val fetched = marketplace.fetchWorkflow(name, body.version)
        val version = fetched.path("version").asText()
        if (version == row.marketVersion) throw ApiException(HttpStatus.CONFLICT, "$name $version is installed already.", "There is nothing to update.",
            mapOf("installed" to version))
        val c = check(fetched)
        val changed = row.marketSha256 != null && sha256(row.yaml) != row.marketSha256
        if (changed && body.mode == "keep") {
            val newName = "${c.doc.name} $version"
            val yaml = renamed(c.yaml, c.doc, newName)
            val doc = WorkflowDoc.parse(yaml)
            workflows.validate(yaml)
            val base = if (row.projectId == null) "mkt-$name-$version" else "$pid-$name-$version"
            val wf = workflows.insertMarket(row.projectId, base, doc, yaml, c.origin(), sha256(yaml))
            workflows.detachFromMarket(row.id)
            return MarketUpdateResult(wf, c.review, kept = row.id)
        }
        return MarketUpdateResult(workflows.replaceFromMarket(row.id, c.doc, c.yaml, c.origin(), c.sha256), c.review)
    }

    // ---- publish: a folder (zip) the person signs and sends --------------------------------

    fun publish(wid: String, name: String?, version: String?, summary: String?, category: String?, publisher: String?): Pair<String, ByteArray> {
        val wf = workflows.get(wid)
        val doc = runCatching { WorkflowDoc.parse(wf.yaml) }.getOrElse { WorkflowDoc(name = wf.name, steps = wf.steps) }
        val n = name?.trim()?.ifBlank { null } ?: wf.market?.name ?: Slug.of(doc.name).take(32).trim('-').let { if (it.firstOrNull()?.isLetter() == true) it else "w-$it".take(32).trim('-') }
        if (!PluginHost.NAME.matches(n)) throw BadRequest("$n is not a marketplace name", "a-z, 0-9 and '-', starting with a letter, 32 at most.")
        val v = version?.trim()?.ifBlank { null } ?: wf.market?.version?.let { bumpPatch(it) } ?: "1.0.0"
        if (!SEMVER.matches(v)) throw BadRequest("$v is not a version", "A version looks like 1.2.0 (or 1.2.0-beta.1).")
        val cat = category?.trim()?.ifBlank { null } ?: "other"
        if (cat !in CATEGORIES) throw BadRequest("$cat is not a workflow category", "One of ${CATEGORIES.joinToString()}.")
        val pub = publisher?.trim()?.ifBlank { null } ?: "you"
        if (!PluginHost.NAME.matches(pub)) throw BadRequest("$pub is not a publisher id", "a-z, 0-9 and '-', starting with a letter, 32 at most.")
        val about = summary?.trim()?.ifBlank { null } ?: doc.about?.trim()?.ifBlank { null } ?: "What ${doc.name} does, in one line."
        val file = "$n-$v.yaml"
        val yaml = exportYaml(wf.yaml)
        val entry = buildString {
            append("# workflows/$n.yml in keel-studio/keel-marketplace: the marketplace entry of this workflow\n")
            append("name: $n\n")
            append("title: ${quote(doc.name)}\n")
            append("publisher: $pub # your publisher id (publishers/$pub.yml)\n")
            append("repo: https://github.com/$pub/keel-workflows # the repo whose releases hold $file (leave it out when the file is in the marketplace repo)\n")
            append("category: $cat # ${CATEGORIES.joinToString(", ")}\n")
            append("summary: ${quote(about)}\n")
            append("tags: []\n")
        }
        val zip = ByteArrayOutputStream()
        ZipOutputStream(zip).use { z ->
            fun add(path: String, text: String) {
                z.putNextEntry(ZipEntry(path))
                z.write(text.toByteArray(Charsets.UTF_8))
                z.closeEntry()
            }
            add(file, yaml)
            add("workflows/$n.yml", entry)
            add("PUBLISH.md", publishGuide(n, v, doc.name, pub, file))
        }
        return "$n-$v.zip" to zip.toByteArray()
    }

    private fun publishGuide(n: String, v: String, title: String, pub: String, file: String): String = """
        |# Publish $title $v to the keel marketplace
        |
        |This folder holds what the marketplace needs. keel never signs anything: your secret key stays on your computer.
        |
        |- `$file`: the workflow people install
        |- `workflows/$n.yml`: its entry in `keel-studio/keel-marketplace` (check the publisher and the repo)
        |- `PUBLISH.md`: this page
        |
        |## 1 · The tool
        |
        |```
        |alias keel-plugin='uvx --from "git+https://github.com/MiladNalbandi/keel-v2@plugin-base#subdirectory=tools/keel-plugin" keel-plugin'
        |```
        |
        |## 2 · Check it
        |
        |```
        |keel-plugin workflow lint $file
        |```
        |
        |## 3 · Your key (only once)
        |
        |```
        |keel-plugin keygen $pub          # $pub.key (secret: never commit it) and $pub.pub (public)
        |```
        |
        |New publisher? Add `publishers/$pub.yml` to the pull request below, with the key line of `$pub.pub`
        |(see keel's docs/plugins/17-publish-your-plugin.md, step 6).
        |
        |## 4 · Sign it
        |
        |```
        |keel-plugin sign $file --key-file $pub.key
        |keel-plugin verify $file --pub $pub.pub
        |```
        |
        |## 5 · Put the file where keel downloads it (one of the two)
        |
        |a) A release in your repo (the `repo` of `workflows/$n.yml`):
        |
        |```
        |gh release create $n-v$v $file $file.minisig --repo $pub/keel-workflows --title "$title $v"
        |```
        |
        |b) Or the marketplace repo itself, for a small workflow: in the pull request below, add
        |`workflows/$n/$v.yaml` and `workflows/$n/$v.yaml.minisig` (these two files, renamed), and leave `repo` out.
        |
        |## 6 · The pull request
        |
        |```
        |gh repo fork keel-studio/keel-marketplace --clone
        |cd keel-marketplace
        |git checkout -b workflow-$n-$v
        |cp ../workflows/$n.yml workflows/$n.yml
        |# (b) only: mkdir -p workflows/$n && cp ../$file workflows/$n/$v.yaml && cp ../$file.minisig workflows/$n/$v.yaml.minisig
        |keel-plugin index . --check
        |git add workflows publishers && git commit -m "Workflow $n $v"
        |git push -u origin HEAD
        |gh pr create --repo keel-studio/keel-marketplace --title "Workflow $n $v" --body "Adds the workflow $n $v."
        |```
        |
        |The pull request's check lints the file, checks its signature with your key and computes its Install review (steps,
        |gates, agents, MCP servers, commands, files it changes). A maintainer merges it; within an hour keel lists it in
        |Settings › Plugins › Marketplace › Workflows.
        |""".trimMargin()

    companion object {
        val CATEGORIES = listOf("code", "review", "docs", "release", "quality", "ops", "other")
        private val SEMVER = Regex("^\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.-]+)?$")

        fun sha256(text: String): String =
            Hash.sha256Hex(text)

        private fun and(words: List<String>) = if (words.size == 1) words[0] else words.dropLast(1).joinToString(", ") + " and " + words.last()

        private fun bumpPatch(v: String): String {
            val core = v.substringBefore('-').substringBefore('+').split('.')
            return if (core.size == 3 && core.all { it.toIntOrNull() != null }) "${core[0]}.${core[1]}.${core[2].toInt() + 1}" else "1.0.0"
        }

        /** A YAML scalar in single quotes (inner quotes doubled). */
        fun quote(text: String): String = "'" + text.replace("'", "''").replace("\n", " ") + "'"

        /** The workflow's YAML with a new top-level name (its other lines as they are), or written again when it has none. */
        fun renamed(yaml: String, doc: WorkflowDoc, name: String): String {
            val line = Regex("(?m)^name:[ \\t]*.*$")
            return if (line.containsMatchIn(yaml)) line.replaceFirst(yaml, Regex.escapeReplacement("name: ${quote(name)}"))
            else doc.copy(name = name).toYaml()
        }

        /**
         * The export for the marketplace: the YAML as it is, without a top-level `version:` that is not a whole number
         * (keel's engine reads `version` as a number; the marketplace's version is in the file name and the catalog).
         */
        fun exportYaml(yaml: String): String =
            Regex("(?m)^version:[ \\t]*+(?![0-9]+[ \\t]*(#.*)?$).*\\n?").replace(yaml, "")
    }
}
