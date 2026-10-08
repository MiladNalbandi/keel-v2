package keel.api.tasks

/**
 * The outside tracker that holds a task's real ticket. The Jira plugin (plugins/jira) gives the one bean; the Tasks
 * plugin works without it. Then no project is connected: a task with a ticket key asks the user, in the Inbox, to move
 * the ticket by hand, as it does today for a project without a Jira connection.
 *
 * The Tasks plugin's extension point for the plugins that need it (requires.plugins: tasks).
 */
interface TicketTracker {
    /** The project's connection, or null when it has none. */
    fun connection(pid: String): TrackerConnection?

    /** A client for the project's tickets; null when there is no connection (or its token is missing). */
    fun client(pid: String): TicketClient?

    /** The Tasks page's sync line for the project. */
    fun syncInfo(pid: String): SyncInfo

    /** "Sync now": the tickets become tasks. Null when the project has no connection. */
    fun sync(pid: String): SyncResult?

    /** The scheduler's tick (about every minute): sync each connection whose poll interval passed. */
    fun poll()
}

/** What the task lifecycle needs of a project's tracker connection. */
data class TrackerConnection(
    /** The tracker's address: a ticket's link is <baseUrl>/browse/<key>. */
    val baseUrl: String,
    /** keel status → tracker status or transition name; "-" = do not move the ticket for that status. */
    val statusMap: Map<String, String> = emptyMap(),
    /** The ticket field that holds reviewers, and the people keel puts in it when the PR opens. */
    val reviewerField: String? = null,
    val reviewers: List<String> = emptyList(),
    /** GitHub logins asked to review the PR when the task names none. */
    val githubReviewers: List<String> = emptyList(),
)

/** What keel does to a real ticket. A refusal is a [TicketException] with words a person can read (never the token). */
interface TicketClient {
    /** Moves the ticket to [target] (a status or a transition name). */
    fun transitionTo(key: String, target: String): TicketMove

    fun comment(key: String, text: String)

    /** Puts [users] in the ticket's user field [fieldId]. */
    fun setUsers(key: String, fieldId: String, users: List<String>)
}

/** A move: [moved] false = the ticket was in [to] already. */
interface TicketMove {
    val moved: Boolean
    val from: String?
    val to: String
}

/** The tracker said no, or could not be reached. */
open class TicketException(override val message: String, val hint: String? = null) : RuntimeException(message)
