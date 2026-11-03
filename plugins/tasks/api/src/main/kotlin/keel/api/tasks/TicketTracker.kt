package keel.api.tasks

import org.springframework.beans.factory.ObjectProvider

/**
 * The outside tracker that holds a task's real ticket: the Jira plugin (plugins/jira) and, since 0.22.0, the GitHub
 * Issues plugin (plugins/github-issues) each give one bean; the Tasks plugin works without any. A project uses the
 * tracker it is connected to ([forProject]). Without a connection, a task with a ticket key asks the user, in the Inbox,
 * to move the ticket by hand.
 *
 * The Tasks plugin's extension point for the plugins that need it (requires.plugins: tasks).
 */
interface TicketTracker {
    /** A task's source when its ticket comes from this tracker ("jira", "github"). */
    val source: String get() = "jira"

    /** The tracker's name in what keel writes ("Jira", "GitHub"). */
    val title: String get() = "Jira"

    /** [raw] as this tracker's ticket key ("ABC-123", "#12"), or null when it is not one. */
    fun key(raw: String): String? = raw.trim().uppercase().takeIf { JIRA_KEY.matches(it) }

    /** The ticket's page. */
    fun ticketUrl(pid: String, key: String): String? = connection(pid)?.baseUrl?.let { "$it/browse/$key" }

    /** The line keel's pull request carries for the ticket, e.g. GitHub's "Closes #12"; null = none. */
    fun prLine(pid: String, key: String): String? = null

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

val JIRA_KEY = Regex("^[A-Z][A-Z0-9_]{0,29}-\\d{1,9}$")

/** The tracker [pid] is connected to; else the first one (its sync line says "not connected"); null without any. */
fun Iterable<TicketTracker>.forProject(pid: String): TicketTracker? = firstOrNull { it.connection(pid) != null } ?: firstOrNull()

/** The same for the beans Spring has. */
fun ObjectProvider<TicketTracker>.forProject(pid: String): TicketTracker? = orderedStream().toList().forProject(pid)

/** The tracker whose source is [source] ("jira", "github"), or null. */
fun ObjectProvider<TicketTracker>.bySource(source: String?): TicketTracker? = orderedStream().toList().firstOrNull { it.source == source }

/** What the task lifecycle needs of a project's tracker connection. */
data class TrackerConnection(
    /** The tracker's address (Jira: a ticket's link is <baseUrl>/browse/<key>; GitHub: the repository's page). */
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

    /** 0.18.52 the ticket's newest comments as "<author>: <text>", oldest first (the flow pick reads them). */
    fun comments(key: String, max: Int = 10): List<String> = emptyList()
}

/** A move: [moved] false = the ticket was in [to] already. */
interface TicketMove {
    val moved: Boolean
    val from: String?
    val to: String
}

/** The tracker said no, or could not be reached. */
open class TicketException(override val message: String, val hint: String? = null) : RuntimeException(message)
