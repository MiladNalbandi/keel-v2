package keel.api.stacks

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import com.fasterxml.jackson.databind.node.ObjectNode
import keel.api.common.ApiException
import keel.api.common.Conflict
import keel.api.marketplace.Marketplace
import keel.api.projects.ProjectService
import keel.api.workflows.MarketVersions
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service

/** POST /api/projects/{pid}/stacks/market/install. [replace]: an update over another installed version. */
data class MarketStackInstall(val name: String = "", val version: String? = null, val replace: Boolean = false)

/**
 * Stacks in the marketplace, api side (keel 0.18.9, the fifth kind): the engine finds a stack in the signed catalogs and
 * fetches its verified YAML; the engine's stack save (runtime/stack_edit.py) checks it again and writes it into one
 * project as `.keel/stacks/<name>.yml`, remembering its catalog, version and sha256 (.keel/stacks/.installed.json). Nothing
 * restarts: keel reads a project's stacks when it needs them.
 *
 * keel's own stacks (content/stacks, content/packs) are listed too, under `builtin`: a keel stack is used wherever it
 * is detected; a keel pack installs with `POST /api/projects/{pid}/stacks/{name}/install`. Remove is
 * `DELETE /api/projects/{pid}/stacks/{name}`.
 */
@Service
class MarketStacks(
    private val marketplace: Marketplace,
    private val stacks: StackService,
    private val projects: ProjectService,
    private val mapper: ObjectMapper,
) {
    private fun mine(project: String?): Map<String, Stack> =
        if (project.isNullOrBlank()) emptyMap() else stacks.list(project).associateBy { it.name }

    /** A hit + installed {version, edited, origin} (or null: a marketplace install only) and update. */
    private fun decorate(hit: ObjectNode, have: Map<String, Stack>) {
        val st = have[hit.path("name").asText()]?.takeIf { it.origin == "marketplace" }
        val version = st?.market?.get("version")?.toString()
        if (st == null || version == null) hit.putNull("installed") else hit.set<JsonNode>("installed", mapper.createObjectNode()
            .put("version", version).put("edited", st.market?.get("edited") == true).put("origin", st.origin))
        val best = hit.path("version").takeIf { it.isTextual }?.asText()
        val update = if (version != null && best != null && MarketVersions.newer(best, version)) best else null
        if (update == null) hit.putNull("update") else hit.put("update", update)
        // the project has its own stack with this name (not from the marketplace): an install would replace it
        val other = have[hit.path("name").asText()]?.takeIf { it.origin != "marketplace" && it.source == "project" }
        if (other == null) hit.putNull("conflict") else hit.put("conflict", other.origin)
    }

    /** keel's own stacks, with what this project does with each (used, turned off, a pack installed). */
    private fun library(project: String?, q: String?): List<Map<String, Any?>> {
        val have = mine(project)
        val words = q.orEmpty().lowercase().split(' ').filter { it.isNotBlank() }
        return stacks.library().filter { s -> words.all { w -> "${s["name"]} ${s["lane"]} ${s["source"]} ${s["detect"]}".lowercase().contains(w) } }
            .map { s ->
                val here = have[s["name"]]
                s - "yaml" + mapOf("builtin" to true, "publisher" to "keel", "trust" to "content",
                    "used" to (here != null && here.detected && !here.off && here.source == s["source"]),
                    "off" to (here?.off == true), "installed" to (s["source"] == "keel pack" && here?.origin == "pack"))
            }
    }

    /**
     * The catalogs' stacks (+ installed | null, update, conflict) and keel's own (`builtin`). When no catalog answers,
     * `stacks` is empty and `catalog_error` says why; keel's own are still there.
     */
    fun search(q: String?, category: String?, project: String?): JsonNode {
        if (!project.isNullOrBlank()) projects.require(project)
        val out = try {
            marketplace.stacks(q, category).deepCopy<JsonNode>() as ObjectNode
        } catch (e: ApiException) {
            mapper.createObjectNode().apply {
                putArray("stacks")
                putArray("sources")
                set<JsonNode>("categories", mapper.valueToTree(CATEGORIES))
                set<JsonNode>("catalog_error", mapper.createObjectNode().put("error", e.message).put("hint", e.hint))
            }
        }
        val have = mine(project)
        out.path("stacks").forEach { decorate(it as ObjectNode, have) }
        if (!out.has("catalog_error")) out.putNull("catalog_error")
        out.set<JsonNode>("builtin", mapper.valueToTree(library(project, q)))
        return out
    }

    /**
     * One stack: the catalog's detail + installed/update + the verified YAML of the newest fitting version (`yaml`),
     * keel's review (`review`), keel's check against the project (`check`, with a project) and the catalog's review
     * (`catalog_review`); a fetch that fails is `fetch_error`. A name no catalog lists but keel has is keel's own:
     * `builtin: true` with its YAML and review.
     */
    fun detail(name: String, project: String?): JsonNode {
        if (!project.isNullOrBlank()) projects.require(project)
        val found = try {
            marketplace.stack(name).deepCopy<JsonNode>() as ObjectNode
        } catch (e: ApiException) {
            if (e.status != HttpStatus.NOT_FOUND && e.status != HttpStatus.BAD_GATEWAY && e.status != HttpStatus.SERVICE_UNAVAILABLE) throw e
            return builtin(name, project) ?: throw e
        }
        decorate(found, mine(project))
        found.set<JsonNode>("catalog_review", found.path("review").deepCopy())
        found.putNull("yaml")
        found.putNull("fetch_error")
        found.putNull("check")
        found.put("builtin", false)
        val version = found.path("version").takeIf { it.isTextual }?.asText()
        if (version == null || !found.path("refused").isNull && !found.path("refused").isMissingNode) return found
        try {
            val fetched = marketplace.fetchStack(name, version)
            found.put("yaml", fetched.path("yaml").asText(""))
            found.set<JsonNode>("review", fetched.path("review"))
            if (!project.isNullOrBlank()) {
                found.set<JsonNode>("check", stacks.check(project, StackText(yaml = fetched.path("yaml").asText(""), replace = true)))
            }
        } catch (e: ApiException) {
            found.set<JsonNode>("fetch_error", mapper.createObjectNode().put("error", e.message).put("hint", e.hint))
        }
        return found
    }

    private fun builtin(name: String, project: String?): JsonNode? {
        val s = stacks.library().firstOrNull { it["name"] == name } ?: return null
        val yaml = s["yaml"]?.toString().orEmpty()
        val out = mapper.valueToTree<ObjectNode>(library(project, null).first { it["name"] == name })
        out.put("title", name)
        out.put("yaml", yaml)
        out.put("verified", true)
        out.put("publisher_title", "keel")
        out.putNull("fetch_error")
        out.putNull("update")
        val check = if (project.isNullOrBlank()) stacks.checkAlone(yaml) else stacks.check(project, StackText(yaml = yaml, replace = true))
        out.set<JsonNode>("check", if (project.isNullOrBlank()) mapper.nullNode() else check)
        out.set<JsonNode>("review", check.path("review"))
        out.set<JsonNode>("checks", mapper.valueToTree(listOf(
            "it ships with keel's image: keel's own commands, in every project that it detects",
            if (s["source"] == "keel pack") "a keel pack: Install pack copies its stack file and its skills into the project" else
                "it cannot be removed; a project that does not want it turns it off (Build › Stacks)")))
        return out
    }

    /**
     * Fetch the verified YAML and save it with the engine's stack save (source marketplace: catalog, version, sha256).
     * 409 the same version installed, or the project already has a stack with that name (another version, its own, or
     * an installed pack) without [MarketStackInstall.replace]; a check that fails is 422; a fetch refusal keeps the
     * engine's status (409, 502).
     */
    fun install(pid: String, body: MarketStackInstall): JsonNode {
        projects.require(pid)
        val name = Marketplace.checkPackName(body.name, "stack")
        val fetched = marketplace.fetchStack(name, body.version)
        val version = fetched.path("version").asText()
        val here = mine(pid)[name]?.takeIf { it.source == "project" }
        if (here != null) {
            val same = here.origin == "marketplace" && here.market?.get("version")?.toString() == version
            if (same && !body.replace) throw Conflict("$name $version is installed in this project already.")
            if (!body.replace) {
                throw Conflict("This project has a stack called $name already (${if (here.origin == "marketplace") "version ${here.market?.get("version")}" else "its ${here.origin}"}).",
                    "Install it with replace to write over it.")
            }
        }
        // 0.21.3 verified: the engine refuses an unverified publisher's setup block unless the person allows it, and a
        // stack's setup never runs before the person's OK in the Inbox (engine runtime/stack_edit.py save)
        val market = mapOf("catalog" to fetched.path("catalog").asText(), "version" to version, "sha256" to fetched.path("sha256").asText(),
            "verified" to fetched.path("verified").asBoolean(false))
        val saved = stacks.save(pid, StackText(yaml = fetched.path("yaml").asText(""), replace = here != null), "marketplace", market) as ObjectNode
        saved.set<JsonNode>("market", mapper.createObjectNode().put("catalog", fetched.path("catalog").asText()).put("name", name)
            .put("version", version).put("sha256", fetched.path("sha256").asText()))
        saved.set<JsonNode>("review", fetched.path("review"))
        return saved
    }

    companion object {
        /** The marketplace's stack categories (the engine's kinds.STACKS). */
        val CATEGORIES = listOf("jvm", "javascript", "python", "php", "go", "rust", "dotnet", "other")
    }
}
