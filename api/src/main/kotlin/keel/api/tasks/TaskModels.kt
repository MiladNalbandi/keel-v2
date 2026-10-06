package keel.api.tasks

import org.springframework.boot.context.properties.ConfigurationProperties

/** keel.tasks.* (application.yml): how the task lifecycle reaches the outside and runs in the background. */
@ConfigurationProperties(prefix = "keel.tasks")
data class TaskProperties(
    /** Run the lifecycle's effects of engine events on the event thread (tests) instead of a background worker. */
    val inlineEffects: Boolean = false,
    /** The Jira poll and the PR review poll (off in tests: they call "Sync now"). */
    val scheduler: Boolean = true,
    /** How often the scheduler looks (each connection keeps its own poll interval; PR reviews every 2 ticks). */
    val tickMs: Long = 60_000,
    /** The GitHub API root; blank = api.github.com (or https://<host>/api/v3 for an Enterprise PR URL). */
    val githubApi: String = "",
    /** Where people open keel (KEEL_PUBLIC_URL), for the links keel writes into Jira comments. */
    val publicUrl: String = "",
    /** Also read GITHUB_TOKEN / GH_TOKEN from the environment when no token is saved in Connections. */
    val githubFromEnv: Boolean = true,
)

object TaskStatus {
    const val TODO = "todo"
    const val IN_PROGRESS = "in_progress"
    const val IN_REVIEW = "in_review"
    const val TESTING_PP = "testing_pp"
    const val READY_PROD = "ready_prod"
    const val DONE = "done"
    const val CANCELLED = "cancelled"
    const val BLOCKED = "blocked"

    /** In board order. */
    val ALL = listOf(TODO, IN_PROGRESS, IN_REVIEW, TESTING_PP, READY_PROD, DONE, CANCELLED, BLOCKED)

    /** The Jira status keel moves a ticket to when the mapping names none. */
    val DEFAULT_JIRA = mapOf(
        TODO to "To Do", IN_PROGRESS to "In Progress", IN_REVIEW to "In Review", TESTING_PP to "Testing in PP",
        READY_PROD to "Ready for Production", DONE to "Done",
    )

    /** Statuses many Jira workflows do not have: keel moves the ticket there only when the mapping names one. */
    val ONLY_WHEN_MAPPED = setOf(BLOCKED, CANCELLED)

    /** A mapping value that means "leave the Jira ticket where it is". */
    const val DO_NOT_MOVE = "-"

    val LABELS = mapOf(
        TODO to "To do", IN_PROGRESS to "In progress", IN_REVIEW to "In review", TESTING_PP to "Testing (PP)",
        READY_PROD to "Ready for production", DONE to "Done", CANCELLED to "Cancelled", BLOCKED to "Blocked",
    )
}

object TaskTypes {
    val ALL = listOf("bug", "story", "task")

    /** The flow a task starts when the user does not pick one. */
    fun defaultWorkflow(type: String): String = when (type) {
        "bug" -> "fix"
        "story" -> "feature"
        else -> "change"
    }

    /** Jira's issue type → keel's type. */
    fun fromJira(type: String?): String = when (type?.lowercase()) {
        "bug", "defect", "incident" -> "bug"
        "story", "user story", "feature", "epic" -> "story"
        else -> "task"
    }
}

data class Reviewer(val login: String = "", val on: String = "github", val state: String = "wanted")

data class Task(
    val id: String,
    val projectId: String,
    val title: String,
    val description: String,
    val type: String,
    val status: String,
    val source: String,
    val externalKey: String?,
    val externalUrl: String?,
    val externalStatus: String?,
    val assignee: String?,
    val priority: String?,
    val threadId: String?,
    val workflowId: String?,
    val prUrl: String?,
    val reviewers: List<Reviewer>,
    val blockedReason: String?,
    val createdAt: String,
    val updatedAt: String,
) {
    /** "ABC-12" or the short id: what keel calls the task in titles. */
    val label: String get() = externalKey ?: "task ${id.take(8)}"
}

/** The task's flow as the api last saw it (threads table; no engine call). */
data class TaskFlow(val threadId: String, val workflowId: String?, val status: String, val phase: String?, val current: String?, val title: String?)

data class TaskView(
    val id: String,
    val projectId: String,
    val title: String,
    val description: String,
    val type: String,
    val status: String,
    val source: String,
    val externalKey: String?,
    val externalUrl: String?,
    val externalStatus: String?,
    val assignee: String?,
    val priority: String?,
    val threadId: String?,
    val workflowId: String?,
    val prUrl: String?,
    val reviewers: List<Reviewer>,
    val blockedReason: String?,
    val createdAt: String,
    val updatedAt: String,
    val flow: TaskFlow?,
    /** Open Inbox items of this task (Confirm PP, Ship, move in Jira by hand). */
    val waiting: List<TaskItem>,
    /** GET /tasks/{id} only: the history, oldest first. */
    val events: List<TaskEvent>? = null,
)

data class TaskEvent(val id: Long, val taskId: String, val at: String, val kind: String, val fromStatus: String?, val toStatus: String?, val note: String?, val actor: String)

data class TaskItem(
    val id: Long,
    val taskId: String,
    val projectId: String,
    /** task | jira-manual */
    val kind: String,
    /** task: pp | prod; jira-manual: the Jira status, or "reviewers" */
    val stage: String?,
    val title: String,
    val detail: String,
    val createdAt: String,
    val doneAt: String?,
)

data class SyncInfo(val connected: Boolean, val kind: String?, val lastSyncAt: String?, val lastSyncError: String?, val me: String?, val pollMinutes: Int?)
data class TaskList(val tasks: List<TaskView>, val sync: SyncInfo)

// ---- request bodies ----

data class NewTask(
    val title: String = "",
    val description: String? = null,
    val type: String? = null,
    val externalKey: String? = null,
    val externalUrl: String? = null,
    val assignee: String? = null,
    val priority: String? = null,
    val reviewers: List<String>? = null,
)

data class TaskPatch(
    val title: String? = null,
    val description: String? = null,
    val type: String? = null,
    val externalKey: String? = null,
    val externalUrl: String? = null,
    val assignee: String? = null,
    val priority: String? = null,
    /** GitHub logins to ask when the PR opens (replaces the wanted list). */
    val reviewers: List<String>? = null,
)

data class StartTask(
    val workflowId: String? = null,
    val runMode: String? = null,
    val allowDirty: Boolean = false,
    val allowFake: Boolean = false,
)

data class ConfirmTask(val stage: String = "", val note: String? = null)
data class MoveTask(val to: String = "", val note: String? = null)
data class PrBody(val url: String = "")
data class ItemAct(val action: String = "", val note: String? = null)
