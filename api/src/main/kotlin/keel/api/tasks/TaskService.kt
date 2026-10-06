package keel.api.tasks

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.events.EngineEvent
import keel.api.events.EventHub
import keel.api.flow.FlowCap
import keel.api.flow.FlowService
import keel.api.jira.JiraClient
import keel.api.jira.JiraException
import keel.api.jira.JiraService
import keel.api.jira.JiraSettings
import keel.api.notifications.NotificationService
import keel.api.projects.ProjectService
import keel.api.tasks.TaskStatus.IN_PROGRESS
import keel.api.tasks.TaskStatus.IN_REVIEW
import keel.api.tasks.TaskStatus.READY_PROD
import keel.api.tasks.TaskStatus.TESTING_PP
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

/**
 * Tasks: create and edit them, start a flow for one, and walk it through review, PP testing and production. Every change
 * goes through [fire]: the [TaskMachine] plans it, this class writes the status and the history, opens or closes Inbox
 * items, and does what the plan asks outside keel — move and comment the Jira ticket, ask for reviewers on GitHub and in
 * Jira. When Jira cannot be reached (or there is no connection) for a task with a Jira key, an Inbox item asks the user
 * to move the real ticket by hand.
 */
@Service
class TaskService(
    private val store: TaskStore,
    private val jira: JiraService,
    private val flows: FlowService,
    private val projects: ProjectService,
    private val secrets: SecretService,
    private val notifications: NotificationService,
    private val hub: EventHub,
    private val engine: EngineClient,
    private val props: TaskProperties,
    private val mapper: ObjectMapper,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val locks = ConcurrentHashMap<String, Any>()
    private fun lock(id: String) = locks.computeIfAbsent(id) { Any() }

    // ---- reads ---------------------------------------------------------------------------------

    fun view(t: Task, events: Boolean = false) = TaskView(
        t.id, t.projectId, t.title, t.description, t.type, t.status, t.source, t.externalKey, t.externalUrl, t.externalStatus,
        t.assignee, t.priority, t.threadId, t.workflowId, t.prUrl, t.reviewers, t.blockedReason, t.createdAt, t.updatedAt,
        flow = t.threadId?.let { store.flow(it) }, waiting = store.openItems(t.id), events = if (events) store.events(t.id) else null,
    )

    fun list(pid: String, source: String? = null): TaskList {
        projects.require(pid)
        val s = jira.settings(pid)
        val last = jira.lastSync(pid)
        val tasks = store.list(pid).filter { source.isNullOrBlank() || it.source == source }.map { view(it) }
        return TaskList(tasks, SyncInfo(s != null, s?.kind, last?.first, last?.second, jira.me(pid)?.name, s?.pollMinutes))
    }

    fun get(id: String): TaskView = view(store.require(id), events = true)

    // ---- create / edit -------------------------------------------------------------------------

    fun create(pid: String, b: NewTask): TaskView {
        projects.require(pid)
        val title = b.title.trim()
        if (title.isEmpty()) throw BadRequest("Give the task a title", "One short line: what should change?")
        if (title.length > 300) throw BadRequest("The title is too long (300 characters at most)")
        val type = b.type?.trim()?.lowercase()?.ifEmpty { null } ?: "task"
        if (type !in TaskTypes.ALL) throw BadRequest("type must be bug, story or task")
        val key = key(b.externalKey)
        if (key != null && store.byKey(pid, key) != null) throw Conflict("A task for $key already exists", "Open it on the Tasks page.")
        val now = Time.now()
        val t = store.insert(Task(
            id = TaskStore.newId(), projectId = pid, title = title, description = b.description?.trim()?.take(20_000).orEmpty(), type = type,
            status = TaskStatus.TODO, source = if (key != null) "jira" else "local", externalKey = key,
            externalUrl = url(b.externalUrl) ?: key?.let { browse(pid, it) }, externalStatus = null,
            assignee = b.assignee?.trim()?.ifEmpty { null }, priority = b.priority?.trim()?.ifEmpty { null }, threadId = null, workflowId = null,
            prUrl = null, reviewers = logins(b.reviewers).map { Reviewer(it, "github", "wanted") }, blockedReason = null, createdAt = now, updatedAt = now,
        ))
        store.event(t.id, "created", null, t.status, "Created${key?.let { " for $it" } ?: " (local task)"}.", "user")
        changed(pid)
        return view(t, events = true)
    }

    fun update(id: String, b: TaskPatch): TaskView = synchronized(lock(id)) {
        val t = store.require(id)
        val what = mutableListOf<String>()
        var n = t
        b.title?.trim()?.let {
            if (it.isEmpty()) throw BadRequest("The title cannot be empty")
            if (it != t.title) { n = n.copy(title = it.take(300)); what += "title" }
        }
        b.description?.let { if (it.trim() != t.description) { n = n.copy(description = it.trim().take(20_000)); what += "description" } }
        b.type?.trim()?.lowercase()?.let {
            if (it !in TaskTypes.ALL) throw BadRequest("type must be bug, story or task")
            if (it != t.type) { n = n.copy(type = it); what += "type" }
        }
        if (b.externalKey != null) {
            val key = key(b.externalKey)
            if (key != t.externalKey) {
                if (key != null && store.byKey(t.projectId, key)?.let { it.id != t.id } == true) throw Conflict("A task for $key already exists")
                n = n.copy(externalKey = key, source = if (key != null) "jira" else "local", externalUrl = key?.let { browse(t.projectId, it) }, externalStatus = null)
                what += "Jira key"
            }
        }
        b.externalUrl?.let { if (url(it) != n.externalUrl) { n = n.copy(externalUrl = url(it)); what += "link" } }
        b.assignee?.let { if (it.trim().ifEmpty { null } != t.assignee) { n = n.copy(assignee = it.trim().ifEmpty { null }); what += "assignee" } }
        b.priority?.let { if (it.trim().ifEmpty { null } != t.priority) { n = n.copy(priority = it.trim().ifEmpty { null }); what += "priority" } }
        b.reviewers?.let { list ->
            val wanted = logins(list)
            val now = t.reviewers.filter { it.on == "github" }.map { it.login }
            if (wanted != now) {
                n = n.copy(reviewers = t.reviewers.filter { it.on != "github" } + wanted.map { w -> t.reviewers.firstOrNull { it.on == "github" && it.login == w } ?: Reviewer(w, "github", "wanted") })
                what += "reviewers"
            }
        }
        if (what.isEmpty()) return view(t, events = true)
        n = store.update(n)
        store.event(id, "updated", null, null, "Changed: ${what.joinToString()}.", "user")
        changed(t.projectId)
        view(n, events = true)
    }

    fun delete(id: String) {
        val t = store.require(id)
        store.delete(id)
        changed(t.projectId)
    }

    // ---- the lifecycle: actions ----------------------------------------------------------------

    /** Start: a flow for the task (its type picks the workflow unless the user picked one). Never starts by itself. */
    fun start(id: String, b: StartTask): TaskView {
        val t = store.require(id)
        val wid = b.workflowId?.trim()?.ifEmpty { null } ?: TaskTypes.defaultWorkflow(t.type)
        try {
            TaskMachine.plan(t, Trigger.Start(wid, "", null))
        } catch (e: IllegalMove) {
            throw Conflict(e.message, e.hint)
        }
        val running = t.threadId?.let { store.flow(it) }?.takeIf { it.status in setOf("running", "waiting") }
        if (running != null) throw Conflict("A flow already runs for ${t.label}", "Open it on the Flow page, or stop it first.")
        val title = (t.externalKey?.let { "$it: " } ?: "") + t.title
        val request = buildString {
            append(t.title)
            if (t.description.isNotBlank()) append("\n\n").append(t.description)
            if (t.externalKey != null) append("\n\nJira ticket: ").append(t.externalKey).append(t.externalUrl?.let { " ($it)" } ?: "")
        }
        val state = try {
            flows.start(t.projectId, wid, title.take(200), null, FlowCap(runMode = b.runMode?.trim()?.ifEmpty { null }),
                allowFake = b.allowFake, allowDirty = b.allowDirty, request = request)
        } catch (e: Conflict) {
            // A used-up cap, uncommitted files, the fake model: the task stays where it is and its history says why.
            store.event(id, "start_refused", t.status, t.status, "The $wid flow did not start: ${e.message}${e.hint?.let { " $it" } ?: ""}", "keel")
            changed(t.projectId)
            throw e
        }
        val tid = state.get("thread_id")?.asText()?.takeIf { it.isNotBlank() } ?: throw Conflict("The engine did not say which flow it started")
        return view(fire(id, Trigger.Start(wid, tid, "Follow it in keel: ${link(t)}")), events = true)
    }

    fun confirm(id: String, b: ConfirmTask): TaskView {
        if (b.stage !in setOf("pp", "prod")) throw BadRequest("stage must be pp or prod")
        return view(fire(id, Trigger.Confirm(b.stage, b.note)), events = true)
    }

    /** A move by hand; back to In progress from review, PP or ready is a send-back and needs a reason. */
    fun move(id: String, b: MoveTask): TaskView {
        val t = store.require(id)
        val to = b.to.trim()
        val trigger = if (to == IN_PROGRESS && t.status in setOf(IN_REVIEW, TESTING_PP, READY_PROD)) {
            val note = b.note?.trim().orEmpty()
            if (note.isEmpty()) throw BadRequest("Say why it goes back", "Your words go into the task's history and the Jira comment.")
            Trigger.SendBack(note)
        } else Trigger.Move(to, b.note)
        return view(fire(id, trigger), events = true)
    }

    /** The user pastes the PR link (keel built the PR body, but opened no PR). */
    fun setPr(id: String, url: String): TaskView {
        val u = url.trim()
        if (!Regex("^https?://\\S+$").matches(u)) throw BadRequest("Paste the pull request's link", "Like https://github.com/acme/app/pull/12")
        return view(fire(id, Trigger.PrOpened(u, "user")), events = true)
    }

    /** An Inbox button on a task item: confirm / send_back (kind task), done (kind jira-manual). */
    fun act(itemId: Long, b: ItemAct): TaskView {
        val item = store.item(itemId) ?: throw NotFound("No inbox item $itemId")
        if (item.doneAt != null) throw Conflict("This was answered already", "Reload the inbox.")
        return when (item.kind) {
            "task" -> when (b.action) {
                "confirm" -> confirm(item.taskId, ConfirmTask(item.stage ?: "", b.note))
                "send_back" -> move(item.taskId, MoveTask(IN_PROGRESS, b.note))
                else -> throw BadRequest("action must be confirm or send_back")
            }
            "jira-manual" -> {
                if (b.action != "done") throw BadRequest("action must be done")
                synchronized(lock(item.taskId)) {
                    val t = store.require(item.taskId)
                    store.closeItem(item.id)
                    if (item.stage == "reviewers") {
                        store.event(t.id, "jira_manual", null, null, "You set the reviewers of ${t.label} in Jira by hand.", "user")
                    } else {
                        store.update(t.copy(externalStatus = item.stage))
                        store.event(t.id, "jira_manual", null, null, "You moved ${t.label} to ${item.stage} in Jira by hand.", "user")
                    }
                    changed(t.projectId)
                }
                get(item.taskId)
            }
            else -> throw BadRequest("Unknown item kind ${item.kind}")
        }
    }

    // ---- the lifecycle: engine events and PR reviews ------------------------------------------

    /** An engine event of a thread a task follows (TaskEngineEvents calls this, on its own worker). */
    fun onEngineEvent(e: EngineEvent) {
        when (e.type) {
            "thread.started" -> {
                val parent = (e.data["parent"] as? Map<*, *>)?.get("thread_id")?.toString() ?: return
                val t = store.byThread(parent) ?: return
                synchronized(lock(t.id)) {
                    val wf = (e.data["workflow_id"] ?: e.data["workflow"])?.toString()
                    store.update(t.copy(threadId = e.threadId, workflowId = wf ?: t.workflowId))
                    store.event(t.id, "flow", null, null, "The ${t.workflowId ?: "flow"} flow handed the work to the ${wf ?: "next"} flow.", "keel")
                }
                changed(t.projectId)
            }
            "step.finished" -> {
                val url = PR_OPENED.find(e.data["note"]?.toString() ?: return)?.groupValues?.get(1) ?: return
                val t = store.byThread(e.threadId) ?: return
                quietly(t) { fire(t.id, Trigger.PrOpened(url, "keel")) }
            }
            "thread.done" -> {
                val t = store.byThread(e.threadId) ?: return
                if (e.data["status"]?.toString() == "stopped") {
                    quietly(t) { fire(t.id, Trigger.FlowEnded(false, "stopped")) }
                    return
                }
                val pr = if (t.status == IN_PROGRESS && t.prUrl == null) {
                    runCatching { engine.thread(e.threadId).get("pr_url")?.takeIf { !it.isNull }?.asText() }.getOrNull()
                } else null
                quietly(t) { fire(t.id, if (pr != null) Trigger.PrOpened(pr, "keel") else Trigger.FlowDone()) }
            }
            "thread.failed" -> {
                val t = store.byThread(e.threadId) ?: return
                val stopped = e.data["status"]?.toString() == "stopped"
                val why = e.data["error"]?.toString()?.take(300)?.ifBlank { null } ?: if (stopped) "stopped" else "a step failed"
                quietly(t) { fire(t.id, Trigger.FlowEnded(!stopped, why)) }
            }
        }
    }

    /**
     * Reads the reviews of every task in review (of [pid], or all): approved (and nobody asks for changes) → Testing (PP).
     * Returns (PRs read, tasks moved). Needs a GitHub token; without one the user moves the task by hand.
     */
    fun checkReviews(pid: String? = null): Pair<Int, Int> {
        val token = githubToken() ?: return 0 to 0
        val gh = GitHubClient(token, mapper, props.githubApi)
        var checked = 0
        var moved = 0
        for (t in store.inReview().filter { pid == null || it.projectId == pid }) {
            val pr = PrRef.parse(t.prUrl) ?: continue
            checked++
            val r = try {
                gh.reviews(pr)
            } catch (e: GitHubException) {
                once(t, "github_error", "GitHub: ${e.message}")
                continue
            }
            synchronized(lock(t.id)) {
                val cur = store.require(t.id)
                val states = r.commented.associateWith { "commented" } + r.approved.associateWith { "approved" } + r.changes.associateWith { "changes_requested" }
                val others = cur.reviewers.filter { it.on == "github" && it.login !in states }
                val next = cur.reviewers.filter { it.on != "github" } + others + states.map { (who, st) -> Reviewer(who, "github", st) }
                if (next.toSet() != cur.reviewers.toSet()) store.update(cur.copy(reviewers = next))
                if (r.changes.isNotEmpty()) once(cur, "review", "Changes requested by ${r.changes.joinToString()}.")
            }
            if (r.approved.isNotEmpty() && r.changes.isEmpty()) {
                quietly(t) { fire(t.id, Trigger.Approved(r.approved)) }
                moved++
            }
        }
        return checked to moved
    }

    // ---- the one place that changes a task's status -------------------------------------------

    /** Plans [tr] with the [TaskMachine] and applies it: status, history, Inbox items, then Jira and GitHub. */
    fun fire(id: String, tr: Trigger): Task = synchronized(lock(id)) {
        val t = store.require(id)
        val plan = try {
            TaskMachine.plan(t, tr)
        } catch (e: IllegalMove) {
            throw Conflict(e.message, e.hint)
        }
        var next = t.copy(
            status = plan.to,
            prUrl = plan.prUrl ?: t.prUrl,
            blockedReason = if (plan.to == TaskStatus.BLOCKED) plan.blockedReason ?: t.blockedReason else null,
        )
        if (tr is Trigger.Start) next = next.copy(threadId = tr.threadId, workflowId = tr.workflowId)
        if (tr is Trigger.JiraMoved) next = next.copy(externalStatus = tr.to)
        next = store.update(next)
        store.event(id, plan.kind, t.status, plan.to, plan.note, plan.actor)
        if (plan.to != t.status) store.closeItems(id, kind = "task")
        plan.openItem?.let { openItem(next, "task", it.stage, it.title, it.detail) }
        if (plan.stopFlow) stopFlow(next)
        if (plan.actor != "jira") next = effects(next, plan)
        changed(next.projectId)
        next
    }

    private fun effects(start: Task, plan: Plan): Task {
        var t = start
        val key = t.externalKey
        val conn = jira.settings(t.projectId)
        val client = if (key != null) jira.client(t.projectId) else null
        if (plan.moveJira && key != null) {
            val target = TaskMachine.jiraTarget(plan.to, conn?.statusMap.orEmpty())
            if (target != null) {
                if (client == null) {
                    byHand(t, target, "no Jira connection for this project")
                } else try {
                    val m = client.transitionTo(key, target)
                    store.event(t.id, "jira", null, null, if (m.moved) "Jira: moved $key from ${m.from ?: "?"} to ${m.to}." else "Jira: $key is already in ${m.to}.", "keel")
                    t = store.update(t.copy(externalStatus = m.to))
                } catch (e: JiraException) {
                    store.event(t.id, "jira_error", null, null, "Jira: could not move $key to $target. ${e.message}", "keel")
                    byHand(t, target, e.message)
                }
            }
        }
        if (plan.comment != null && key != null && client != null) {
            try {
                client.comment(key, plan.comment)
            } catch (e: JiraException) {
                store.event(t.id, "jira_error", null, null, "Jira: the comment was not added. ${e.message}", "keel")
            }
        }
        if (plan.askReviewers) t = askReviewers(t, conn, client)
        return t
    }

    /** Asks the GitHub reviewers on the PR (only with a token) and fills the Jira reviewer field. */
    private fun askReviewers(start: Task, conn: JiraSettings?, client: JiraClient?): Task {
        var t = start
        val wanted = t.reviewers.filter { it.on == "github" }.map { it.login }.ifEmpty { conn?.githubReviewers.orEmpty() }
        if (wanted.isNotEmpty()) {
            val pr = PrRef.parse(t.prUrl)
            val token = githubToken()
            when {
                pr == null -> store.event(t.id, "github_error", null, null, "The PR link is not a GitHub pull request: reviewers were not asked.", "keel")
                token == null -> store.event(t.id, "github", null, null,
                    "Reviewers were not asked on GitHub: keel has no GitHub token (Control › Connections). Ask ${wanted.joinToString()} on the PR yourself.", "keel")
                else -> try {
                    GitHubClient(token, mapper, props.githubApi).requestReviewers(pr, wanted)
                    store.event(t.id, "reviewers", null, null, "Asked ${wanted.joinToString()} to review the PR on GitHub.", "keel")
                    val others = t.reviewers.filter { !(it.on == "github" && it.login in wanted) }
                    t = store.update(t.copy(reviewers = others + wanted.map { Reviewer(it, "github", "requested") }))
                } catch (e: GitHubException) {
                    store.event(t.id, "github_error", null, null, "GitHub: ${e.message}", "keel")
                }
            }
        }
        val key = t.externalKey
        val field = conn?.reviewerField
        val people = conn?.jiraReviewers.orEmpty()
        if (key != null && field != null && people.isNotEmpty()) {
            val why = if (client == null) "no Jira connection for this project" else try {
                client.setUsers(key, field, people)
                store.event(t.id, "reviewers", null, null, "Jira: set the reviewers of $key to ${people.joinToString()}.", "keel")
                val others = t.reviewers.filter { it.on != "jira" }
                t = store.update(t.copy(reviewers = others + people.map { Reviewer(it, "jira", "set") }))
                null
            } catch (e: JiraException) {
                store.event(t.id, "jira_error", null, null, "Jira: could not set the reviewers of $key. ${e.message}", "keel")
                e.message
            }
            if (why != null) {
                openItem(t, "jira-manual", "reviewers", "Set the reviewers of $key in Jira to ${people.joinToString()} (keel could not: ${why.trimEnd('.')})",
                    "The PR is ${t.prUrl ?: "open"}. Set the field by hand, then press Done.")
            }
        }
        return t
    }

    /** keel could not move the real ticket: the Inbox asks the user to do it (an older such item is replaced). */
    private fun byHand(t: Task, target: String, why: String) {
        store.openItems(t.id).filter { it.kind == "jira-manual" && it.stage != "reviewers" }.forEach { store.closeItem(it.id) }
        openItem(t, "jira-manual", target, "Move ${t.externalKey} to $target in Jira (keel could not: ${why.trimEnd('.')})",
            "keel moved the task to ${TaskStatus.LABELS[t.status] ?: t.status}. Move the real ticket by hand, then press Done.")
        store.event(t.id, "jira_manual", null, null, "Asked you to move ${t.externalKey} to $target in Jira by hand.", "keel")
    }

    private fun openItem(t: Task, kind: String, stage: String?, title: String, detail: String): TaskItem {
        val item = store.openItem(t, kind, stage, title, detail)
        runCatching { notifications.create("review", t.projectId, title, detail.take(200), "/tasks/${t.id}") }
        return item
    }

    private fun stopFlow(t: Task) {
        val tid = t.threadId ?: return
        if (store.flow(tid)?.status !in setOf("running", "waiting")) return
        runCatching { flows.stop(tid) }
            .onSuccess { store.event(t.id, "flow", null, null, "Stopped its flow.", "keel") }
            .onFailure { store.event(t.id, "flow", null, null, "Could not stop its flow: ${it.message}", "keel") }
    }

    /** Engine events come from the engine, not a person: a refused trigger is history, not an error. */
    private fun quietly(t: Task, block: () -> Unit) {
        try {
            block()
        } catch (e: Conflict) {
            store.event(t.id, "note", null, null, e.message, "keel")
        } catch (e: Exception) {
            log.warn("task {}: {}", t.id, e.message)
        }
    }

    /** Writes a note unless the last event already says it (the review poll runs every few minutes). */
    private fun once(t: Task, kind: String, note: String) {
        if (store.events(t.id).lastOrNull()?.note != note) store.event(t.id, kind, null, null, note, "keel")
    }

    fun githubToken(): String? =
        secrets.get("GITHUB_TOKEN") ?: secrets.get("GH_TOKEN")
            ?: (if (props.githubFromEnv) (System.getenv("GITHUB_TOKEN") ?: System.getenv("GH_TOKEN"))?.takeIf { it.isNotBlank() } else null)

    private fun changed(pid: String) = hub.publish(pid, "project.changed", mapOf("id" to pid))

    private fun link(t: Task) = "${props.publicUrl.trim().trimEnd('/').ifBlank { "http://127.0.0.1:8080" }}/#/tasks/${t.id}"

    private fun browse(pid: String, key: String) = jira.settings(pid)?.baseUrl?.let { "$it/browse/$key" }

    private fun key(raw: String?): String? {
        val k = raw?.trim()?.uppercase()?.ifEmpty { null } ?: return null
        if (!Regex("^[A-Z][A-Z0-9_]{0,29}-\\d{1,9}$").matches(k)) throw BadRequest("\"$raw\" is not a Jira key", "Like ABC-123.")
        return k
    }

    private fun url(raw: String?): String? {
        val u = raw?.trim()?.ifEmpty { null } ?: return null
        if (!Regex("^https?://\\S+$").matches(u)) throw BadRequest("The link must start with http:// or https://")
        return u
    }

    private fun logins(list: List<String>?): List<String> {
        val out = list.orEmpty().map { it.trim().removePrefix("@") }.filter { it.isNotEmpty() }.distinct()
        out.firstOrNull { !Regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(/[A-Za-z0-9._-]+)?$").matches(it) }
            ?.let { throw BadRequest("\"$it\" is not a GitHub login") }
        return out
    }

    companion object {
        val PR_OPENED = Regex("^PR opened: (https?://\\S+)")
    }
}
