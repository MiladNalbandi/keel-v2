package keel.api.tasks

import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.Conflict
import keel.api.common.NotFound
import keel.api.events.EngineEvent
import keel.api.events.EngineEventStored
import keel.api.flow.FlowCap
import keel.api.flow.FlowService
import keel.api.flow.PrCommentJudge
import keel.api.flow.PrCommentVerdict
import keel.api.flow.PrThread
import keel.api.flow.checkRunMode
import keel.api.projects.ProjectService
import keel.api.tasks.TaskStatus.BLOCKED
import keel.api.tasks.TaskStatus.IN_PROGRESS
import keel.api.tasks.TaskStatus.IN_REVIEW
import keel.api.tasks.TaskStatus.TODO
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.context.event.EventListener
import org.springframework.stereotype.Service
import java.util.concurrent.Executors

/**
 * 0.18.52 task automations (docs/plans/task-automations.md): keel proposes, the person approves.
 *
 * - A ticket moved in Jira (seen by the sync, never keel's own move) into a status with a rule: keel picks the best flow
 *   ([TaskPicker]) and opens an Inbox proposal (kind task-proposal: Start / Use another flow / Not now, with the
 *   model's questions first), or starts it by itself when the rule says so (run mode, token cap, a daily limit).
 * - In review: the flow is a review of the task's pull request; it runs read-only and never changes the task's status.
 * - A ticket assigned to KeelBot: keel watches its PR; each new review comment gets the cheap "Is the reviewer right?"
 *   check ([PrCommentJudge], the Code Review plugin), a short Jira comment and an Inbox item (kind task-review: Use
 *   keel's reply / Address it / I'll handle it). keel replies by itself only with the project's allow-reply switch.
 * - The task's buttons "Pick a flow for me" and "Review it" open the same proposals.
 *
 * Every step writes a line in the task's history (kind automation, actor keel). Picks and checks run on one background
 * worker (on the caller's thread in tests), so a sync never waits for a model.
 */
@Service
class TaskAutomations(
    private val store: TaskStore,
    private val auto: TaskAutomationStore,
    private val tasks: TaskService,
    private val picker: TaskPicker,
    private val flows: FlowService,
    private val projects: ProjectService,
    private val trackers: ObjectProvider<TicketTracker>,
    private val judges: ObjectProvider<PrCommentJudge>,
    private val props: TaskProperties,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "task-automations").apply { isDaemon = true } }

    companion object {
        const val PROPOSAL = "task-proposal"
        const val REVIEW_ITEM = "task-review"
        val KINDS = setOf(PROPOSAL, REVIEW_ITEM)
        /** The statuses a rule can name: a flow for in_progress and blocked, a review for in_review. */
        val RULE_STATUSES = listOf(IN_PROGRESS, IN_REVIEW, BLOCKED)
        /** At most this many new PR comments are checked per task and poll (the rest wait for the next poll). */
        const val CHECKS_PER_POLL = 3
        private val ACCOUNT = Regex("^[A-Za-z0-9:_.@ -]{1,128}$")
        private val LABEL = Regex("^[A-Za-z0-9_.-]{1,64}$")
        val VERDICT_WORDS = mapOf("reviewer_right" to "the reviewer is right", "author_right" to "the code is fine as it is",
            "partly" to "the reviewer is partly right", "unclear" to "it is not clear")
        private val SECRET = Regex("(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|" +
            "sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}|[A-Za-z0-9+=_-]{40,})")
        private val CODE = Regex("```.*?(```|$)", RegexOption.DOT_MATCHES_ALL)

        /** One line a person can read in Jira: no code blocks, no secrets, at most [max] characters. */
        fun plain(text: String, max: Int): String {
            val s = CODE.replace(text, " [code] ").replace(Regex("`([^`]{0,80})`"), "$1").replace(SECRET, "***").replace(Regex("\\s+"), " ").trim()
            return if (s.length > max) s.take(max - 1).trimEnd() + "…" else s
        }
    }

    // ---- settings --------------------------------------------------------------------------------

    fun view(pid: String): AutomationView {
        projects.require(pid)
        return AutomationView(auto.settings(pid), RULE_STATUSES, AutomationSettings.MODES, judges.ifAvailable != null,
            trackers.forProject(pid)?.connection(pid) != null, auto.autoStartsToday(pid))
    }

    fun save(pid: String, b: AutomationSave): AutomationView {
        projects.require(pid)
        val cur = auto.settings(pid)
        val rules = (b.rules ?: cur.rules).mapValues { (_, r) ->
            r.copy(mode = r.mode.trim().lowercase(), runMode = r.runMode?.trim()?.ifEmpty { null })
        }
        rules.forEach { (status, r) ->
            if (status !in RULE_STATUSES) throw BadRequest("A rule cannot name \"$status\"", "Use one of: ${RULE_STATUSES.joinToString()}.")
            if (r.mode !in AutomationSettings.MODES) throw BadRequest("mode must be off, propose or start")
            checkRunMode(r.runMode)
            if (r.capTokens != null && r.capTokens <= 0) throw BadRequest("cap_tokens must be above 0")
        }
        val kb = (b.keelbot ?: cur.keelbot).let { KeelBotOwner(it.account?.trim()?.ifEmpty { null }, it.label?.trim()?.ifEmpty { null }) }
        if (kb.account != null && !ACCOUNT.matches(kb.account)) throw BadRequest("The KeelBot account is not valid", "An account id, a user name or a display name (letters, digits, spaces, : _ . @ -).")
        if (kb.label != null && !LABEL.matches(kb.label)) throw BadRequest("The label is not valid", "One Jira label, like keel (letters, digits, _ . -).")
        val next = AutomationSettings(rules, kb, b.allowReply ?: cur.allowReply, b.dailyAutoStarts ?: cur.dailyAutoStarts, b.pickCapTokens ?: cur.pickCapTokens)
        if (next.dailyAutoStarts !in 0..100) throw BadRequest("daily_auto_starts must be 0 to 100", "0 = keel never starts a flow by itself.")
        if (next.pickCapTokens !in 1_000..200_000) throw BadRequest("pick_cap_tokens must be 1000 to 200000")
        auto.save(pid, next)
        tasks.changed(pid)
        return view(pid)
    }

    fun settings(pid: String): AutomationSettings = auto.settings(pid)

    // ---- a Jira move ------------------------------------------------------------------------------

    /**
     * The sync saw the ticket move in Jira and the task moved from [before]'s status to [after]'s (keel's own moves set
     * the Jira status keel saw first, so they never get here). The status's rule decides.
     */
    fun onJiraMoved(before: Task, after: Task) {
        if (after.status == before.status) return
        val rule = auto.settings(after.projectId).rule(after.status)
        if (rule.mode == "off" || after.status !in RULE_STATUSES) return
        go(after.id) {
            val t = store.require(after.id)
            val why = "Jira moved ${t.label} to ${t.externalStatus ?: TaskStatus.LABELS[t.status]}"
            if (t.status == IN_REVIEW) review(t, rule, why, byRule = true) else flow(t, rule, why, byRule = true)
        }
    }

    /** "Pick a flow for me": a proposal now (a rule never starts it). */
    fun pickFor(id: String): TaskView {
        val t = store.require(id)
        if (t.status !in setOf(TODO, IN_PROGRESS, BLOCKED)) {
            throw Conflict("${t.label} is ${TaskStatus.LABELS[t.status]}: a flow starts from To do, In progress or Blocked.",
                if (t.status == IN_REVIEW) "Use Review it for its pull request." else "Send it back to In progress first.")
        }
        running(t)?.let { throw Conflict("A flow already runs for ${t.label}", "Open it on the Flow page, or stop it first.") }
        // the pick may wait for the model: the history says it started (and the proposal's line follows)
        store.event(t.id, "automation", null, null, "You asked keel to pick a flow: it reads the ticket and this project's flows. Its proposal will be in your Inbox.", "user")
        tasks.changed(t.projectId)
        flow(t, AutomationRule("propose"), "You asked keel to pick a flow", byRule = false)
        return tasks.get(id)
    }

    /** "Review it": a proposal to review the task's pull request. */
    fun reviewFor(id: String): TaskView {
        val t = store.require(id)
        if (t.prUrl == null) throw Conflict("${t.label} has no pull request link", "Paste the PR's link on the task first.")
        review(t, AutomationRule("propose"), "You asked keel to review it", byRule = false)
        return tasks.get(id)
    }

    private fun running(t: Task) = t.threadId?.let { store.flow(it) }?.takeIf { it.status in setOf("running", "waiting") }

    /** B12 which ticket text a proposal was made from (title and description), to see at Start whether it changed since. */
    private fun ticketSig(t: Task): String {
        val md = java.security.MessageDigest.getInstance("SHA-256").digest("${t.title}\u0000${t.description}".toByteArray())
        return md.take(8).joinToString("") { "%02x".format(it) }
    }

    private fun flow(t: Task, rule: AutomationRule, why: String, byRule: Boolean) {
        if (running(t) != null) {
            note(t, "$why, but a flow already runs for it: keel proposes nothing.")
            return
        }
        val s = auto.settings(t.projectId)
        val pick = picker.pick(t, "flow", s.pickCapTokens)
        if (byRule && rule.mode == "start") {
            val blocker = startBlocker(t, s, pick)
            if (blocker == null) {
                try {
                    tasks.start(t.id, StartTask(pick.workflowId, rule.runMode, title = pick.title, request = pick.request, capTokens = rule.capTokens), "keel")
                    store.event(t.id, "auto_start", null, null, "$why: keel started the ${pick.workflowId} flow by itself (rule: start). Why: ${pick.why}", "keel")
                    tasks.changed(t.projectId)
                    return
                } catch (e: ApiException) {
                    note(t, "keel did not start the ${pick.workflowId} flow by itself (${e.message}): it asks you instead.")
                }
            } else note(t, "keel did not start the ${pick.workflowId} flow by itself: $blocker. It asks you instead.")
        }
        propose(t, "flow", pick, why, rule)
    }

    private fun startBlocker(t: Task, s: AutomationSettings, pick: FlowPick): String? = when {
        pick.questions.isNotEmpty() -> "it has questions for you first"
        auto.autoStartsToday(t.projectId) >= s.dailyAutoStarts -> "the daily limit of ${s.dailyAutoStarts} flow(s) keel starts by itself is reached"
        else -> null
    }

    private fun review(t: Task, rule: AutomationRule, why: String, byRule: Boolean) {
        if (t.prUrl == null) {
            close(t, PROPOSAL, "review")
            tasks.openItem(t, PROPOSAL, "review", "${t.label} is in review: paste its pull request so keel can review it",
                "$why. keel knows no pull request for it. Paste the link on the task, then press Review it.",
                mapOf("kind" to "review", "no_pr" to true, "trigger" to why))
            note(t, "$why: keel knows no pull request to review.")
            return
        }
        val s = auto.settings(t.projectId)
        val pick = picker.pick(t, "review", s.pickCapTokens)
        if (byRule && rule.mode == "start") {
            val blocker = startBlocker(t, s, pick)
            if (blocker == null) {
                try {
                    val tid = startReview(t, pick.workflowId, pick, rule.runMode, rule.capTokens)
                    store.event(t.id, "auto_start", null, null, "$why: keel started the ${pick.workflowId} flow on ${t.prUrl} by itself (rule: start). It posts nothing on the pull request.", "keel")
                    log.debug("review flow {} for {}", tid, t.id)
                    tasks.changed(t.projectId)
                    return
                } catch (e: ApiException) {
                    note(t, "keel did not start the review by itself (${e.message}): it asks you instead.")
                }
            } else note(t, "keel did not start the review by itself: $blocker. It asks you instead.")
        }
        propose(t, "review", pick, why, rule)
    }

    private fun propose(t: Task, kind: String, pick: FlowPick, why: String, rule: AutomationRule) {
        close(t, PROPOSAL, kind)
        val what = if (kind == "review") "review ${t.prUrl} with the ${pick.workflowId} flow" else "run the ${pick.workflowId} flow"
        val ask = if (pick.questions.isNotEmpty()) " ${pick.questions.size} question(s) first." else ""
        tasks.openItem(t, PROPOSAL, kind, "I want to $what on ${t.label}", "Why: ${pick.why}$ask", mapOf(
            "kind" to kind, "workflow_id" to pick.workflowId, "why" to pick.why, "title" to pick.title, "request" to pick.request,
            "questions" to pick.questions, "settings" to pick.settings, "fallback" to pick.fallback, "model" to pick.model,
            "run_mode" to rule.runMode, "cap_tokens" to rule.capTokens, "trigger" to why, "pr_url" to t.prUrl, "ticket_sig" to ticketSig(t),
        ))
        store.event(t.id, "automation", null, null, "$why: keel proposes the ${pick.workflowId} flow${if (pick.fallback) " (its default)" else ""}. Why: ${pick.why}", "keel")
        tasks.changed(t.projectId)
    }

    /** A review flow for the task's PR: read-only, on the branch the folder is on, never changes the task's status. */
    private fun startReview(t: Task, workflowId: String, pick: FlowPick?, runMode: String?, capTokens: Int?, answers: String = ""): String {
        val pr = t.prUrl ?: throw Conflict("${t.label} has no pull request link")
        val ticket = buildString {
            append(t.externalKey?.let { "$it: " } ?: "").append(t.title)
            if (t.description.isNotBlank()) append("\n\n").append(t.description.take(4000))
            pick?.request?.takeIf { it.isNotBlank() && it != t.title }?.let { append("\n\nFocus: ").append(it) }
            if (answers.isNotBlank()) append("\n\n").append(answers)
        }
        val options = if (workflowId == TaskPicker.REVIEW_DEFAULT) mapOf("pr" to pr, "ticket" to ticket) else null
        val title = "Review ${t.label}: ${t.title}".take(200)
        val state = flows.start(t.projectId, workflowId, title, null, FlowCap(capTokens = capTokens, runMode = runMode), allowDirty = true,
            request = "$pr\n\n$ticket", options = options, ownBranch = false)
        val tid = state.get("thread_id")?.asText()?.takeIf { it.isNotBlank() } ?: throw Conflict("The engine did not say which flow it started")
        auto.addRun(tid, t.id, "review", workflowId)
        store.event(t.id, "review_flow", null, null, "Started the $workflowId flow on $pr (read-only: it posts nothing without your OK).", "keel")
        return tid
    }

    // ---- the Inbox's answers ----------------------------------------------------------------------

    fun act(itemId: Long, b: ItemAct): TaskView {
        val item = store.item(itemId) ?: throw NotFound("No inbox item $itemId")
        if (item.doneAt != null) throw Conflict("This was answered already", "Reload the inbox.")
        val t = store.require(item.taskId)
        val d = item.data.orEmpty()
        when (item.kind) {
            PROPOSAL -> when (b.action) {
                "start", "other" -> {
                    if (d["no_pr"] == true) throw Conflict("keel knows no pull request for ${t.label}", "Paste the link on the task, then press Review it.")
                    val wid = if (b.action == "other") b.workflowId?.trim()?.ifEmpty { null } ?: throw BadRequest("Pick the flow to run")
                    else d["workflow_id"]?.toString() ?: throw Conflict("This proposal names no flow")
                    val answers = answers(d, b.answers)
                    val runMode = d["run_mode"]?.toString()
                    val cap = (d["cap_tokens"] as? Number)?.toInt()
                    // B12 the ticket changed since the proposal (or the proposal is keel's default, which only copied the
                    // ticket): the flow gets the ticket as it is now, not the proposal's old copy of it
                    val changed = d["ticket_sig"]?.let { it != ticketSig(t) } ?: false
                    val fresh = changed || d["fallback"] == true
                    val asked = if (fresh) "" else d["request"]?.toString().orEmpty()
                    if (d["kind"] == "review") {
                        startReview(t, wid, FlowPick(wid, "", "", asked), runMode, cap, answers)
                    } else {
                        val request = listOf(asked.ifBlank { if (answers.isBlank()) "" else tasks.ticketText(t) }, answers).filter { it.isNotBlank() }.joinToString("\n\n")
                        tasks.start(t.id, StartTask(wid, runMode, allowDirty = b.allowDirty, title = if (b.action == "start" && !fresh) d["title"]?.toString() else null,
                            request = request.ifBlank { null }, capTokens = cap))
                    }
                    if (changed) store.event(t.id, "automation", null, null, "The ticket changed after keel's proposal: the flow got the ticket as it is now.", "keel")
                    tasks.closeItem(item.id)
                    store.event(t.id, "automation", null, null, "You started the $wid flow from keel's proposal${if (b.action == "other") " (another flow than keel's ${d["workflow_id"]})" else ""}.", "user")
                }
                "later" -> {
                    tasks.closeItem(item.id)
                    store.event(t.id, "automation", null, null, "Not now: you put keel's ${d["workflow_id"] ?: "flow"} proposal aside.${b.note?.let { " $it" } ?: ""}", "user")
                }
                else -> throw BadRequest("action must be start, other or later")
            }
            REVIEW_ITEM -> {
                val pr = d["pr_url"]?.toString() ?: t.prUrl ?: throw Conflict("${t.label} has no pull request link")
                val thread = d["thread_id"]?.toString() ?: item.stage ?: throw Conflict("This item names no thread")
                when (b.action) {
                    "reply" -> {
                        val text = b.note?.trim()?.ifEmpty { null } ?: d["reply"]?.toString()?.trim()?.ifEmpty { null }
                            ?: throw BadRequest("keel has no reply for this comment", "Write one in the note, or address it.")
                        judge().reply(t.projectId, pr, thread, text)
                        tasks.closeItem(item.id)
                        store.event(t.id, "automation", null, null, "You posted keel's reply to ${d["author"] ?: "the reviewer"} on the pull request.", "user")
                    }
                    "address" -> {
                        val tid = judge().address(t.projectId, pr, thread)
                        auto.addRun(tid, t.id, "address", null)
                        tasks.closeItem(item.id)
                        store.event(t.id, "automation", null, null, "You asked keel to address ${d["author"] ?: "the reviewer"}'s comment: a small flow works on the PR's branch (it pushes nothing).", "user")
                    }
                    "handle" -> {
                        tasks.closeItem(item.id)
                        store.event(t.id, "automation", null, null, "You handle ${d["author"] ?: "the reviewer"}'s comment yourself.", "user")
                    }
                    else -> throw BadRequest("action must be reply, address or handle")
                }
            }
            else -> throw BadRequest("Unknown item kind ${item.kind}")
        }
        tasks.changed(t.projectId)
        return tasks.get(t.id)
    }

    /** The person's answers to the proposal's questions, as lines the flow reads. */
    private fun answers(d: Map<String, Any?>, given: Map<String, Any?>?): String {
        if (given.isNullOrEmpty()) return ""
        @Suppress("UNCHECKED_CAST")
        val qs = (d["questions"] as? List<Map<String, Any?>>).orEmpty()
        val lines = given.mapNotNull { (id, a) ->
            val text = when (a) {
                is List<*> -> a.mapNotNull { it?.toString()?.trim()?.ifEmpty { null } }.joinToString(", ")
                null -> ""
                else -> a.toString().trim()
            }.take(500)
            if (text.isEmpty()) null else "- ${(qs.firstOrNull { it["id"] == id }?.get("question") ?: id).toString().take(300)} $text"
        }
        return if (lines.isEmpty()) "" else "Answers to keel's questions:\n" + lines.joinToString("\n")
    }

    private fun judge(): PrCommentJudge = judges.ifAvailable
        ?: throw Conflict("The Code Review plugin is off", "Turn it on in Plugins: keel reads and answers PR comments with it.")

    // ---- KeelBot-owned tickets --------------------------------------------------------------------

    /** Whether a ticket is KeelBot's: its assignee is the configured account (id, name or display name), or it has the label. */
    fun owns(pid: String, assigneeIds: Collection<String?>, labels: Collection<String>): Boolean {
        val kb = auto.settings(pid).keelbot
        val account = kb.account
        if (account != null && assigneeIds.any { it != null && it.equals(account, ignoreCase = true) }) return true
        return kb.label != null && labels.any { it.equals(kb.label, ignoreCase = true) }
    }

    /** The sync read the ticket: keel starts or stops watching it. */
    fun onTicketSeen(taskId: String, owned: Boolean) {
        val t = store.find(taskId) ?: return
        val w = auto.watch(t.id)
        if (owned && w?.owned != true) {
            auto.setOwned(t.id, true, t.prUrl)
            store.event(t.id, "automation", null, null, "KeelBot owns ${t.label}: keel watches its pull request's review comments and asks you before it answers.", "keel")
        } else if (!owned && w?.owned == true) {
            auto.setOwned(t.id, false, t.prUrl)
            store.event(t.id, "automation", null, null, "${t.label} is no longer assigned to KeelBot: keel stops watching its pull request.", "keel")
        }
    }

    /** The review poll: new comments on the PRs of KeelBot's tickets (of [pid], or all). Returns how many were checked. */
    fun watchReviews(pid: String? = null): Int {
        val judge = judges.ifAvailable ?: return 0
        var checked = 0
        for (w in auto.owned()) {
            val t = store.find(w.taskId) ?: continue
            if (pid != null && t.projectId != pid) continue
            if (t.status in setOf(TaskStatus.DONE, TaskStatus.CANCELLED)) continue
            val pr = t.prUrl ?: continue
            if (w.prUrl != pr) auto.setOwned(t.id, true, pr)
            checked += runCatching { watchOne(judge, t, pr) }.onFailure { tasks.once(t, "automation", "keel could not read the review comments: ${it.message}") }.getOrDefault(0)
        }
        return checked
    }

    private fun watchOne(judge: PrCommentJudge, t: Task, pr: String): Int {
        val w = auto.watch(t.id) ?: return 0
        val all = judge.threads(t.projectId, pr)
        val keyOf = { th: PrThread -> "${th.id}:${th.lastCommentId}" }
        if (!w.looked) {
            auto.markSeen(t.id, all.threads.map(keyOf))
            store.event(t.id, "automation", null, null, "keel watches ${pr}: ${all.threads.size} thread(s) there now; it checks the comments that come after.", "keel")
            return 0
        }
        val fresh = all.threads.filter { !it.resolved && keyOf(it) !in w.seen && (all.author == null || !it.lastAuthor.equals(all.author, ignoreCase = true)) }
        // comments by the PR's author (or keel's replies) are not reviews: remember them without a check
        val skip = all.threads.filter { keyOf(it) !in w.seen && it !in fresh }.map(keyOf)
        if (skip.isNotEmpty()) auto.markSeen(t.id, skip)
        var n = 0
        for (th in fresh.take(CHECKS_PER_POLL)) {
            val v = runCatching { judge.check(t.projectId, pr, th.id) }.getOrElse { PrCommentVerdict(null, error = it.message ?: "the check failed") }
            reviewed(judge, t, pr, th, v)
            auto.markSeen(t.id, listOf(keyOf(th)))
            n++
        }
        return n
    }

    private fun reviewed(judge: PrCommentJudge, t: Task, pr: String, th: PrThread, v: PrCommentVerdict) {
        val where = th.path?.let { p -> p + (th.line?.let { ":$it" } ?: "") }
        val said = plain(th.lastBody, 300)
        val verdict = v.verdict?.let { VERDICT_WORDS[it] ?: it }
        val check = when {
            verdict != null -> "keel's check: $verdict${v.why.takeIf { it.isNotBlank() }?.let { " (${plain(it, 200)})" } ?: ""}."
            v.flow != null -> "keel checks it with the ${v.flow} flow."
            else -> "keel could not check it${v.error?.let { " (${plain(it, 120)})" } ?: ""}."
        }
        val s = auto.settings(t.projectId)
        var replied = false
        if (s.allowReply && v.reply.isNotBlank()) {
            replied = runCatching { judge.reply(t.projectId, pr, th.id, v.reply) }
                .onFailure { note(t, "keel could not post its reply: ${it.message}") }.isSuccess
        }
        val next = if (replied) "keel replied on the pull request (allowed by the project's switch)." else "Waiting for a decision in keel."
        val key = t.externalKey
        val tracker = trackers.bySource(t.source) ?: trackers.forProject(t.projectId)
        val client = key?.let { tracker?.client(t.projectId) }
        if (key != null && client != null) {
            try {
                client.comment(key, "keel: ${th.lastAuthor} commented on the pull request${where?.let { " ($it)" } ?: ""}: \"$said\" $check $next")
            } catch (e: TicketException) {
                store.event(t.id, "jira_error", null, null, "${tracker?.title ?: "Jira"}: the review comment note was not added. ${e.message}", "keel")
            }
        }
        store.event(t.id, "automation", null, null, "${th.lastAuthor} commented${where?.let { " on $it" } ?: ""}: \"${plain(th.lastBody, 160)}\" $check", "keel")
        if (replied) return
        close(t, REVIEW_ITEM, th.id)
        tasks.openItem(t, REVIEW_ITEM, th.id, "${th.lastAuthor} commented on ${t.label}'s pull request: ${verdict ?: "keel could not check it"}",
            "\"${plain(th.lastBody, 300)}\"${where?.let { " ($it)" } ?: ""}. $check", mapOf(
                "thread_id" to th.id, "pr_url" to pr, "author" to th.lastAuthor, "where" to where, "said" to plain(th.lastBody, 600),
                "verdict" to v.verdict, "why" to v.why.take(600), "reply" to v.reply.take(4000), "url" to th.url, "flow" to v.flow, "error" to v.error,
            ))
    }

    // ---- the review flows' end --------------------------------------------------------------------

    @EventListener
    fun on(stored: EngineEventStored) {
        val e = stored.event
        if (e.type != "thread.done" && e.type != "thread.failed") return
        val run = e.threadId.takeIf { it.isNotBlank() }?.let { auto.run(it) } ?: return
        go(run.taskId) { ended(run, e) }
    }

    private fun ended(run: TaskRun, e: EngineEvent) {
        val t = store.find(run.taskId) ?: return
        val what = if (run.kind == "review") "The ${run.workflowId ?: "review"} flow" else "The flow that addresses the review comment"
        val stopped = e.data["status"]?.toString() == "stopped"
        val note = when {
            e.type == "thread.failed" -> "$what failed: ${e.data["error"]?.toString()?.take(300) ?: "a step failed"}"
            stopped -> "$what was stopped."
            else -> "$what finished${e.data["summary"]?.toString()?.takeIf { it.isNotBlank() }?.let { ": ${plain(it, 400)}" } ?: "."} Its result is on the Flow page."
        }
        store.event(t.id, "automation", null, null, note, "keel")
        tasks.changed(t.projectId)
    }

    // ---- small helpers ----------------------------------------------------------------------------

    private fun close(t: Task, kind: String, stage: String) {
        tasks.closeItems(t.id, kind = kind, stage = stage)
    }

    private fun note(t: Task, text: String) {
        store.event(t.id, "automation", null, null, text, "keel")
    }

    /** One task's automation work, on the worker (or now, in tests); a failure is a line in its history. */
    private fun go(taskId: String, block: () -> Unit) {
        val run: () -> Unit = {
            try {
                block()
            } catch (e: Exception) {
                log.warn("task automation {}: {}", taskId, e.message)
                runCatching { store.find(taskId)?.let { note(it, "keel's automation stopped: ${e.message ?: e.javaClass.simpleName}") } }
            }
            Unit
        }
        if (props.inlineEffects) run() else worker.execute(run)
    }
}
