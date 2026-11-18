package dev.keel.jetbrains.core

import com.intellij.notification.NotificationAction
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.project.Project
import dev.keel.jetbrains.ui.CommentDialog
import dev.keel.jetbrains.ui.KeelNotifier
import dev.keel.jetbrains.ui.KeelToolWindow
import dev.keel.jetbrains.ui.ReviewPanel

/** A pending comment of your review: asked for, kept on keel, then shown in the Review tab's Pending. */
object ReviewComments {
    fun add(project: Project, path: String, line: Int, side: String, where: String = "$path:$line", startLine: Int? = null, code: String? = null) {
        val session = ReviewSession.of(project)
        if (session.key == null || KeelProjectService.of(project).pid == null) {
            KeelNotifier.error(project, "No pull request is open", "Open the PR in keel's Review tab first, then comment on its lines.")
            return
        }
        // 0.36 the comment box can ask KeelBot about these lines (the PR's review chat, core/ReviewAsk)
        val pid = KeelProjectService.of(project).pid!!
        val pr = session.key?.substringAfter("pr:", "")?.toIntOrNull()
        val chatKey = ReviewAsk.chatKey(pid, pr ?: session.key)
        CommentDialog.open(project, where, code, chatKey, pr) { text -> keep(project, session, path, line, side, where, startLine, text) }
    }

    private fun keep(project: Project, session: ReviewSession, path: String, line: Int, side: String, where: String, startLine: Int?, text: String) {
        session.call({ api, pid, key -> api.addDraft(pid, key, path, line, side, text, startLine) }) { r ->
            r.onSuccess {
                val n = NotificationGroupManager.getInstance().getNotificationGroup("keel")
                    .createNotification("Comment kept as pending", "$where · send it with Submit review", NotificationType.INFORMATION)
                n.addAction(NotificationAction.createSimpleExpiring("Show Pending") { showPending(project) })
                n.notify(project)
            }
            r.onFailure { KeelNotifier.error(project, "Could not keep the comment", KeelProjectService.say(it)) }
        }
    }

    fun showPending(project: Project) = KeelToolWindow.show(project, KeelToolWindow.REVIEW) { (it as? ReviewPanel)?.showPending() }
}
