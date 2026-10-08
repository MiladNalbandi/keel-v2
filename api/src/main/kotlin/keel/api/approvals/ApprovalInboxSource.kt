package keel.api.approvals

import keel.api.inbox.InboxItem
import keel.api.inbox.InboxPermission
import keel.api.inbox.InboxService
import keel.api.inbox.InboxSource
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component

/**
 * The approvals that wait, as Inbox items. The engine's questions are commands that wait for the person's OK, so they
 * keep kind "permission" (the web shows them as before: Allow once, Always, Deny); the api's own show with their kind.
 * Answered with POST /api/approvals/{id}/decide.
 */
@Component
@Order(20)
class ApprovalInboxSource(private val approvals: ApprovalService) : InboxSource {
    override val kind = "approvals"

    override fun items(pid: String?): List<InboxItem> =
        approvals.list(ApprovalService.WAITING, pid).map { if (approvals.engineOwned(it)) permission(it) else item(it) }

    override fun waiting(pid: String?): Int = approvals.waitingCount(pid)

    private fun permission(a: Approval): InboxItem {
        val p = a.payload
        val session = p?.path("session")?.asText(null) ?: a.requestedBy ?: a.id
        val command = p?.path("command")?.asText(null) ?: a.detail
        return InboxItem(
            projectId = a.projectId, projectName = "", threadId = session, flow = a.title.ifBlank { "KeelBot" }, workflowId = null,
            step = "permission", kind = "permission",
            // v0.10.0: Claude Code's acting tool (keel2 mcp --write) asks with its own title ("Claude Code: push the branch?")
            title = if (session == MCP) a.title.ifBlank { "Claude Code asks" } else "KeelBot asks to run a command",
            detail = command.take(InboxService.DETAIL_MAX), more = command.length > InboxService.DETAIL_MAX,
            options = ApprovalService.ENGINE_DECISIONS, id = a.id, since = a.createdAt,
            permission = InboxPermission(a.id, session, command, p?.path("path")?.asText("")?.ifBlank { null }),
        )
    }

    private fun item(a: Approval): InboxItem = InboxItem(
        projectId = a.projectId, projectName = "", threadId = a.id, flow = a.source, workflowId = null, step = "approval",
        kind = a.kind, title = a.title, detail = a.detail.take(InboxService.DETAIL_MAX), more = a.detail.length > InboxService.DETAIL_MAX,
        options = approvals.decisionsOf(a), id = a.id, since = a.createdAt,
    )

    companion object {
        /** The engine's session for keel2 mcp's questions: Claude Code asks, with no KeelBot chat behind it. */
        const val MCP = "mcp"
    }
}
