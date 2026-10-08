package keel.product

import keel.api.common.BadRequest
import keel.api.common.NotFound
import keel.api.common.Slug
import keel.api.common.Time
import keel.api.projects.ProjectService
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Paths

data class TeamBody(
    val name: String? = null,
    val lead: String? = null,
    val jiraProject: String? = null,
    val capacityDays: Double? = null,
    val members: List<String>? = null,
    val owns: List<OwnsBody>? = null,
)

data class OwnsBody(val projectId: String = "", val glob: String = "**")

data class ImportResult(val teams: List<String>, val paths: Int, val read: List<String>, val skipped: List<String>)

data class TeamPage(val name: String, val title: String, val text: String)

data class TeamKnowledge(val team: Team, val pages: List<TeamPage>, val suggestions: List<TeamSuggestion>)

data class RepoOwner(val projectId: String, val name: String, val root: String, val teams: List<String>)

/**
 * Teams: who owns which code (from CODEOWNERS, or by hand), their lead, their Jira project, their free capacity, and
 * their own pages (definition of ready, how they split and estimate stories...) that keel's agents follow when they
 * plan work for that team. The pages live in the product repo: teams/<team>/knowledge/<page>.md.
 */
@Service
class TeamService(private val store: ProductStore, private val repo: ProductRepo, private val projects: ProjectService) {
    companion object {
        val PAGES = linkedMapOf(
            "definition-of-ready" to "Definition of ready",
            "how-we-split" to "How we split stories",
            "how-we-estimate" to "How we estimate",
            "conventions" to "Our conventions",
            "glossary" to "Glossary",
            "contacts" to "Contacts and rules",
            "lessons" to "Lessons from past work",
        )
        private val PAGE = Regex("^[a-z][a-z0-9-]{1,40}$")
        private val CODEOWNERS = listOf(".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")
    }

    fun list(): List<Team> = store.teams()

    fun get(id: String): Team = store.team(id) ?: throw NotFound("No team $id")

    fun create(b: TeamBody): Team {
        val name = b.name?.trim().orEmpty()
        if (name.isEmpty()) throw BadRequest("Give the team a name")
        var id = Slug.of(name).ifBlank { "team" }
        var n = 2
        while (store.team(id) != null) id = "${Slug.of(name)}-${n++}"
        val now = Time.now()
        store.saveTeam(Team(id, name, b.lead?.trim(), b.jiraProject?.trim()?.uppercase(), b.capacityDays, b.members.orEmpty(), "manual", emptyList(), now, now))
        b.owns?.let { setOwns(id, it) }
        return get(id)
    }

    fun update(id: String, b: TeamBody): Team {
        val t = get(id)
        store.saveTeam(t.copy(name = b.name?.trim()?.ifEmpty { null } ?: t.name, lead = b.lead ?: t.lead,
            jiraProject = b.jiraProject?.trim()?.uppercase() ?: t.jiraProject, capacityDays = b.capacityDays ?: t.capacityDays,
            members = b.members ?: t.members, updatedAt = Time.now()))
        b.owns?.let { setOwns(id, it) }
        return get(id)
    }

    private fun setOwns(id: String, owns: List<OwnsBody>) {
        owns.forEach { projects.require(it.projectId) }
        store.setPaths(id, owns.map { TeamPath(id, it.projectId, it.glob.trim().ifEmpty { "**" }, "manual") }, "manual")
    }

    fun delete(id: String) {
        get(id)
        store.deleteTeam(id)
    }

    /** CODEOWNERS of every project: each "@org/team" becomes a team that owns those paths in that project. */
    fun importCodeowners(): ImportResult {
        val productRoot = repo.root.toString()
        val read = mutableListOf<String>()
        val skipped = mutableListOf<String>()
        val found = linkedMapOf<String, MutableList<TeamPath>>()
        for (p in projects.rows()) {
            if (p.root == productRoot || projects.hidden(p.id)) continue
            val file = CODEOWNERS.map { Paths.get(p.root).resolve(it) }.firstOrNull { Files.isRegularFile(it) }
            if (file == null) { skipped += p.id; continue }
            read += "${p.id}: ${Paths.get(p.root).relativize(file)}"
            for (line in Files.readAllLines(file)) {
                val parts = line.substringBefore('#').trim().split(Regex("\\s+")).filter { it.isNotBlank() }
                if (parts.size < 2) continue
                val glob = parts[0]
                for (owner in parts.drop(1).filter { it.startsWith("@") && it.contains("/") }) {
                    val teamName = owner.substringAfter("/")
                    found.getOrPut(teamName) { mutableListOf() } += TeamPath(Slug.of(teamName), p.id, glob, "codeowners")
                }
            }
        }
        val now = Time.now()
        for ((name, owns) in found) {
            val id = Slug.of(name)
            val t = store.team(id)
            if (t == null) store.saveTeam(Team(id, name.replace('-', ' ').replaceFirstChar { it.uppercase() }, null, null, null, emptyList(),
                "codeowners", emptyList(), now, now))
            store.setPaths(id, owns.map { it.copy(teamId = id) }, "codeowners")
        }
        return ImportResult(found.keys.map { Slug.of(it) }, found.values.sumOf { it.size }, read, skipped)
    }

    /** The teams that own (part of) a project. */
    fun ownersOf(projectId: String): List<Team> {
        val ids = store.paths().filter { it.projectId == projectId }.map { it.teamId }.toSet()
        return store.teams().filter { it.id in ids }
    }

    fun repoOwners(): List<RepoOwner> {
        val productRoot = repo.root.toString()
        val paths = store.paths()
        return projects.rows().filter { it.root != productRoot && !projects.hidden(it.id) }.map { p ->
            RepoOwner(p.id, p.name, p.root, paths.filter { it.projectId == p.id }.map { it.teamId }.distinct())
        }
    }

    // ---- team pages
    fun knowledge(id: String): TeamKnowledge {
        val t = get(id)
        val files = repo.list("teams/$id/knowledge").filter { it.endsWith(".md") }.map { it.removeSuffix(".md") }
        val names = (PAGES.keys + files).distinct()
        val pages = names.map { n -> TeamPage(n, PAGES[n] ?: n.replace('-', ' ').replaceFirstChar { it.uppercase() }, repo.read("teams/$id/knowledge/$n.md").orEmpty()) }
        return TeamKnowledge(t, pages, store.suggestions(id))
    }

    fun savePage(id: String, page: String, text: String): TeamPage {
        get(id)
        if (!PAGE.matches(page)) throw BadRequest("A page name is lowercase words with dashes", "For example definition-of-ready.")
        repo.write("teams/$id/knowledge/$page.md", text.trim(), "team $id: $page")
        return TeamPage(page, PAGES[page] ?: page, text.trim())
    }

    /** The team pages as text for an agent (keel only reads what the team wrote). */
    fun pagesText(id: String): String = knowledge(id).pages.filter { it.text.isNotBlank() }
        .joinToString("\n") { "  - ${it.title}: ${it.text.replace(Regex("\\s+"), " ").take(600)}" }

    fun suggest(teamId: String, page: String, text: String, source: String) {
        store.saveSuggestion(TeamSuggestion(store.newId("sug"), teamId, page, text, source, "open", Time.now(), null))
    }

    fun decide(teamId: String, sid: String, accept: Boolean): TeamKnowledge {
        val s = store.suggestions(teamId).firstOrNull { it.id == sid } ?: throw NotFound("No suggestion $sid")
        if (s.status != "open") throw BadRequest("This suggestion was already ${s.status}")
        if (accept) {
            val now = repo.read("teams/$teamId/knowledge/${s.page}.md").orEmpty().trimEnd()
            repo.write("teams/$teamId/knowledge/${s.page}.md", if (now.isEmpty()) s.text else "$now\n\n${s.text}", "team $teamId: ${s.page} (keel's suggestion)")
        }
        store.saveSuggestion(s.copy(status = if (accept) "accepted" else "rejected", decidedAt = Time.now()))
        return knowledge(teamId)
    }
}
