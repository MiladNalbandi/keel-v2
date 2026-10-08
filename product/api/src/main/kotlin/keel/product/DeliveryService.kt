package keel.product

import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.jira.JiraClient
import keel.api.jira.JiraException
import keel.api.jira.JiraService
import keel.api.projects.ProjectService
import keel.api.tasks.NewTask
import keel.api.tasks.TaskService
import keel.api.tasks.TaskStatus
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service

data class StoryView(
    val id: String,
    val epic: String,
    val team: String?,
    val repo: String?,
    val projectId: String?,
    val title: String,
    val criteria: List<String>,
    val tasks: List<String>,
    val dependsOn: List<String>,
    val estimateDays: List<Double>,
    val taskId: String?,
    val taskStatus: String?,
    val jiraKey: String?,
)

data class EpicView(val id: String, val team: String?, val title: String, val stories: List<StoryView>)

data class PlanView(
    val version: Int,
    val ok: Boolean,
    val problems: List<String>,
    val criticalPath: List<String>,
    val criticalDays: Double?,
    val teams: Any?,
    val totalDays: Any?,
    val epics: List<EpicView>,
    val progress: Progress?,
)

data class HandoffBody(
    /** tasks | jira | both (Jira first, then keel Tasks that carry the ticket keys) */
    val target: String = "tasks",
    /** story ids; empty = every story not handed off yet */
    val stories: List<String> = emptyList(),
)

data class HandoffResult(val tasks: List<String>, val jira: List<String>, val skipped: List<String>, val notes: List<String>)

/** After the plan: the stories go to keel Tasks (one per repo, with their criteria) or to Jira, and their progress rolls up. */
@Service
class DeliveryService(
    private val store: ProductStore,
    private val tasks: TaskService,
    private val jira: JiraService,
    private val projects: ProjectService,
    private val teams: TeamService,
) {
    private val log = LoggerFactory.getLogger(javaClass)

    @Suppress("UNCHECKED_CAST")
    fun saveItems(i: Initiative, version: Int, plan: Any?) {
        val id = i.id
        val epics = (plan as? Map<String, Any?>)?.get("epics") as? List<Map<String, Any?>> ?: return
        // the initiative's own repos first (by id or name): two keel projects may have the same name
        val rows = projects.rows()
        val own = rows.filter { it.id in i.repos }

        fun project(repo: String) = (own.firstOrNull { it.id == repo } ?: own.firstOrNull { it.name.equals(repo, true) }
            ?: rows.firstOrNull { it.id == repo } ?: rows.firstOrNull { it.name.equals(repo, true) })?.id
        for (e in epics) {
            val stories = e["stories"] as? List<Map<String, Any?>> ?: continue
            for (s in stories) {
                val sid = s["id"] as? String ?: continue
                val repo = s["repo"] as? String
                val pid = repo?.let { project(it) }
                store.saveItem(PlanItem(id, version, sid, e["id"] as? String ?: "EP", e["team"] as? String, repo, (s["title"] as? String ?: sid).take(300),
                    s, null, pid, null))
            }
        }
    }

    @Suppress("UNCHECKED_CAST")
    fun planView(i: Initiative, doc: ProductDoc): PlanView {
        val d = doc.data as? Map<String, Any?> ?: emptyMap()
        val items = store.items(i.id, doc.version)
        val statuses = items.mapNotNull { it.taskId }.associateWith { tid -> runCatching { tasks.get(tid).status }.getOrNull() }
        val stories = items.map { p ->
            val b = p.body
            StoryView(p.id, p.epic, p.team, p.repo, p.projectId, p.title, strings(b["criteria"]), strings(b["tasks"]), strings(b["depends_on"]),
                days(b["estimate_days"]), p.taskId, p.taskId?.let { statuses[it] }, p.jiraKey)
        }
        val epicTitles = ((d["plan"] as? Map<String, Any?>)?.get("epics") as? List<Map<String, Any?>>).orEmpty()
            .associate { (it["id"] as? String ?: "") to (it["title"] as? String ?: "") }
        val epics = stories.groupBy { it.epic }.map { (eid, list) -> EpicView(eid, list.first().team, epicTitles[eid]?.ifBlank { null } ?: eid, list) }
        return PlanView(doc.version, d["ok"] == true, strings(d["problems"]), strings(d["critical_path"]), (d["critical_days"] as? Number)?.toDouble(),
            d["teams"], d["total_days"], epics, if (items.any { it.taskId != null }) progress(items).let { Progress(it.first, it.second) } else null)
    }

    /** (done, total) over the stories handed to keel Tasks. */
    fun progress(items: List<PlanItem>): Pair<Int, Int> {
        val linked = items.filter { it.taskId != null }
        val done = linked.count { p -> runCatching { tasks.get(p.taskId!!).status == TaskStatus.DONE }.getOrDefault(false) }
        return done to items.size
    }

    fun handoff(i: Initiative, b: HandoffBody): HandoffResult {
        if (b.target !in setOf("tasks", "jira", "both")) throw BadRequest("target is tasks, jira or both")
        if (i.stage != "delivery") throw Conflict("The plan of ${i.id} is not agreed yet", "The leads agree the plan first.")
        val doc = store.latest(i.id, "plan") ?: throw NotFound("${i.id} has no plan")
        var items = store.items(i.id, doc.version)
        if (b.stories.isNotEmpty()) items = items.filter { it.id in b.stories }
        val notes = mutableListOf<String>()
        val jiraKeys = mutableListOf<String>()
        if (b.target != "tasks") {
            items = toJira(i, items.filter { it.jiraKey == null }, notes, jiraKeys).let { done ->
                val byId = done.associateBy { it.id }
                items.map { byId[it.id] ?: it }
            }
        }
        val made = mutableListOf<String>()
        val skipped = mutableListOf<String>()
        if (b.target != "jira") {
            for (p in items.filter { it.taskId == null }) {
                val pid = p.projectId
                if (pid == null) {
                    skipped += p.id
                    notes += "${p.id}: no keel project for the repo \"${p.repo ?: "?"}\""
                    continue
                }
                val t = tasks.create(pid, NewTask(title = "${p.id} · ${p.title}".take(200), description = description(i, p), type = "story",
                    externalKey = p.jiraKey))
                store.saveItem(p.copy(taskId = t.id))
                made += t.id
            }
        }
        store.event(i.id, InitiativeService.YOU, "handoff", buildString {
            append("Sent ")
            if (made.isNotEmpty()) append("${made.size} stories to keel Tasks")
            if (made.isNotEmpty() && jiraKeys.isNotEmpty()) append(" and ")
            if (jiraKeys.isNotEmpty()) append("${jiraKeys.size} tickets to Jira")
            if (made.isEmpty() && jiraKeys.isEmpty()) append("nothing (every story was sent before)")
        }, mapOf("tasks" to made, "jira" to jiraKeys, "skipped" to skipped))
        return HandoffResult(made, jiraKeys, skipped, notes)
    }

    /** One Jira epic per plan epic, a story under it, sub-tasks for its tasks, and "Blocks" links for what waits. */
    private fun toJira(i: Initiative, items: List<PlanItem>, notes: MutableList<String>, keys: MutableList<String>): List<PlanItem> {
        val out = mutableListOf<PlanItem>()
        for ((epic, list) in items.groupBy { it.epic }) {
            val pid = list.firstNotNullOfOrNull { it.projectId }
            val client = pid?.let { jira.client(it) }
            if (client == null) {
                notes += "epic $epic: the repo's keel project has no Jira connection"
                continue
            }
            val team = list.first().team?.let { store.team(it) }
            val project = team?.jiraProject ?: jira.settings(pid)?.projectKey
            if (project.isNullOrBlank()) {
                notes += "epic $epic: no Jira project (set it on the team ${team?.name ?: list.first().team ?: "?"})"
                continue
            }
            val epicKey = runCatching { create(client, project, "Epic", "${i.id} · $epic · ${i.title}", "Part of ${i.id}: ${i.idea}", null) }
                .getOrElse { ex ->
                    notes += "epic $epic: Jira refused an Epic (${msg(ex)}), the stories go without one"
                    null
                }
            epicKey?.let { keys += it }
            for (p in list) {
                val made = runCatching { create(client, project, "Story", "${p.id} · ${p.title}", description(i, p), epicKey) }
                    .recoverCatching { create(client, project, "Story", "${p.id} · ${p.title}", description(i, p), null) }
                val key = made.getOrNull()
                if (key == null) {
                    notes += "${p.id}: ${msg(made.exceptionOrNull()!!)}"
                    continue
                }
                keys += key
                for (t in strings(p.body["tasks"])) {
                    runCatching { create(client, project, "Sub-task", t.take(250), "", key) }.onSuccess { keys += it }
                        .onFailure { ex -> notes += "${p.id} sub-task: ${msg(ex)}" }
                }
                val saved = p.copy(jiraKey = key)
                store.saveItem(saved)
                out += saved
            }
            val byStory = out.associate { it.id to it.jiraKey }
            for (p in list) {
                val key = byStory[p.id] ?: continue
                for (dep in strings(p.body["depends_on"])) {
                    val before = byStory[dep] ?: store.items(i.id, p.version).firstOrNull { it.id == dep }?.jiraKey ?: continue
                    runCatching { client.linkIssues("Blocks", key, before) }.onFailure { ex -> notes += "${p.id} waits for $dep: ${msg(ex)}" }
                }
            }
        }
        return out
    }

    private fun create(c: JiraClient, project: String, type: String, summary: String, description: String, parent: String?): String =
        c.createIssue(mapOf("project" to mapOf("key" to project), "issuetype" to mapOf("name" to type), "summary" to summary.take(250),
            "description" to description, "parent" to parent?.let { mapOf("key" to it) }).filterValues { it != null })

    private fun msg(ex: Throwable) = when (ex) {
        is JiraException -> ex.message
        else -> ex.message ?: ex.javaClass.simpleName
    }.take(200)

    /** The task text: the story, its criteria as a keel-criteria block (the flow starts with them) and where it comes from. */
    fun description(i: Initiative, p: PlanItem): String = buildString {
        append("${p.title}\n\nPart of ${i.id}: ${i.title}. ${i.idea}\n")
        p.team?.let { append("Team: $it\n") }
        val deps = strings(p.body["depends_on"])
        if (deps.isNotEmpty()) append("Waits for: ${deps.joinToString()}\n")
        val todo = strings(p.body["tasks"])
        if (todo.isNotEmpty()) append("\nTasks:\n").append(todo.joinToString("\n") { "- $it" }).append("\n")
        val criteria = strings(p.body["criteria"])
        if (criteria.isNotEmpty()) append("\n```keel-criteria\n").append(criteria.joinToString("\n")).append("\n```\n")
    }

    /** After the outcome: a lesson for each team in play goes to its knowledge, as a suggestion they accept or not. */
    fun learn(i: Initiative) {
        val outcome = store.latest(i.id, "outcome")?.text?.take(1500) ?: return
        val inPlay = (store.latest(i.id, "plan")?.let { store.items(i.id, it.version) }.orEmpty().mapNotNull { it.team } +
            i.repos.flatMap { teams.ownersOf(it).map { t -> t.id } }).distinct()
        inPlay.filter { store.team(it) != null }.forEach { tid ->
            runCatching { teams.suggest(tid, "lessons", "### ${i.id} · ${i.title}\n\n$outcome", i.id) }
                .onFailure { log.warn("keel Product could not suggest a lesson to {}: {}", tid, it.message) }
        }
    }

    private fun strings(v: Any?): List<String> = (v as? List<*>).orEmpty().mapNotNull { it?.toString()?.takeIf { s -> s.isNotBlank() } }

    private fun days(v: Any?): List<Double> = when (v) {
        is Number -> listOf(v.toDouble(), v.toDouble())
        is List<*> -> v.mapNotNull { (it as? Number)?.toDouble() }
        else -> emptyList()
    }
}
