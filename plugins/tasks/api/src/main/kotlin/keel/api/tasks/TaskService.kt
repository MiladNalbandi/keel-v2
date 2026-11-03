package keel.api.tasks

import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.events.EngineEvent
import keel.api.events.EventHub
import keel.api.flow.Ac
import keel.api.flow.FlowCap
import keel.api.flow.FlowService
import keel.api.notifications.NotificationService
import keel.api.projects.ProjectService
import keel.api.tasks.TaskStatus.IN_PROGRESS
import keel.api.tasks.TaskStatus.IN_REVIEW
import keel.api.tasks.TaskStatus.READY_PROD
import keel.api.tasks.TaskStatus.TESTING_PP
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Service
import java.util.concurrent.ConcurrentHashMap

/**
 * Tasks: create and edit them, start a flow for one, and walk it through review, PP testing and production. Every change
 * goes through [fire]: the [TaskMachine] plans it, this class writes the status and the history, opens or closes Inbox
 * items, and does what the plan asks outside keel — move and comment the real ticket, ask for reviewers on GitHub and in
 * Jira. When the tracker cannot be reached (or there is no connection) for a task with a ticket key, an Inbox item asks
 * the user to move the real ticket by hand. The trackers are plugins (Jira, GitHub Issues): each is a [TicketTracker].
 */
@Service
class TaskService(
    private val store: TaskStore,
    private val trackers: ObjectProvider<TicketTracker>,
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
    private val effectLocks = ConcurrentHashMap<String, Any>()
    private fun effectLock(id: String) = effectLocks.computeIfAbsent(id) { Any() }

    /** The tracker [pid] is connected to (Jira, GitHub Issues), or null. */
    private fun tracker(pid: String): TicketTracker? = trackers.forProject(pid)

    /** The tracker of [t]'s ticket: the one its source names, else the project's. */
    private fun tracker(t: Task): TicketTracker? = trackers.bySource(t.source) ?: tracker(t.projectId)

    /** The tracker's name for what keel writes about [t]'s ticket: the tracker's, else by the task's source. */
    private fun named(tr: TicketTracker?, t: Task) = tr?.title ?: if (t.source == "github") "GitHub" else "Jira"

    // ---- reads ---------------------------------------------------------------------------------

    fun view(t: Task, events: Boolean = false) = TaskView(
        t.id, t.projectId, t.title, t.description, t.type, t.status, t.source, t.externalKey, t.externalUrl, t.externalStatus,
        t.assignee, t.priority, t.threadId, t.workflowId, t.prUrl, t.reviewers, t.blockedReason, t.createdAt, t.updatedAt,
        flow = t.threadId?.let { store.flow(it) }, waiting = store.openItems(t.id), events = if (events) store.events(t.id) else null,
    )

    fun list(pid: String, source: String? = null): TaskList {
        projects.require(pid)
        val tasks = store.list(pid).filter { source.isNullOrBlank() || it.source == source }.map { view(it) }
        return TaskList(tasks, tracker(pid)?.syncInfo(pid) ?: SyncInfo.NONE)
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
        val (key, tr) = key(pid, b.externalKey)
        if (key != null && store.byKey(pid, key) != null) throw Conflict("A task for $key already exists", "Open it on the Tasks page.")
        val now = Time.now()
        val t = store.insert(Task(
            id = TaskStore.newId(), projectId = pid, title = title, description = b.description?.trim()?.take(20_000).orEmpty(), type = type,
            status = TaskStatus.TODO, source = if (key != null) tr?.source ?: "jira" else "local", externalKey = key,
            externalUrl = url(b.externalUrl) ?: key?.let { tr?.ticketUrl(pid, it) }, externalStatus = null,
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
            val (key, tr) = key(t.projectId, b.externalKey)
            if (key != t.externalKey) {
                if (key != null && store.byKey(t.projectId, key)?.let { it.id != t.id } == true) throw Conflict("A task for $key already exists")
                n = n.copy(externalKey = key, source = if (key != null) tr?.source ?: "jira" else "local",
                    externalUrl = key?.let { tr?.ticketUrl(t.projectId, it) }, externalStatus = null)
                what += "ticket key"
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
        closeItems(id)                // its open Inbox items' notifications leave the unread count too
        store.delete(id)
        changed(t.projectId)
    }

    // ---- the lifecycle: actions ----------------------------------------------------------------

    /** Start: a flow for the task (its type picks the workflow unless the user picked one). Never starts by itself. */
    fun start(id: String, b: StartTask): TaskView = start(id, b, "user")

    /** [actor] keel: an automation rule started it (the history and the Jira comment say so). */
    fun start(id: String, b: StartTask, actor: String): TaskView {
        val t = store.require(id)
        val wid = b.workflowId?.trim()?.ifEmpty { null } ?: TaskTypes.defaultWorkflow(t.type)
        try {
            TaskMachine.plan(t, Trigger.Start(wid, "", null))
        } catch (e: IllegalMove) {
            throw Conflict(e.message, e.hint)
        }
        val running = t.threadId?.let { store.flow(it) }?.takeIf { it.status in setOf("running", "waiting") }
        if (running != null) throw Conflict("A flow already runs for ${t.label}", "Open it on the Flow page, or stop it first.")
        val title = (t.externalKey?.let { "$it: " } ?: "") + (b.title?.trim()?.ifEmpty { null } ?: t.title)
        val tr = tracker(t)
        val request = buildString {
            append(b.request?.trim()?.ifEmpty { null } ?: ticketText(t))
            if (t.externalKey != null) append("\n\n${named(tr, t)} ticket: ").append(t.externalKey).append(t.externalUrl?.let { " ($it)" } ?: "")
        }
        // 0.22.0 the line the PR carries for the ticket (GitHub: "Closes #12", so merging the PR closes the issue)
        val prLine = t.externalKey?.let { k -> runCatching { tr?.prLine(t.projectId, k) }.getOrNull() }
        val state = try {
            flows.start(t.projectId, wid, title.take(200), criteria(t.description),
                FlowCap(capTokens = b.capTokens, runMode = b.runMode?.trim()?.ifEmpty { null }),
                allowFake = b.allowFake, allowDirty = b.allowDirty, request = request, branch = "new",
                // the engine names the ticket in every commit of the flow (data.ticket), so the ticket scan links them
                options = t.externalKey?.let { k -> mapOf("ticket" to k) + (prLine?.let { mapOf("pr_line" to it) } ?: emptyMap()) }, force = b.force)
        } catch (e: ApiException) {
            // A used-up cap, uncommitted files, the fake model, an unknown workflow: the task stays where it is and its history says why.
            store.event(id, "start_refused", t.status, t.status, "The $wid flow did not start: ${e.message}${e.hint?.let { " $it" } ?: ""}", "keel")
            changed(t.projectId)
            throw e
        }
        val tid = state.get("thread_id")?.asText()?.takeIf { it.isNotBlank() } ?: throw Conflict("The engine did not say which flow it started")
        // a flow runs now: keel's open proposal to run one is answered (it no longer waits in the Inbox)
        closeItems(id, kind = TaskAutomations.PROPOSAL, stage = "flow")
        return view(fire(id, Trigger.Start(wid, tid, "Follow it in keel: ${link(t)}", actor)), events = true)
    }

    /** What a flow is asked when nobody wrote it: the task's title and description as they are now. */
    fun ticketText(t: Task): String = if (t.description.isBlank()) t.title else "${t.title}\n\n${t.description}"

    /**
     * v0.13.0: acceptance criteria written in a task's description, in a fenced block, go to the flow as its criteria;
     * the feature flow then skips its interview. keel Product writes them; a person or a Jira ticket can too.
     *
     *     ```keel-criteria
     *     AC-1 [API] GET /prices returns the currency
     *     AC-2 [WEB] An EU visitor sees the price in euro
     *     ```
     */
    fun criteria(description: String): List<Ac>? {
        val block = CRITERIA.find(description)?.groupValues?.get(1) ?: return null
        return block.lines().mapNotNull { line ->
            CRITERION.matchEntire(line.trim())?.let { m -> Ac(id = m.groupValues[1], layer = m.groupValues[2].uppercase(), title = m.groupValues[3].trim()) }
        }.distinctBy { it.id }.takeIf { it.isNotEmpty() }
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
            "task" -> if (item.stage == TaskMachine.PR_STAGE) when (b.action) {
                // B11 the flow finished without a PR: paste its link (the task goes to review), or put the ask aside
                "pr" -> setPr(item.taskId, b.note?.trim().orEmpty())
                "later" -> {
                    closeItem(item.id)
                    store.event(item.taskId, "flow", null, null, "Not now: you put aside opening the pull request.", "user")
                    changed(item.projectId)
                    get(item.taskId)
                }
                else -> throw BadRequest("action must be pr or later")
            } else when (b.action) {
                "confirm" -> confirm(item.taskId, ConfirmTask(item.stage ?: "", b.note))
                "send_back" -> move(item.taskId, MoveTask(IN_PROGRESS, b.note))
                else -> throw BadRequest("action must be confirm or send_back")
            }
            "jira-manual" -> {
                if (b.action != "done") throw BadRequest("action must be done")
                synchronized(lock(item.taskId)) {
                    val t = store.require(item.taskId)
                    closeItem(item.id)
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
                    val cur = store.require(t.id)               // read inside the lock: an edit meanwhile is not overwritten
                    val wf = (e.data["workflow_id"] ?: e.data["workflow"])?.toString()
                    store.update(cur.copy(threadId = e.threadId, workflowId = wf ?: cur.workflowId))
                    store.event(t.id, "flow", null, null, "The ${cur.workflowId ?: "flow"} flow handed the work to the ${wf ?: "next"} flow.", "keel")
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
                val why = if (pr == null && t.status == IN_PROGRESS) runCatching { store.noPrReason(e.threadId) }.getOrNull() else null
                quietly(t) { fire(t.id, if (pr != null) Trigger.PrOpened(pr, "keel") else Trigger.FlowDone(why = why)) }
            }
            "thread.failed" -> {
                val t = store.byThread(e.threadId) ?: return
                val stopped = e.data["status"]?.toString() == "stopped"
                val why = e.data["error"]?.toString()?.take(300)?.ifBlank { null } ?: if (stopped) "stopped" else "a step failed"
                quietly(t) { fire(t.id, Trigger.FlowEnded(!stopped, why)) }
            }
        }
    }

    /** "Sync now": the tracker's tickets (when the project is connected), then the PR reviews of its tasks in review. */
    fun syncNow(pid: String): SyncResult {
        projects.require(pid)
        val r = tracker(pid)?.sync(pid) ?: SyncResult(ok = true, jira = false, at = Time.now())
        val (checked, moved) = checkReviews(pid)
        return r.copy(reviewsChecked = checked, reviewsMoved = moved)
    }

    /**
     * Reads the reviews of every task in review (of [pid], or all): approved (and nobody asks for changes) → Testing (PP).
     * Returns (PRs read, tasks moved). Needs a GitHub token; without one the user moves the task by hand.
     */
    fun checkReviews(pid: String? = null): Pair<Int, Int> {
        val token = githubToken() ?: return 0 to 0
        val gh = GitHubClient(token, mapper, props.githubApi, secrets.githubHost())
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
    fun fire(id: String, tr: Trigger): Task {
        val (next, plan) = synchronized(lock(id)) { applyPlan(id, tr) }
        // Jira, GitHub and the engine are reached outside the task's lock: a slow answer must not block an edit of the
        // task. Their own lock keeps one task's outside steps in order.
        synchronized(effectLock(id)) {
            if (plan.stopFlow) stopFlow(next)
            // a move the tracker made (Jira, GitHub) is never sent back to it
            if (tr !is Trigger.JiraMoved) effects(next, plan)
        }
        changed(next.projectId)
        return try { store.require(id) } catch (e: ApiException) { next }
    }

    /** The part of [fire] that writes keel's own state: status, history and Inbox items. Runs under the task's lock. */
    private fun applyPlan(id: String, tr: Trigger): Pair<Task, Plan> {
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
        if (plan.to != t.status) closeItems(id, kind = "task")
        plan.openItem?.let {
            closeItems(id, kind = "task", stage = it.stage)          // one per stage: a second flow's ask replaces the first's
            openItem(next, "task", it.stage, it.title, it.detail)
        }
        return next to plan
    }

    /** Changes the task as it is now (read under its lock), so a change made while Jira or GitHub answered is kept. */
    private fun patch(id: String, f: (Task) -> Task): Task = synchronized(lock(id)) { store.update(f(store.require(id))) }

    private fun effects(start: Task, plan: Plan): Task {
        var t = start
        val key = t.externalKey
        val tracker = tracker(t)
        val name = named(tracker, t)
        val conn = tracker?.connection(t.projectId)
        val client = if (key != null) tracker?.client(t.projectId) else null
        if (plan.moveJira && key != null) {
            val target = TaskMachine.jiraTarget(plan.to, conn?.statusMap.orEmpty())
            if (target != null) {
                if (client == null) {
                    byHand(t, target, "no $name connection for this project", name)
                } else try {
                    val m = client.transitionTo(key, target)
                    store.event(t.id, "jira", null, null, if (m.moved) "$name: moved $key from ${m.from ?: "?"} to ${m.to}." else "$name: $key is already in ${m.to}.", "keel")
                    t = patch(t.id) { it.copy(externalStatus = m.to) }
                    // keel reached the tracker this time: an older "move it by hand" is out of date
                    store.openItems(t.id).filter { it.kind == "jira-manual" && it.stage != "reviewers" }.forEach { closeItem(it.id) }
                } catch (e: TicketException) {
                    store.event(t.id, "jira_error", null, null, "$name: could not move $key to $target. ${e.message}", "keel")
                    byHand(t, target, e.message, name)
                }
            }
        }
        if (plan.comment != null && key != null && client != null) {
            try {
                client.comment(key, plan.comment)
            } catch (e: TicketException) {
                store.event(t.id, "jira_error", null, null, "$name: the comment was not added. ${e.message}", "keel")
            }
        }
        if (plan.askReviewers) t = askReviewers(t, conn, client)
        return t
    }

    /** Asks the GitHub reviewers on the PR (only with a token) and fills the Jira reviewer field. */
    private fun askReviewers(start: Task, conn: TrackerConnection?, client: TicketClient?): Task {
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
                    GitHubClient(token, mapper, props.githubApi, secrets.githubHost()).requestReviewers(pr, wanted)
                    store.event(t.id, "reviewers", null, null, "Asked ${wanted.joinToString()} to review the PR on GitHub.", "keel")
                    t = patch(t.id) { c ->
                        c.copy(reviewers = c.reviewers.filter { !(it.on == "github" && it.login in wanted) } + wanted.map { Reviewer(it, "github", "requested") })
                    }
                } catch (e: GitHubException) {
                    store.event(t.id, "github_error", null, null, "GitHub: ${e.message}", "keel")
                }
            }
        }
        val key = t.externalKey
        val field = conn?.reviewerField
        val people = conn?.reviewers.orEmpty()
        if (key != null && field != null && people.isNotEmpty()) {
            val why = if (client == null) "no Jira connection for this project" else try {
                client.setUsers(key, field, people)
                store.event(t.id, "reviewers", null, null, "Jira: set the reviewers of $key to ${people.joinToString()}.", "keel")
                t = patch(t.id) { c -> c.copy(reviewers = c.reviewers.filter { it.on != "jira" } + people.map { Reviewer(it, "jira", "set") }) }
                null
            } catch (e: TicketException) {
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
    private fun byHand(t: Task, target: String, why: String, name: String) {
        store.openItems(t.id).filter { it.kind == "jira-manual" && it.stage != "reviewers" }.forEach { closeItem(it.id) }
        openItem(t, "jira-manual", target, "Move ${t.externalKey} to $target in $name (keel could not: ${why.trimEnd('.')})",
            "keel moved the task to ${TaskStatus.LABELS[t.status] ?: t.status}. Move the real ticket by hand, then press Done.")
        store.event(t.id, "jira_manual", null, null, "Asked you to move ${t.externalKey} to $target in $name by hand.", "keel")
    }

    /** Opens an Inbox item and its notification (the phone, Muxy and the IDE follow the Inbox's notifications). */
    fun openItem(t: Task, kind: String, stage: String?, title: String, detail: String, data: Map<String, Any?>? = null): TaskItem {
        val item = store.openItem(t, kind, stage, title, detail, data)
        runCatching { notifications.create("review", t.projectId, title, detail, "/tasks/${t.id}", threadId = itemRef(item.id)) }
        return item
    }

    /**
     * Closes one Inbox item; its bell notification is done and read (the unread count goes down), like a decided gate's.
     * Every task item (proposal, review comment, confirm, Jira by hand) closes through here or [closeItems].
     */
    fun closeItem(id: Long): Boolean {
        val closed = store.closeItem(id)
        if (closed) runCatching {
            notifications.markDone(itemRef(id))
            // B3 a notification from before items had their own (0.18.54 and older) goes once its task waits for nothing
            store.item(id)?.taskId?.let { tid -> if (store.openItems(tid).isEmpty()) notifications.markDoneUnthreaded("/tasks/$tid") }
        }
        return closed
    }

    /** Closes the open items of a task ([kind]/[stage] null = any), with their notifications; returns how many. */
    fun closeItems(taskId: String, kind: String? = null, stage: String? = null): Int =
        store.openItems(taskId).filter { (kind == null || it.kind == kind) && (stage == null || it.stage == stage) }.count { closeItem(it.id) }

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
    fun once(t: Task, kind: String, note: String) {
        if (store.events(t.id).lastOrNull()?.note != note) store.event(t.id, kind, null, null, note, "keel")
    }

    fun githubToken(): String? =
        secrets.get("GITHUB_TOKEN") ?: secrets.get("GH_TOKEN")
            ?: (if (props.githubFromEnv) (System.getenv("GITHUB_TOKEN") ?: System.getenv("GH_TOKEN"))?.takeIf { it.isNotBlank() } else null)

    fun changed(pid: String) = hub.publish(pid, "project.changed", mapOf("id" to pid))

    fun link(t: Task) = "${props.publicUrl.trim().trimEnd('/').ifBlank { "http://127.0.0.1:8080" }}/#/tasks/${t.id}"

    /**
     * [raw] as a ticket key and the tracker it belongs to: the project's tracker first, then any other (a Jira key on a
     * project without a connection stays a Jira key). (null, null) for a blank key.
     */
    private fun key(pid: String, raw: String?): Pair<String?, TicketTracker?> {
        val r = raw?.trim()?.ifEmpty { null } ?: return null to null
        val all = trackers.orderedStream().toList()
        for (tr in listOfNotNull(all.forProject(pid)) + all) tr.key(r)?.let { return it to tr }
        if (JIRA_KEY.matches(r.uppercase())) return r.uppercase() to null      // a Jira key without the Jira plugin, as before
        throw BadRequest("\"$raw\" is not a ticket key", "Like ABC-123 (Jira) or #12 (a GitHub issue).")
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
        /** The key that ties an Inbox item's bell notification to it (the notification's thread_id, like an approval id's). */
        fun itemRef(itemId: Long) = "task-item:$itemId"
        val PR_OPENED = Regex("^PR opened: (https?://\\S+)")
        private val CRITERIA = Regex("```[ \\t]*keel-criteria[ \\t]*\\n(.*?)\\n[ \\t]*```", RegexOption.DOT_MATCHES_ALL)
        private val CRITERION = Regex("^[-*]?\\s*(AC-\\d+)\\s*\\[(\\w+)]\\s*(.+)$")
    }
}
