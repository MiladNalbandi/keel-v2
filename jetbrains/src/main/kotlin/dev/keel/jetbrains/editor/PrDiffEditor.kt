package dev.keel.jetbrains.editor

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.colors.ColorKey
import com.intellij.openapi.editor.colors.EditorFontType
import com.intellij.openapi.editor.ex.EditorEx
import com.intellij.openapi.editor.ex.util.EditorUtil
import com.intellij.openapi.editor.highlighter.EditorHighlighterFactory
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.editor.markup.TextAttributes
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.fileTypes.FileTypeManager
import com.intellij.openapi.fileTypes.PlainTextFileType
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.util.Key
import com.intellij.openapi.editor.TextAnnotationGutterProvider
import com.intellij.testFramework.LightVirtualFile
import dev.keel.jetbrains.api.DiffRow
import dev.keel.jetbrains.api.ReviewThread
import dev.keel.jetbrains.api.parseDiff
import dev.keel.jetbrains.api.threadOnLine
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.ui.KeelNotifier
import dev.keel.jetbrains.ui.KeelUi
import java.awt.Color

/**
 * A file of the open pull request as its diff, in an editor of its own: old and new line numbers in the gutter, added
 * and removed lines coloured, the code highlighted as its language, and the review threads between the lines.
 */
object PrDiffEditor {
    data class PrFile(val prKey: String, val path: String, val rows: List<DiffRow>)

    val KEY: Key<PrFile> = Key.create("keel.pr.file")
    private val DECORATED: Key<Boolean> = Key.create("keel.pr.decorated")
    private val files = HashMap<String, LightVirtualFile>()

    /** Opens [path] of the session's PR at a line (or the top). A line outside the diff opens the project's file. */
    fun open(project: Project, path: String, line: Int? = null, side: String = "RIGHT") {
        val session = ReviewSession.of(project)
        val prKey = session.key ?: return
        val pid = KeelProjectService.of(project).pid ?: return
        KeelProjectService.of(project).io({ api -> api.diff(pid, prKey, path) }) { r ->
            r.onFailure { KeelNotifier.error(project, "Could not read the diff of $path", KeelProjectService.say(it)) }
            r.onSuccess { fd ->
                if (fd.binary) {
                    KeelUi.openFile(project, path, line)
                    return@onSuccess
                }
                val rows = parseDiff(fd.diff)
                val target = line?.let { l -> rows.indexOfFirst { it is DiffRow.Line && if (side == "LEFT") it.old == l && it.mark != '+' else it.new == l && it.mark != '-' } } ?: 0
                if (line != null && target < 0 && KeelUi.openFile(project, path, line)) return@onSuccess
                val text = rows.joinToString("\n") {
                    when (it) {
                        is DiffRow.Hunk -> it.header
                        is DiffRow.Line -> it.text
                    }
                }
                val number = session.view?.number?.let { "#$it " } ?: ""
                val id = "$prKey|$path"
                val file = files[id]?.takeIf { it.content.toString() == text } ?: LightVirtualFile("$number${path.substringAfterLast('/')}", PlainTextFileType.INSTANCE, text).also {
                    it.isWritable = false
                    files[id] = it
                }
                file.putUserData(KEY, PrFile(prKey, path, rows))
                val editor = FileEditorManager.getInstance(project).openTextEditor(OpenFileDescriptor(project, file, target.coerceAtLeast(0), 0), true)
                if (editor is EditorEx) decorate(project, editor, PrFile(prKey, path, rows))
            }
        }
    }

    private fun decorate(project: Project, editor: EditorEx, pf: PrFile) {
        if (editor.getUserData(DECORATED) == true) return
        editor.putUserData(DECORATED, true)
        val life = Disposer.newDisposable("keel PR diff")
        EditorUtil.disposeWithEditor(editor, life)
        editor.settings.isLineNumbersShown = false
        editor.settings.isFoldingOutlineShown = false
        editor.settings.additionalLinesCount = 2
        val type = FileTypeManager.getInstance().getFileTypeByFileName(pf.path)
        editor.highlighter = EditorHighlighterFactory.getInstance().createEditorHighlighter(project, type)
        editor.gutter.registerTextAnnotation(Column(pf.rows) { (it as? DiffRow.Line)?.old?.toString() })
        editor.gutter.registerTextAnnotation(Column(pf.rows) { (it as? DiffRow.Line)?.new?.toString() })
        editor.gutter.registerTextAnnotation(Column(pf.rows) { r ->
            when ((r as? DiffRow.Line)?.mark) {
                '+' -> "+"
                '-' -> "−"
                else -> " "
            }
        })
        val markup = editor.markupModel
        pf.rows.forEachIndexed { i, r ->
            val bg: Color? = when {
                r is DiffRow.Hunk -> KeelUi.CARD_BG
                r is DiffRow.Line && r.mark == '+' -> KeelUi.ADDED_BG
                r is DiffRow.Line && r.mark == '-' -> KeelUi.REMOVED_BG
                else -> null
            }
            if (bg != null && i < editor.document.lineCount) {
                markup.addLineHighlighter(i, HighlighterLayer.SELECTION - 1, TextAttributes(null, bg, null, null, 0))
            }
        }
        val inlays = ComponentInlays(editor, life)
        val shown = HashMap<String, ThreadComponent>()
        val session = ReviewSession.of(project)
        fun sync() {
            if (editor.isDisposed || session.key != pf.prKey) return
            val threads = session.view?.threads.orEmpty().filter { it.path == pf.path }
            threads.forEach { t ->
                val have = shown[t.id]
                if (have != null) {
                    have.update(t)
                    return@forEach
                }
                val row = rowOf(pf.rows, t)
                val comp = ThreadComponent(project, t) { inlays.relayout(it) }
                val offset = if (row >= 0) editor.document.getLineEndOffset(row) else 0
                inlays.add(offset, comp, above = row < 0)
                shown[t.id] = comp
            }
        }
        session.onChange(life) { sync() }
        sync()
    }

    private fun rowOf(rows: List<DiffRow>, t: ReviewThread): Int = rows.indexOfFirst { it is DiffRow.Line && threadOnLine(t, it) }

    /** One gutter column: the old numbers, the new numbers, or the +/− marks. */
    private class Column(private val rows: List<DiffRow>, private val text: (DiffRow) -> String?) : TextAnnotationGutterProvider {
        override fun getLineText(line: Int, editor: Editor): String? = rows.getOrNull(line)?.let(text) ?: ""
        override fun getToolTip(line: Int, editor: Editor): String? = null
        override fun getStyle(line: Int, editor: Editor): EditorFontType = EditorFontType.PLAIN
        override fun getColor(line: Int, editor: Editor): ColorKey? = null
        override fun getBgColor(line: Int, editor: Editor): Color? = null
        override fun getPopupActions(line: Int, editor: Editor): List<com.intellij.openapi.actionSystem.AnAction> = emptyList()
        override fun gutterClosed() = Unit
    }
}

/** In a PR diff editor: a comment on the caret's line, kept as a pending comment of your review. */
class AddReviewCommentAction : AnAction("keel: Comment on This Line") {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = e.getData(CommonDataKeys.VIRTUAL_FILE)?.getUserData(PrDiffEditor.KEY) != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val editor = e.getData(CommonDataKeys.EDITOR) ?: return
        val pf = e.getData(CommonDataKeys.VIRTUAL_FILE)?.getUserData(PrDiffEditor.KEY) ?: return
        val sel = editor.selectionModel
        val endRow = if (sel.hasSelection()) editor.document.getLineNumber((sel.selectionEnd - 1).coerceAtLeast(sel.selectionStart)) else editor.caretModel.logicalPosition.line
        val row = pf.rows.getOrNull(endRow) as? DiffRow.Line ?: return
        val (line, side) = if (row.new != null) row.new to "RIGHT" else row.old!! to "LEFT"
        val startRow = if (sel.hasSelection()) editor.document.getLineNumber(sel.selectionStart) else endRow
        val start = (startRow until endRow).asSequence().mapNotNull { pf.rows.getOrNull(it) as? DiffRow.Line }
            .mapNotNull { if (side == "RIGHT") it.new else it.old }.firstOrNull()
        dev.keel.jetbrains.core.ReviewComments.add(project, pf.path, line, side, if (start != null) "${pf.path}:$start–$line" else "${pf.path}:$line", start, sel.selectedText)
    }
}
