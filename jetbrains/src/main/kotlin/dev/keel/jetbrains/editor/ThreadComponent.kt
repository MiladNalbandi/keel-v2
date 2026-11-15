package dev.keel.jetbrains.editor

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import dev.keel.jetbrains.api.AiRun
import dev.keel.jetbrains.api.ReviewThread
import dev.keel.jetbrains.api.ThreadState2
import dev.keel.jetbrains.api.ago
import dev.keel.jetbrains.api.firstLine
import dev.keel.jetbrains.api.initials
import dev.keel.jetbrains.api.ktokens
import dev.keel.jetbrains.api.threadStateOf
import dev.keel.jetbrains.api.checkMeta
import dev.keel.jetbrains.api.fixNote
import dev.keel.jetbrains.api.samePointLabel
import dev.keel.jetbrains.api.sidesOf
import dev.keel.jetbrains.api.useOnAllLabel
import dev.keel.jetbrains.api.verdictLabel
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.ui.KeelNotifier
import dev.keel.jetbrains.ui.KeelUi
import dev.keel.jetbrains.ui.KeelUi.full
import java.awt.BorderLayout
import java.awt.Cursor
import java.awt.event.MouseAdapter
import java.awt.event.MouseEvent
import javax.swing.JButton
import javax.swing.JPanel

/**
 * A review thread inside the editor: a folded head (author · first line · status) that opens to the messages, keel's
 * "keel checked the code" card, and Reply / Is the reviewer right? / Address it / Resolve.
 */
class ThreadComponent(
    private val project: Project,
    private var thread: ReviewThread,
    /** null: open when it waits for you (the PR diff); false: folded (normal editors in review mode) */
    startOpen: Boolean? = null,
    private val onSize: (ThreadComponent) -> Unit,
) : JPanel(BorderLayout()) {
    private val session = ReviewSession.of(project)
    private var open = startOpen ?: (threadStateOf(thread, session.view?.me) == ThreadState2.WAITING)
    private var busy: String? = null
    private var note: String? = null
    private var draftReply: String = ""

    val threadId: String get() = thread.id

    init {
        isOpaque = false
        border = JBUI.Borders.empty(4, 0)
        render()
    }

    fun update(t: ReviewThread) {
        thread = t
        render()
    }

    fun render() {
        removeAll()
        val me = session.view?.me
        val state = threadStateOf(thread, me)
        // 0.26.1 who is right, by role and name ("Reviewer (bo) is right"), as the web says it
        val sides = sidesOf(me, session.view?.author, thread)
        val fix = fixNote(session.fixOf(thread.id), thread)
        val first = thread.comments.firstOrNull()
        val body = KeelUi.stack()
        val head = KeelUi.row(
            KeelUi.muted(if (open) "▾" else "▸"),
            KeelUi.bold(first?.author ?: "?"),
            KeelUi.muted("\"" + firstLine(first?.body.orEmpty(), 70) + "\""),
            if (thread.comments.size > 1) KeelUi.muted("· ${thread.comments.size - 1} repl${if (thread.comments.size == 2) "y" else "ies"}") else KeelUi.muted(""),
            session.judgeOf(thread.id)?.result?.items?.firstOrNull()?.let { KeelUi.muted("· keel: ${verdictLabel(it.verdict, sides)}") } ?: KeelUi.muted(""),
            fix?.takeIf { it.fixed }?.let { KeelUi.muted("· ${it.text.replaceFirstChar { c -> c.lowercase() }}") } ?: KeelUi.muted(""),
            stateChip(state),
        ).apply {
            cursor = Cursor.getPredefinedCursor(Cursor.HAND_CURSOR)
            addMouseListener(object : MouseAdapter() {
                override fun mouseClicked(e: MouseEvent) {
                    open = !open
                    render()
                }
            })
        }
        body.add(head.full())
        if (open) {
            thread.comments.forEach { c ->
                body.add(KeelUi.gap(6))
                val who = KeelUi.row(KeelUi.avatar(c.author, initials(c.author)), KeelUi.bold(c.author), KeelUi.muted(ago(c.at).let { if (it.isBlank()) "" else "· $it" }))
                body.add(who.full())
                body.add(KeelUi.prose(c.body).apply { border = JBUI.Borders.emptyLeft(28) }.full())
            }
            session.judgeOf(thread.id)?.let { body.add(KeelUi.gap(8)); body.add(judgeCard(it, sides).full()) }
            // keel's check of the author's "fixed" against the new code: it never resolves, Resolve is your click
            fix?.let { n ->
                body.add(KeelUi.gap(8))
                val text = KeelUi.bold(n.text).apply { foreground = if (n.fixed) KeelUi.OK else KeelUi.WARN }
                val resolve = if (n.fixed && session.view?.canPost == true && !thread.resolved) KeelUi.link("Resolve") { resolve(true) } else KeelUi.muted("")
                body.add(KeelUi.card(KeelUi.row(KeelUi.chip("k"), text, resolve, gap = 8), KeelUi.CARD_BG, if (n.fixed) KeelUi.OK else KeelUi.WARN).full())
            }
            body.add(KeelUi.gap(8))
            val actions = mutableListOf<java.awt.Component>(
                JButton("Reply").apply { addActionListener { reply(session.drafts[thread.id] ?: draftReply) } },
                KeelUi.link("✦ Is the reviewer right?") { judge(again = session.ai.judges.containsKey(thread.id)) },
                KeelUi.link("✦ Address it") { address() },
                KeelUi.link(if (thread.resolved) "Reopen" else "Resolve") { resolve(!thread.resolved) },
            )
            busy?.let { actions += KeelUi.muted(it) }
            note?.let { actions += KeelUi.muted(it) }
            body.add(KeelUi.row(*actions.toTypedArray(), gap = 10).full())
        }
        add(KeelUi.card(body, UIUtil.getPanelBackground(), if (state == ThreadState2.WAITING) KeelUi.ACCENT else KeelUi.LINE), BorderLayout.CENTER)
        revalidate()
        repaint()
        onSize(this)
    }

    private fun stateChip(s: ThreadState2) = when (s) {
        ThreadState2.WAITING -> KeelUi.chip(s.label)
        ThreadState2.OPEN -> KeelUi.chip(s.label, UIUtil.getLabelForeground(), KeelUi.CARD_BG)
        ThreadState2.RESOLVED -> KeelUi.chip(s.label, KeelUi.OK, KeelUi.CARD_BG)
    }

    private fun judgeCard(run: AiRun, sides: dev.keel.jetbrains.api.Sides): JPanel {
        val c = KeelUi.stack()
        val res = run.result
        val meta = listOfNotNull("keel checked the code", res?.let { checkMeta(it) }?.ifBlank { null }).joinToString(" · ")
        // what the check read (the thread's file, the files its comments name, their tests): the files in the tooltip
        val head = KeelUi.bold(meta).apply { res?.context?.paths?.takeIf { it.isNotEmpty() }?.let { toolTipText = "keel read:\n" + it.joinToString("\n") } }
        c.add(KeelUi.row(KeelUi.chip("k"), head, KeelUi.link("×") { session.dismiss(thread.id) }).full())
        when {
            run.status == "running" -> c.add(KeelUi.muted("keel reads the code…").full())
            run.error != null -> c.add(KeelUi.prose(run.error).apply { foreground = KeelUi.BAD }.full())
            res != null -> {
                res.items.forEach { item ->
                    c.add(KeelUi.gap(4))
                    val color = when (item.verdict) {
                        "reviewer_right" -> KeelUi.OK
                        "author_right" -> KeelUi.BAD
                        else -> KeelUi.WARN
                    }
                    c.add(KeelUi.line(KeelUi.chip(verdictLabel(item.verdict, sides), color, KeelUi.CARD_BG), text = KeelUi.prose(item.claim)).full())
                    if (item.why.isNotBlank()) c.add(KeelUi.prose(item.why).full())
                    item.fix?.takeIf { it.isNotBlank() }?.let { c.add(KeelUi.prose("Fix: $it").full()) }
                }
                res.text?.takeIf { res.items.isEmpty() && it.isNotBlank() }?.let { c.add(KeelUi.prose(it).full()) }
                // the same point in other threads: keel checked them as one (it never merges them on the code host)
                if (run.status == "done") res.sameAs.forEach { o ->
                    c.add(KeelUi.link("↳ " + samePointLabel(o, session.view?.me)) { o.path?.let { PrDiffEditor.open(project, it, o.line) } }.full())
                }
                c.add(KeelUi.gap(6))
                val onAll = res.reply?.takeIf { res.sameAs.isNotEmpty() && run.status == "done" && session.view?.canPost == true }?.let { text ->
                    KeelUi.link(useOnAllLabel(res.sameAs.size)) {
                        res.sameAs.forEach { session.drafts[it.threadId] = text }
                        reply(text)
                    }
                }
                c.add(
                    KeelUi.row(
                        KeelUi.link("Use as my reply") { reply(res.reply ?: res.items.joinToString("\n") { it.why }) },
                        onAll ?: KeelUi.muted(""),
                        KeelUi.link("Address it (a small flow)") { address() },
                        KeelUi.link("Think harder") { judge(again = true, harder = true) },
                        KeelUi.link("Dismiss") { session.dismiss(thread.id) },
                        gap = 10,
                    ).full(),
                )
            }
        }
        return KeelUi.card(c, KeelUi.ACCENT_BG, KeelUi.ACCENT)
    }

    private fun reply(start: String) {
        val text = Messages.showMultilineInputDialog(project, "Your reply to ${thread.comments.firstOrNull()?.author ?: "the reviewer"}:", "Reply", start, null, null)
            ?: return
        if (text.isBlank()) return
        draftReply = ""
        work("Sending…") { done ->
            session.act({ api, pid, key -> api.reply(pid, key, thread.id, text.trim()) }) {
                if (it == null) session.drafts.remove(thread.id)
                done(it, "Sent.")
            }
        }
    }

    private fun resolve(resolved: Boolean) =
        work(if (resolved) "Resolving…" else "Reopening…") { done -> session.act({ api, pid, key -> api.resolve(pid, key, thread.id, resolved) }) { done(it, null) } }

    private fun judge(again: Boolean, harder: Boolean = false) =
        work("keel reads the code…") { done -> session.judge(thread.id, again, harder) { done(it, null) } }

    private fun address() = work("Starting a small flow…") { done ->
        session.call({ api, pid, key -> api.address(pid, key, thread.id) }) { r ->
            r.onSuccess { KeelNotifier.info(project, "keel started a flow to address it", it.title.ifBlank { it.flowId }) }
            done(r.exceptionOrNull(), r.getOrNull()?.let { "A flow addresses it: ${it.title}" })
        }
    }

    private fun work(label: String, run: ((Throwable?, String?) -> Unit) -> Unit) {
        busy = label
        note = null
        render()
        run { e, ok ->
            busy = null
            note = if (e != null) KeelProjectService.say(e) else ok
            render()
        }
    }
}
