package dev.keel.jetbrains.ui

import com.intellij.ide.projectView.PresentationData
import com.intellij.ide.projectView.ProjectViewNode
import com.intellij.ide.projectView.ProjectViewNodeDecorator
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.actionSystem.ex.ActionUtil
import com.intellij.openapi.fileEditor.FileEditor
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.TextEditor
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.openapi.wm.StatusBar
import com.intellij.openapi.wm.StatusBarWidget
import com.intellij.openapi.wm.StatusBarWidgetFactory
import com.intellij.ui.EditorNotificationPanel
import com.intellij.ui.EditorNotificationProvider
import com.intellij.ui.SimpleTextAttributes
import com.intellij.util.Consumer
import dev.keel.jetbrains.api.PrFileStatus
import dev.keel.jetbrains.core.ReviewMode
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.editor.OldNewDiff
import dev.keel.jetbrains.editor.ReviewModeMarks
import java.awt.Component
import java.awt.event.MouseEvent
import java.util.function.Function
import javax.swing.JComponent

/** The few words review mode shows in the status bar, the toolbar pill and the editor banner. */
object ReviewModeText {
    fun color(s: PrFileStatus) = when (s) {
        PrFileStatus.NEW -> KeelUi.OK
        PrFileStatus.MODIFIED, PrFileStatus.RENAMED -> ReviewModeMarks.MODIFIED
        PrFileStatus.DELETED -> KeelUi.BAD
    }

    fun pill(project: Project): String {
        val m = ReviewMode.of(project)
        val v = ReviewSession.of(project).view
        val branches = v?.let { " · ${it.base} ← ${it.branch}" }.orEmpty()
        return "Reviewing #${m.number ?: "?"}$branches"
    }

    fun threadsWait(n: Int) = when (n) {
        0 -> "no thread waits"
        1 -> "1 thread waits"
        else -> "$n threads wait"
    }

    fun fileThreads(m: ReviewMode, path: String): String? {
        val all = m.threadsOf(path).size
        val waits = m.waitingOf(path)
        return when {
            waits > 0 -> "$waits waits"
            all == 1 -> "1 thread"
            all > 1 -> "$all threads"
            else -> null
        }
    }

    /** Exit review mode / Next change ↓ / Open the Review tab, under [c]. */
    fun menu(project: Project, c: Component?, at: MouseEvent? = null) {
        val items = listOf("Exit review mode", "Next change ↓", "Open the Review tab")
        val popup = JBPopupFactory.getInstance().createPopupChooserBuilder(items).setItemChosenCallback { item ->
            when (item) {
                items[0] -> ReviewMode.of(project).exit()
                items[1] -> nextChange(project)
                else -> KeelToolWindow.show(project, KeelToolWindow.REVIEW)
            }
        }.createPopup()
        when {
            at != null -> popup.show(com.intellij.ui.awt.RelativePoint(at))
            c != null -> popup.showUnderneathOf(c)
            else -> popup.showCenteredInCurrentWindow(project)
        }
    }

    fun nextChange(project: Project) {
        val fem = FileEditorManager.getInstance(project)
        val editor = (fem.selectedEditor as? TextEditor)?.editor
        ReviewModeMarks.of(project).nextChange(editor, fem.selectedFiles.firstOrNull())
    }
}

/** Project view: ✚ new (green), ● changed (blue) after the name, which keeps the IDE's own VCS colour; thread counts. */
class ReviewModeDecorator : ProjectViewNodeDecorator {
    override fun decorate(node: ProjectViewNode<*>, data: PresentationData) {
        val project = node.project ?: return
        val m = project.getServiceIfCreated(ReviewMode::class.java) ?: return
        if (!m.active) return
        val file = node.virtualFile ?: return
        val path = m.relPath(file) ?: return
        val status = m.statusOf(path) ?: return
        if (data.coloredText.isEmpty()) {
            val fg = data.forcedTextForeground ?: node.fileStatus?.color
            data.addText(data.presentableText ?: file.name, SimpleTextAttributes(SimpleTextAttributes.STYLE_PLAIN, fg))
        }
        data.addText("  ${status.mark}", SimpleTextAttributes(SimpleTextAttributes.STYLE_BOLD, ReviewModeText.color(status)))
        ReviewModeText.fileThreads(m, path)?.let { data.addText("  $it", SimpleTextAttributes.GRAYED_SMALL_ATTRIBUTES) }
        if (path in m.viewed) data.addText("  ✓", SimpleTextAttributes.GRAYED_SMALL_ATTRIBUTES)
    }
}

/** Status bar: "Reviewing #7 · 5 files · 1 thread waits · Exit review mode". */
class KeelReviewWidget(private val project: Project) : StatusBarWidget, StatusBarWidget.TextPresentation {
    override fun ID(): String = ID
    override fun install(statusBar: StatusBar) = Unit
    override fun getPresentation(): StatusBarWidget.WidgetPresentation = this

    override fun getText(): String {
        val m = project.getServiceIfCreated(ReviewMode::class.java)?.takeIf { it.active } ?: return ""
        val n = m.files.size
        return "Reviewing #${m.number ?: "?"} · $n file${if (n == 1) "" else "s"} · ${ReviewModeText.threadsWait(m.waiting())} · Exit review mode"
    }

    override fun getTooltipText(): String = "keel review mode: click for Exit review mode, Next change, the Review tab"
    override fun getAlignment(): Float = 0f
    override fun getClickConsumer(): Consumer<MouseEvent> = Consumer { e ->
        if (ReviewMode.of(project).active) ReviewModeText.menu(project, null, e)
    }

    override fun dispose() = Unit

    companion object {
        const val ID = "keel.review"
    }
}

class KeelReviewWidgetFactory : StatusBarWidgetFactory {
    override fun getId(): String = KeelReviewWidget.ID
    override fun getDisplayName(): String = "keel review mode"
    override fun isAvailable(project: Project): Boolean = true
    override fun createWidget(project: Project): StatusBarWidget = KeelReviewWidget(project)
    override fun canBeEnabledOn(statusBar: StatusBar): Boolean = true
}

/** The title-bar pill (main toolbar): "Reviewing #7 · main ← feat/paging"; a click offers Exit review mode. */
class ReviewPillAction : AnAction(), DumbAware {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        val project = e.project
        val m = project?.getServiceIfCreated(ReviewMode::class.java)
        e.presentation.isEnabledAndVisible = m?.active == true
        if (m?.active == true) {
            e.presentation.text = ReviewModeText.pill(project)
            e.presentation.description = "keel review mode: Exit review mode, Next change, the Review tab"
            e.presentation.putClientProperty(ActionUtil.SHOW_TEXT_IN_TOOLBAR, true)
        }
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        ReviewModeText.menu(project, e.inputEvent?.component, e.inputEvent as? MouseEvent)
    }
}

/** A banner on each changed file: "Reviewing #7 · main ← feat/paging · 2 changes · 1 thread waits". */
class ReviewModeBanner : EditorNotificationProvider, DumbAware {
    override fun collectNotificationData(project: Project, file: VirtualFile): Function<in FileEditor, out JComponent?>? {
        val m = project.getServiceIfCreated(ReviewMode::class.java)?.takeIf { it.active } ?: return null
        val path = m.relPath(file) ?: return null
        val blocks = m.ranges[path]?.size
        return Function { fe ->
            EditorNotificationPanel(fe, EditorNotificationPanel.Status.Info).apply {
                text = listOfNotNull(
                    ReviewModeText.pill(project),
                    m.statusOf(path)?.let { "${it.mark} ${it.word}" },
                    blocks?.let { "$it change${if (it == 1) "" else "s"}" },
                    ReviewModeText.fileThreads(m, path),
                ).joinToString(" · ")
                createActionLabel("Next change ↓") { ReviewModeText.nextChange(project) }
                createActionLabel("Exit review mode") { m.exit() }
            }
        }
    }
}

/** Editor menu, on a changed block: Ask KeelBot what changed here. */
class AskWhatChangedAction : AnAction("Ask KeelBot What Changed Here"), DumbAware {
    override fun getActionUpdateThread() = ActionUpdateThread.EDT
    override fun update(e: AnActionEvent) {
        val project = e.project
        val editor = e.getData(CommonDataKeys.EDITOR)
        e.presentation.isEnabledAndVisible = project != null && editor != null &&
            project.getServiceIfCreated(ReviewMode::class.java)?.active == true &&
            ReviewModeMarks.of(project).rangeAtCaret(editor, e.getData(CommonDataKeys.VIRTUAL_FILE)) != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val editor = e.getData(CommonDataKeys.EDITOR) ?: return
        val (path, r) = ReviewModeMarks.of(project).rangeAtCaret(editor, e.getData(CommonDataKeys.VIRTUAL_FILE)) ?: return
        ReviewModeMarks.of(project).askWhatChanged(editor, path, r)
    }
}

/** Editor menu, on a changed block: Comment on this change (a pending comment of your review). */
class CommentOnChangeAction : AnAction("Comment on This Change"), DumbAware {
    override fun getActionUpdateThread() = ActionUpdateThread.EDT
    override fun update(e: AnActionEvent) {
        val project = e.project
        val editor = e.getData(CommonDataKeys.EDITOR)
        e.presentation.isEnabledAndVisible = project != null && editor != null &&
            project.getServiceIfCreated(ReviewMode::class.java)?.active == true &&
            ReviewModeMarks.of(project).rangeAtCaret(editor, e.getData(CommonDataKeys.VIRTUAL_FILE)) != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val editor = e.getData(CommonDataKeys.EDITOR) ?: return
        val (path, r) = ReviewModeMarks.of(project).rangeAtCaret(editor, e.getData(CommonDataKeys.VIRTUAL_FILE)) ?: return
        ReviewModeMarks.of(project).commentOn(path, r)
    }
}

/** Next change ↓ (also on a shortcut-free action, for Find Action and the keymap). */
class NextChangeAction : AnAction("Next PR Change"), DumbAware {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT
    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = e.project?.getServiceIfCreated(ReviewMode::class.java)?.active == true
    }

    override fun actionPerformed(e: AnActionEvent) {
        ReviewModeText.nextChange(e.project ?: return)
    }
}

/** 0.4.0 the file in the editor, whole, old next to new (OldNewDiff); only for a changed file of the review. */
class OldNewAction : AnAction("PR File: Old | New"), DumbAware {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT
    override fun update(e: AnActionEvent) {
        val mode = e.project?.getServiceIfCreated(ReviewMode::class.java)
        val file = e.getData(CommonDataKeys.VIRTUAL_FILE)
        e.presentation.isEnabledAndVisible = mode?.active == true && file != null && mode.relPath(file) != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val file = e.getData(CommonDataKeys.VIRTUAL_FILE) ?: return
        OldNewDiff.open(project, ReviewMode.of(project).relPath(file) ?: return)
    }
}

class ExitReviewModeAction : AnAction("Exit Review Mode"), DumbAware {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT
    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = e.project?.getServiceIfCreated(ReviewMode::class.java)?.active == true
    }

    override fun actionPerformed(e: AnActionEvent) {
        ReviewMode.of(e.project ?: return).exit()
    }
}
