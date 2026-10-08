package keel.api.tasks

import keel.api.tasks.TaskStatus.BLOCKED
import keel.api.tasks.TaskStatus.CANCELLED
import keel.api.tasks.TaskStatus.DONE
import keel.api.tasks.TaskStatus.IN_PROGRESS
import keel.api.tasks.TaskStatus.IN_REVIEW
import keel.api.tasks.TaskStatus.READY_PROD
import keel.api.tasks.TaskStatus.TESTING_PP
import keel.api.tasks.TaskStatus.TODO

/** What happened to a task: a person's action, an engine event, a PR review, or a change made in Jira. */
sealed interface Trigger {
    val actor: String

    /** The user pressed Start: a flow runs for the task now. */
    data class Start(val workflowId: String, val threadId: String, val link: String?) : Trigger { override val actor = "user" }

    /** The flow's open_pr step opened the PR (actor keel), or the user pasted its link (actor user). */
    data class PrOpened(val url: String, override val actor: String) : Trigger

    /** The flow ended well but keel knows no PR (no token, auto mode, nothing pushed). */
    data class FlowDone(val note: String? = null) : Trigger { override val actor = "keel" }

    /** The flow failed or was stopped. */
    data class FlowEnded(val failed: Boolean, val reason: String) : Trigger { override val actor = "keel" }

    /** The PR is approved on GitHub (no reviewer asks for changes). */
    data class Approved(val by: List<String>) : Trigger { override val actor = "keel" }

    /** The user confirmed a stage: pp (testing in pre-production passed) or prod (it is in production). */
    data class Confirm(val stage: String, val note: String?) : Trigger { override val actor = "user" }

    /** The user sent it back from review, PP or ready to In progress (a new flow can start). */
    data class SendBack(val note: String) : Trigger { override val actor = "user" }

    /** The user moved it by hand (board, drawer, Cancel). */
    data class Move(val to: String, val note: String?) : Trigger { override val actor = "user" }

    /** Someone moved the ticket in Jira; [keel] is the keel status it maps to (null: none, the task stays). */
    data class JiraMoved(val from: String?, val to: String, val keel: String?) : Trigger { override val actor = "jira" }
}

/** An Inbox item to open when the task enters a status. */
data class OpenItem(val stage: String, val title: String, val detail: String)

/**
 * What to do for one trigger. [to] equal to the current status = record the event only. [moveJira]: move the Jira
 * ticket to the status [to] maps to. Leaving a status closes the task's open "task" Inbox items.
 */
data class Plan(
    val to: String,
    val kind: String,
    val note: String,
    val actor: String,
    val moveJira: Boolean = false,
    val comment: String? = null,
    val askReviewers: Boolean = false,
    val openItem: OpenItem? = null,
    val stopFlow: Boolean = false,
    val blockedReason: String? = null,
    val prUrl: String? = null,
)

/** A trigger that does not fit the task's status: the api answers 409 with this text. */
class IllegalMove(override val message: String, val hint: String? = null) : RuntimeException(message)

/**
 * The task lifecycle in one place, without any IO: To do → In progress (a flow) → In review (its PR) → Testing (PP) →
 * Ready for production → Done, with Blocked (the flow failed) and Cancelled on the side. TaskService applies the plan
 * (database, Inbox, Jira, GitHub); docs/CONTRACT.md "v0.5.0: tasks and Jira" has the table.
 */
object TaskMachine {
    private fun label(s: String) = TaskStatus.LABELS[s] ?: s
    private fun tail(note: String?) = note?.trim()?.takeIf { it.isNotEmpty() }?.let { " — $it" } ?: ""

    /** The Inbox item a status asks for (only PP testing and shipping need a person). */
    fun itemFor(t: Task, status: String): OpenItem? = when (status) {
        TESTING_PP -> OpenItem("pp", "Confirm PP testing for ${t.label}",
            "Test \"${t.title}\" in pre-production (PP). Confirm when it works, or send it back with what is wrong.")
        READY_PROD -> OpenItem("prod", "Ship ${t.label} to production",
            "\"${t.title}\" passed PP. Ship it to production (keel does not deploy), then confirm here.")
        else -> null
    }

    fun plan(t: Task, tr: Trigger): Plan {
        val s = t.status
        return when (tr) {
            is Trigger.Start -> {
                if (s !in setOf(TODO, IN_PROGRESS, BLOCKED)) {
                    throw IllegalMove("${t.label} is ${label(s)}: a flow starts from To do, In progress or Blocked.",
                        "Send it back to In progress first.")
                }
                Plan(IN_PROGRESS, "start", "Started the ${tr.workflowId} flow.", tr.actor, moveJira = true,
                    comment = "keel started the ${tr.workflowId} flow for this ticket.${tr.link?.let { " $it" } ?: ""}")
            }
            is Trigger.PrOpened -> when (s) {
                TODO, IN_PROGRESS, BLOCKED -> Plan(IN_REVIEW, "pr", "${if (tr.actor == "user") "PR link added" else "Pull request opened"}: ${tr.url}",
                    tr.actor, moveJira = true, comment = "Pull request: ${tr.url}", askReviewers = true, prUrl = tr.url)
                IN_REVIEW -> Plan(IN_REVIEW, "pr", "PR link changed: ${tr.url}", tr.actor, comment = "Pull request: ${tr.url}",
                    askReviewers = tr.url != t.prUrl, prUrl = tr.url)
                else -> throw IllegalMove("${t.label} is ${label(s)}: its PR link cannot change now.")
            }
            is Trigger.FlowDone -> Plan(s, "flow",
                tr.note ?: if (s == IN_PROGRESS) "The flow finished without a pull request. Open the PR, then paste its link on the task."
                else "The flow finished.", tr.actor)
            is Trigger.FlowEnded -> {
                val what = if (tr.failed) "failed" else "was stopped"
                if (s == IN_PROGRESS) Plan(BLOCKED, "blocked", "The flow $what: ${tr.reason}", tr.actor, moveJira = true,
                    comment = "keel's flow $what: ${tr.reason}", blockedReason = tr.reason)
                else Plan(s, "flow", "The flow $what: ${tr.reason}", tr.actor)
            }
            is Trigger.Approved -> {
                val by = tr.by.joinToString().ifBlank { "a reviewer" }
                if (s == IN_REVIEW) Plan(TESTING_PP, "approved", "PR approved by $by.", tr.actor, moveJira = true,
                    comment = "The pull request was approved by $by. Next: testing in pre-production (PP).", openItem = itemFor(t, TESTING_PP))
                else Plan(s, "approved", "PR approved by $by.", tr.actor)
            }
            is Trigger.Confirm -> when {
                tr.stage == "pp" && s == TESTING_PP -> Plan(READY_PROD, "confirm", "PP testing confirmed${tail(tr.note)}", tr.actor,
                    moveJira = true, comment = "Testing in PP passed.${tail(tr.note)}", openItem = itemFor(t, READY_PROD))
                tr.stage == "prod" && s == READY_PROD -> Plan(DONE, "confirm", "Shipped to production${tail(tr.note)}", tr.actor,
                    moveJira = true, comment = "Shipped to production.${tail(tr.note)}")
                tr.stage !in setOf("pp", "prod") -> throw IllegalMove("stage must be pp or prod")
                else -> throw IllegalMove("${t.label} is ${label(s)}: there is no ${if (tr.stage == "pp") "PP test" else "release"} to confirm.")
            }
            is Trigger.SendBack -> {
                if (s !in setOf(IN_REVIEW, TESTING_PP, READY_PROD)) {
                    throw IllegalMove("${t.label} is ${label(s)}: only a task in review, PP or ready for production goes back.")
                }
                Plan(IN_PROGRESS, "send_back", "Sent back from ${label(s)}: ${tr.note}", tr.actor, moveJira = true,
                    comment = "Sent back from ${label(s)}: ${tr.note}")
            }
            is Trigger.Move -> {
                if (tr.to !in TaskStatus.ALL) throw IllegalMove("\"${tr.to}\" is not a task status", "Use one of: ${TaskStatus.ALL.joinToString()}.")
                if (tr.to == s) throw IllegalMove("${t.label} is already ${label(s)}.")
                Plan(tr.to, "status", "Moved from ${label(s)} to ${label(tr.to)}${tail(tr.note)}", tr.actor, moveJira = true,
                    comment = tr.note?.trim()?.takeIf { it.isNotEmpty() }?.let { "Moved to ${label(tr.to)}: $it" },
                    askReviewers = tr.to == IN_REVIEW && t.prUrl != null, openItem = itemFor(t, tr.to),
                    stopFlow = tr.to == CANCELLED, blockedReason = if (tr.to == BLOCKED) tr.note?.trim()?.ifEmpty { null } ?: "blocked by hand" else null)
            }
            is Trigger.JiraMoved -> {
                val to = tr.keel ?: s
                Plan(to, "jira_status", "Jira: ${tr.from ?: "?"} → ${tr.to}${if (to != s) " (keel: ${label(to)})" else ""}", tr.actor,
                    openItem = if (to != s) itemFor(t, to) else null, stopFlow = to == CANCELLED && s != CANCELLED)
            }
        }
    }

    /** The Jira status a keel status maps to: the mapping, else a usual name; null = do not move the ticket. */
    fun jiraTarget(status: String, map: Map<String, String>): String? {
        val mapped = map[status]?.trim()
        if (mapped == TaskStatus.DO_NOT_MOVE) return null
        if (!mapped.isNullOrEmpty()) return mapped
        if (status in TaskStatus.ONLY_WHEN_MAPPED) return null
        return TaskStatus.DEFAULT_JIRA[status]
    }

    /**
     * The keel status a Jira status means: the mapping (the current status wins when several map to it), else a usual
     * name, else the status category (done → done; new and in-progress only for a new task). Null: keep the task where it is.
     */
    fun keelStatus(jira: String, category: String?, map: Map<String, String>, current: String?): String? {
        val mapped = TaskStatus.ALL.filter { map[it]?.trim()?.equals(jira, ignoreCase = true) == true }
        if (mapped.isNotEmpty()) return if (current != null && current in mapped) current else mapped.first()
        val usual = TaskStatus.DEFAULT_JIRA.entries.firstOrNull { it.value.equals(jira, ignoreCase = true) }?.key
        if (usual != null) return usual
        return when (category) {
            "done" -> if (Regex("cancel|won.?t|reject|declin|invalid|duplicate", RegexOption.IGNORE_CASE).containsMatchIn(jira)) CANCELLED else DONE
            "indeterminate" -> if (current == null) IN_PROGRESS else null
            "new" -> if (current == null) TODO else null
            else -> null
        }
    }

    /** A first mapping from the Jira statuses keel found (the user checks it in Connections › Jira). */
    fun suggest(statuses: List<Pair<String, String?>>): Map<String, String> {
        fun find(vararg rx: String): String? = rx.firstNotNullOfOrNull { r ->
            statuses.firstOrNull { Regex(r, RegexOption.IGNORE_CASE).containsMatchIn(it.first) }?.first
        }
        val out = linkedMapOf<String, String>()
        (find("^to ?do$", "^open$", "^backlog$", "^selected") ?: statuses.firstOrNull { it.second == "new" }?.first)?.let { out[TODO] = it }
        (find("^in progress$", "progress", "develop") ?: statuses.firstOrNull { it.second == "indeterminate" }?.first)?.let { out[IN_PROGRESS] = it }
        find("review")?.let { out[IN_REVIEW] = it }
        find("\\bpp\\b", "pre.?prod", "staging", "\\bqa\\b", "test")?.let { out[TESTING_PP] = it }
        find("ready.*(prod|release|deploy)", "^ready", "release")?.let { out[READY_PROD] = it }
        (find("^done$", "^closed$", "^resolved$") ?: statuses.firstOrNull { it.second == "done" }?.first)?.let { out[DONE] = it }
        find("cancel", "won.?t", "reject")?.let { out[CANCELLED] = it }
        find("block", "on hold")?.let { out[BLOCKED] = it }
        return out
    }
}
