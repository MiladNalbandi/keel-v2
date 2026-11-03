package dev.keel.jetbrains.editor

import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.editor.markup.ActiveGutterRenderer
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.Inlay
import com.intellij.openapi.editor.colors.EditorFontType
import com.intellij.openapi.editor.ex.EditorEx
import com.intellij.openapi.editor.ex.util.EditorUtil
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.editor.markup.HighlighterTargetArea
import com.intellij.openapi.editor.markup.LineMarkerRendererEx
import com.intellij.openapi.editor.markup.RangeHighlighter
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.FileEditorManagerListener
import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.fileEditor.TextEditor
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.util.Key
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.testFramework.LightVirtualFile
import com.intellij.ui.awt.RelativePoint
import com.intellij.ui.components.JBTextArea
import com.intellij.util.ui.JBUI
import dev.keel.jetbrains.api.ChangeKind
import dev.keel.jetbrains.api.ChangeRange
import dev.keel.jetbrains.api.PrFileStatus
import dev.keel.jetbrains.api.askWhatChanged
import dev.keel.jetbrains.api.placeThreads
import dev.keel.jetbrains.api.rangeAt
import dev.keel.jetbrains.core.KeelConnection
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.core.ReviewMode
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.ui.KeelBotPanel
import dev.keel.jetbrains.ui.KeelNotifier
import dev.keel.jetbrains.ui.KeelToolWindow
import dev.keel.jetbrains.ui.KeelUi
import dev.keel.jetbrains.ui.KeelUi.full
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.Point
import java.awt.Polygon
import java.awt.Rectangle
import java.awt.RenderingHints
import java.awt.event.MouseEvent
import javax.swing.JComponent

/**
 * Review mode in the normal editors: the PR's changed lines in a gutter layer of their own (green added, blue
 * modified, a red ▶ where lines were removed; separate from the IDE's local-change marks), the review threads between
 * the lines (outdated ones as a note at the top), and on a changed block: Ask KeelBot what changed here (the answer in
 * a small card), Comment on this change, Show old, and Old | New (0.4.0: the whole file side by side).
 */
@Service(Service.Level.PROJECT)
class ReviewModeMarks(private val project: Project) : Disposable {
    private val marked = HashMap<EditorEx, Marked>()
    private val mode get() = ReviewMode.of(project)

    init {
        project.messageBus.connect(this).subscribe(FileEditorManagerListener.FILE_EDITOR_MANAGER, object : FileEditorManagerListener {
            override fun fileOpened(source: FileEditorManager, file: VirtualFile) {
                mode.relPath(file)?.let { mode.markViewed(it) }
                sync()
            }
        })
        mode.onChange(this) { sync() }
    }

    /** Brings every open editor in line with review mode (cheap when nothing changed). */
    fun sync() {
        if (!mode.active) {
            marked.values.toList().forEach { Disposer.dispose(it.life) }
            marked.clear()
            return
        }
        FileEditorManager.getInstance(project).allEditors.filterIsInstance<TextEditor>().forEach { te ->
            val file = te.file ?: return@forEach
            if (file is LightVirtualFile) return@forEach // the PR diff editor has its own threads
            val ed = te.editor as? EditorEx ?: return@forEach
            val path = mode.relPath(file) ?: return@forEach
            if (ed.isDisposed) return@forEach
            val m = marked[ed]?.takeIf { it.path == path } ?: Marked(ed, path).also { marked[ed] = it }
            m.sync()
        }
    }

    /** The block at the caret of [editor], for the editor menu actions. */
    fun rangeAtCaret(editor: Editor, file: VirtualFile?): Pair<String, ChangeRange>? {
        val path = file?.let { mode.relPath(it) } ?: return null
        val r = rangeAt(mode.ranges[path].orEmpty(), editor.caretModel.logicalPosition.line + 1) ?: return null
        return path to r
    }

    /** Next change ↓: the next block after the caret, then the next changed file's first block. */
    fun nextChange(editor: Editor?, file: VirtualFile?) {
        val order = mode.files.filter { (mode.statusOf(it.path) ?: PrFileStatus.MODIFIED) != PrFileStatus.DELETED }.map { it.path }
        val here = file?.let { mode.relPath(it) }
        if (here != null && editor != null) {
            val line = editor.caretModel.logicalPosition.line + 1
            mode.ranges[here].orEmpty().firstOrNull { it.start > line && !(it.kind == ChangeKind.DELETED && it.start == line + 1) }?.let {
                return go(here, it.start)
            }
        }
        val from = here?.let { order.indexOf(it) + 1 } ?: 0
        val next = (order.drop(from) + order.take(from)).firstOrNull { p -> p != here && mode.ranges[p].orEmpty().isNotEmpty() }
            ?: order.firstOrNull { mode.ranges[it].orEmpty().isNotEmpty() }
        if (next == null) {
            KeelNotifier.info(project, "No more changes in PR #${mode.number ?: ""}")
            return
        }
        go(next, mode.ranges[next].orEmpty().first().start)
    }

    private fun go(path: String, line: Int) {
        if (!KeelUi.openFile(project, path, line)) KeelNotifier.info(project, "$path is not in this project folder", "Check out the PR to review it in the editor.")
    }

    /** Ask KeelBot what changed here: a new Ask chat; the answer shows in a card under the block. */
    fun askWhatChanged(editor: Editor, path: String, r: ChangeRange) {
        val m = marked[editor as? EditorEx] ?: return
        m.askCard(r)
    }

    fun commentOn(path: String, r: ChangeRange) {
        val (line, side) = if (r.kind == ChangeKind.DELETED) r.oldEnd.coerceAtLeast(1) to "LEFT" else r.end to "RIGHT"
        val text = Messages.showMultilineInputDialog(project, "A comment on $path, ${r.label} (pending until you submit the review):", "Comment on this change", "", null, null)
            ?.trim()?.ifEmpty { null } ?: return
        ReviewSession.of(project).call({ api, pid, key -> api.addDraft(pid, key, path, line, side, text) }) { res ->
            res.onSuccess { KeelNotifier.info(project, "Comment kept for your review", "$path:$line · send it with Submit review") }
            res.onFailure { KeelNotifier.error(project, "Could not keep the comment", KeelProjectService.say(it)) }
        }
    }

    /** The old text of a block, with Ask / Comment / Show old. */
    fun showOld(editor: Editor, path: String, r: ChangeRange, at: RelativePoint? = null) {
        val pr = mode.number?.let { "PR #$it" } ?: "the PR"
        val box = KeelUi.stack().apply { border = JBUI.Borders.empty(8, 10) }
        box.add(KeelUi.muted(
            when (r.kind) {
                ChangeKind.DELETED -> "Removed in $pr (was line ${r.oldStart}${if (r.oldEnd > r.oldStart) "–${r.oldEnd}" else ""}):"
                ChangeKind.ADDED -> "Added in $pr: ${r.label}"
                ChangeKind.MODIFIED -> "Changed in $pr: ${r.label}. It was:"
            },
        ).full())
        if (r.oldLines.isNotEmpty()) {
            val old = r.oldLines.take(30).joinToString("\n") { "− $it" } + if (r.oldLines.size > 30) "\n…" else ""
            box.add(JBTextArea(old).apply {
                isEditable = false
                font = editor.colorsScheme.getFont(EditorFontType.PLAIN)
                background = KeelUi.REMOVED_BG
                border = JBUI.Borders.empty(4, 6)
            }.full())
        }
        var close: () -> Unit = {}
        box.add(KeelUi.gap(6))
        box.add(
            KeelUi.row(
                KeelUi.link("Ask KeelBot what changed here") { close(); askWhatChanged(editor, path, r) },
                KeelUi.link("Comment on this change") { close(); commentOn(path, r) },
                KeelUi.link("Show old") { close(); PrDiffEditor.open(project, path, if (r.kind == ChangeKind.DELETED) r.oldStart else r.start, if (r.kind == ChangeKind.DELETED) "LEFT" else "RIGHT") },
                KeelUi.link("Old | New") { close(); OldNewDiff.open(project, path) },
                gap = 10,
            ).full(),
        )
        val popup = JBPopupFactory.getInstance().createComponentPopupBuilder(box, null).setRequestFocus(true).setCancelOnClickOutside(true).createPopup()
        close = { popup.cancel() }
        if (at != null) {
            popup.show(at)
        } else {
            val line = (if (r.kind == ChangeKind.DELETED) r.start else r.end).coerceIn(1, editor.document.lineCount.coerceAtLeast(1))
            val p = editor.visualPositionToXY(editor.offsetToVisualPosition(editor.document.getLineEndOffset(line - 1)))
            popup.show(RelativePoint(editor.contentComponent, Point(p.x, p.y + editor.lineHeight)))
        }
    }

    /** One editor's marks: the gutter layer, the threads, KeelBot's answer cards. */
    private inner class Marked(val editor: EditorEx, val path: String) {
        val life: Disposable = Disposer.newDisposable("keel review mode")
        val inlays: ComponentInlays
        var shown: List<ChangeRange>? = null
        val highlighters = mutableListOf<RangeHighlighter>()
        val threads = HashMap<String, dev.keel.jetbrains.editor.ThreadComponent>()
        val cards = HashMap<String, Inlay<*>>()

        init {
            EditorUtil.disposeWithEditor(editor, life)
            Disposer.register(life) {
                marked.remove(editor)
                removeHighlighters()
            }
            inlays = ComponentInlays(editor, life)
        }

        fun sync() {
            val now = mode.ranges[path]
            if (now !== shown) {
                shown = now
                removeHighlighters()
                now.orEmpty().forEach { addHighlighter(it) }
            }
            val places = placeThreads(mode.threads(), path, editor.document.lineCount)
            places.inline.forEach { (line, t) -> place(t, line, outdated = false) }
            places.top.forEach { t -> place(t, 0, outdated = true) }
        }

        private fun place(t: dev.keel.jetbrains.api.ReviewThread, line: Int, outdated: Boolean) {
            threads[t.id]?.let { it.update(t); return }
            val comp = ThreadComponent(project, t, startOpen = false) { inlays.relayout(outer(it)) }
            val shownComp: JComponent = if (outdated) {
                KeelUi.stack().apply {
                    add(KeelUi.muted(if (t.side == "LEFT") "On a removed line (was line ${t.line ?: "?"}):" else "Outdated thread (was line ${t.line ?: "?"}):").full())
                    add(comp.full())
                    putClientProperty(OUTER, true)
                }
            } else {
                comp
            }
            threads[t.id] = comp
            val offset = if (outdated) 0 else editor.document.getLineEndOffset(line - 1)
            inlays.add(offset, shownComp, above = outdated)
        }

        private fun outer(c: JComponent): JComponent = (c.parent as? JComponent)?.takeIf { it.getClientProperty(OUTER) == true } ?: c

        private fun addHighlighter(r: ChangeRange) {
            val doc = editor.document
            if (doc.lineCount == 0) return
            val last = doc.lineCount - 1
            val (from, to) = if (r.kind == ChangeKind.DELETED) {
                val l = (r.start - 1).coerceIn(0, last)
                l to l
            } else {
                (r.start - 1).coerceIn(0, last) to (r.end - 1).coerceIn(0, last)
            }
            val h = editor.markupModel.addRangeHighlighter(doc.getLineStartOffset(from), doc.getLineEndOffset(to), HighlighterLayer.FIRST, null, HighlighterTargetArea.LINES_IN_RANGE)
            h.putUserData(MARK, r)
            h.lineMarkerRenderer = PrGutter(r, atEnd = r.kind == ChangeKind.DELETED && r.start - 1 > last)
            highlighters += h
        }

        private fun removeHighlighters() {
            highlighters.forEach { if (it.isValid) editor.markupModel.removeHighlighter(it) }
            highlighters.clear()
        }

        fun askCard(r: ChangeRange) {
            val key = "${r.kind}${r.start}"
            cards.remove(key)?.let { Disposer.dispose(it) }
            val svc = KeelProjectService.of(project)
            val pid = svc.pid ?: return
            val text = KeelUi.prose("KeelBot reads the change…")
            var sid: String? = null
            val head = KeelUi.row(KeelUi.chip("k"), KeelUi.bold("KeelBot · ${r.label}"), KeelUi.link("×") { cards.remove(key)?.let { Disposer.dispose(it) } })
            val open = KeelUi.link("Open in KeelBot") { sid?.let { s -> KeelToolWindow.show(project, KeelToolWindow.KEELBOT) { (it as? KeelBotPanel)?.openSession(s) } } }
            val body = KeelUi.stack().apply {
                add(head.full())
                add(text.full())
                add(KeelUi.gap(4))
                add(open.full())
            }
            val card = KeelUi.card(body, KeelUi.ACCENT_BG, KeelUi.ACCENT)
            val line = (if (r.kind == ChangeKind.DELETED) r.start - 1 else r.end).coerceIn(1, editor.document.lineCount.coerceAtLeast(1))
            val inlay = inlays.add(editor.document.getLineEndOffset(line - 1), card) ?: return
            cards[key] = inlay
            fun show(s: String) {
                text.text = s
                card.revalidate()
                inlays.relayout(card)
            }
            val ask = askWhatChanged(path, r, mode.number)
            svc.scope.launch {
                try {
                    val api = KeelConnection.get().ensure()
                    val chat = api.newChat(pid, "ask")
                    sid = chat.id
                    val turn = api.send(pid, chat.id, ask)
                    repeat(240) {
                        delay(1_500)
                        val partial = svc.live.liveText.value[turn.callId]
                        val s = api.chat(pid, chat.id)
                        val done = s.status != "running" && s.busy != true
                        val answer = s.messages.orEmpty().lastOrNull { it.role == "assistant" && it.n > turn.n }?.text ?: s.messages.orEmpty().lastOrNull { it.role == "assistant" }?.text
                        edt {
                            when {
                                done -> show(answer?.ifBlank { null } ?: s.error ?: "KeelBot gave no answer.")
                                !partial.isNullOrBlank() -> show(partial)
                            }
                        }
                        if (done) return@launch
                    }
                } catch (e: Exception) {
                    edt { show(KeelProjectService.say(e)) }
                }
            }
        }
    }

    /** The gutter layer: a bar for added (green) and modified (blue) lines, a red ▶ where lines were removed. */
    private inner class PrGutter(private val r: ChangeRange, private val atEnd: Boolean) : ActiveGutterRenderer, LineMarkerRendererEx {
        override fun paint(editor: Editor, g: Graphics, rr: Rectangle) {
            val g2 = g.create() as Graphics2D
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
            val w = JBUI.scale(3)
            when (r.kind) {
                ChangeKind.ADDED -> { g2.color = KeelUi.OK; g2.fillRect(rr.x, rr.y, w, rr.height) }
                ChangeKind.MODIFIED -> { g2.color = MODIFIED; g2.fillRect(rr.x, rr.y, w, rr.height) }
                ChangeKind.DELETED -> {
                    g2.color = KeelUi.BAD
                    val y = if (atEnd) rr.y + rr.height else rr.y
                    val s = JBUI.scale(4)
                    g2.fillPolygon(Polygon(intArrayOf(rr.x, rr.x + s + 2, rr.x), intArrayOf(y - s, y, y + s), 3))
                }
            }
            g2.dispose()
        }

        override fun getPosition(): LineMarkerRendererEx.Position = LineMarkerRendererEx.Position.LEFT

        override fun getTooltipText(): String = "changed in PR #${mode.number ?: ""} · ${r.label} (click: old text, Ask KeelBot, Comment)"

        override fun canDoAction(editor: Editor, e: MouseEvent): Boolean = true

        override fun doAction(editor: Editor, e: MouseEvent) {
            val file = FileEditorManager.getInstance(project).selectedFiles.firstOrNull { f ->
                FileEditorManager.getInstance(project).getEditors(f).any { (it as? TextEditor)?.editor === editor }
            } ?: (editor as? EditorEx)?.virtualFile
            val path = file?.let { mode.relPath(it) } ?: return
            e.consume()
            showOld(editor, path, r, RelativePoint(e))
        }

        override fun getAccessibleName(): String = "Changed in PR: ${r.label}"
    }

    private fun edt(block: () -> Unit) = ApplicationManager.getApplication().invokeLater({ if (!project.isDisposed) block() }, com.intellij.openapi.application.ModalityState.any())

    /** How many review-mode gutter marks an editor has (tests and the status line). */
    fun markCount(editor: Editor): Int = editor.markupModel.allHighlighters.count { it.getUserData(MARK) != null }

    override fun dispose() {
        marked.values.toList().forEach { Disposer.dispose(it.life) }
        marked.clear()
    }

    companion object {
        val MARK: Key<ChangeRange> = Key.create("keel.review.mark")
        private const val OUTER = "keel.review.outer"
        val MODIFIED = com.intellij.ui.JBColor(java.awt.Color(0x2F6FDB), java.awt.Color(0x5C9DFF))

        fun of(project: Project): ReviewModeMarks = project.service()
    }
}
