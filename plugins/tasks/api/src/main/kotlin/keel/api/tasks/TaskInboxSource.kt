package keel.api.tasks

import keel.api.inbox.InboxItem
import keel.api.inbox.InboxSource
import keel.api.inbox.InboxTask
import keel.api.inbox.InboxTaskAction
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component

/** v0.5.0: a task's items in the Inbox (table task_inbox): confirm PP testing, ship, or move the Jira ticket by hand. */
@Component
@Order(10)
class TaskInboxSource(private val tasks: TaskStore) : InboxSource {
    override val kind = "tasks"

    override fun items(pid: String?): List<InboxItem> =
        tasks.openItemsAll().filter { pid == null || it.projectId == pid }.mapNotNull { item(it) }

    override fun waiting(pid: String?): Int = if (pid == null) tasks.openItemsAll().size else tasks.openCount(pid)

    /** Answered with POST /api/inbox/tasks/{item_id}/act; an item whose task is gone is left out. */
    private fun item(it: TaskItem): InboxItem? {
        val t = tasks.find(it.taskId) ?: return null
        val actions = if (it.kind == "jira-manual") listOf(InboxTaskAction("done", "Done"))
        else listOf(
            InboxTaskAction("confirm", if (it.stage == "prod") "Shipped, confirm" else "PP works, confirm"),
            InboxTaskAction("send_back", "Send back", needsNote = true),
        )
        return InboxItem(
            projectId = it.projectId, projectName = "", threadId = t.threadId ?: "", flow = t.title, workflowId = t.workflowId,
            step = null, kind = it.kind, title = it.title, detail = it.detail, more = false, options = emptyList(), id = "task-item-${it.id}",
            since = it.createdAt,
            task = InboxTask(t.id, it.id, t.externalKey, t.externalUrl, t.title, t.status, it.stage, t.prUrl, actions),
        )
    }
}
