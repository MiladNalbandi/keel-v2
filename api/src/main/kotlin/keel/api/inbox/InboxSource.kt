package keel.api.inbox

/**
 * A part's items in the Inbox (docs/plugins/09-step2-contract.md §2). [InboxService] merges every bean with core's own
 * items (the flows that wait for a person) and fills in each item's project name.
 */
interface InboxSource {
    /** "approvals", "tasks", … */
    val kind: String

    /** What waits, in one project or (null) in every project; `projectName` may stay blank. */
    fun items(pid: String?): List<InboxItem>

    /** How many items wait, in one project or (null) in every project. Cheap: badges and project cards ask often. */
    fun waiting(pid: String?): Int
}
