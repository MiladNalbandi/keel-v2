package dev.keel.jetbrains.ui

import com.intellij.openapi.Disposable
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.ComboBox
import com.intellij.openapi.ui.DialogBuilder
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.vfs.VirtualFileManager
import com.intellij.ui.CollectionListModel
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBRadioButton
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTabbedPane
import com.intellij.ui.components.JBTextArea
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import dev.keel.jetbrains.api.ChangedFile
import dev.keel.jetbrains.api.PrSummary
import dev.keel.jetbrains.api.ReviewThread
import dev.keel.jetbrains.api.ReviewView
import dev.keel.jetbrains.api.ThreadState2
import dev.keel.jetbrains.api.ago
import dev.keel.jetbrains.api.ciSummary
import dev.keel.jetbrains.api.firstLine
import dev.keel.jetbrains.api.threadStateOf
import dev.keel.jetbrains.api.verdictLabel
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.editor.PrDiffEditor
import dev.keel.jetbrains.ui.KeelUi.full
import java.awt.BorderLayout
import java.awt.CardLayout
import java.awt.Component
import java.awt.Dimension
import java.awt.event.MouseAdapter
import java.awt.event.MouseEvent
import java.io.File
import java.util.concurrent.TimeUnit
import javax.swing.ButtonGroup
import javax.swing.JButton
import javax.swing.JList
import javax.swing.JPanel
import javax.swing.JToggleButton
import javax.swing.ListCellRenderer
import javax.swing.ListSelectionModel

/** Review: the pull requests, then one PR with Changes / Threads / Pending / Overview. */
class ReviewPanel(private val project: Project, parent: Disposable) : JPanel(CardLayout()) {
    private val svc = KeelProjectService.of(project)
    private val session = ReviewSession.of(project)

    // the PR list
    private val prs = CollectionListModel<PrSummary>()
    private val prList = JBList(prs)
    private val filter = ComboBox(arrayOf("review", "mine", "all"))
    private val listNote = KeelUi.muted("")

    // one PR
    private val prTitle = KeelUi.title("")
    private val prSub = KeelUi.muted("")
    private val ci = KeelUi.muted("")
    private val tabs = JBTabbedPane()
    private val files = CollectionListModel<ChangedFile>()
    private val fileList = JBList(files)
    private val threads = CollectionListModel<ReviewThread>()
    private val threadList = JBList(threads)
    private val chips = mutableMapOf<ThreadState2, JToggleButton>()
    private var chip = ThreadState2.WAITING
    private val pending = KeelUi.scrollStack().apply { border = JBUI.Borders.empty(8) }
    private val overview = KeelUi.scrollStack().apply { border = JBUI.Borders.empty(8) }
    private val prNote = KeelUi.muted("")
    private val summary = KeelUi.muted("")
    private val markViewed = com.intellij.ui.components.JBCheckBox("Mark a file viewed when I open it")
    private val mode = dev.keel.jetbrains.core.ReviewMode.of(project)

    init {
        add(listCard(), LIST)
        add(prCard(), PR)
        svc.onChange(parent) { if (session.key == null) loadPrs() }
        session.onChange(parent) { if (session.key == null) show(LIST) else renderPr() }
        mode.onChange(parent) { fileList.repaint() }
        loadPrs()
    }

    private fun show(card: String) = (layout as CardLayout).show(this, card)

    // ------------------------------------------------------------------ list
    private fun listCard(): JPanel {
        val p = JPanel(BorderLayout())
        filter.renderer = com.intellij.ui.SimpleListCellRenderer.create("") {
            when (it) {
                "review" -> "Waiting for my review"
                "mine" -> "Mine"
                else -> "All open"
            }
        }
        filter.addActionListener { loadPrs() }
        p.add(KeelUi.row(KeelUi.bold("Pull requests"), filter, KeelUi.link("Refresh") { loadPrs() }, listNote).apply { border = JBUI.Borders.empty(6, 4) }, BorderLayout.NORTH)
        prList.selectionMode = ListSelectionModel.SINGLE_SELECTION
        prList.cellRenderer = PrRenderer()
        prList.emptyText.text = "No pull requests"
        prList.addMouseListener(object : MouseAdapter() {
            override fun mouseClicked(e: MouseEvent) {
                prList.selectedValue?.let { openPr(it.number) }
            }
        })
        p.add(JBScrollPane(prList), BorderLayout.CENTER)
        return p
    }

    private fun loadPrs() {
        val pid = svc.pid
        if (pid == null) {
            prs.removeAll()
            prList.emptyText.text = if (svc.error != null) KeelProjectService.say(svc.error) else "This project is not one of keel's (Settings › Tools › keel)"
            return
        }
        val f = filter.selectedItem as String
        svc.io({ it.prs(pid, f) }) { r ->
            r.onSuccess {
                prs.replaceAll(it.prs)
                listNote.text = it.note.orEmpty()
                prList.emptyText.text = "No pull requests"
            }.onFailure { prList.emptyText.text = KeelProjectService.say(it) }
        }
    }

    private fun openPr(n: Int) {
        session.open("pr:$n")
        mode.enter("pr:$n")
        prTitle.text = "#$n …"
        show(PR)
    }

    // ------------------------------------------------------------------ one PR
    private fun prCard(): JPanel {
        val p = JPanel(BorderLayout())
        val head = KeelUi.stack().apply { border = JBUI.Borders.empty(6, 8) }
        head.add(KeelUi.row(KeelUi.link("← Pull requests") { show(LIST); loadPrs() }, ci, KeelUi.link("Refresh") { session.reload(refresh = true) }).full())
        head.add(prTitle.full())
        head.add(prSub.full())
        val submit = JButton("Submit review").apply { addActionListener { submitReview(null) } }
        val approve = JButton("Approve").apply { addActionListener { submitReview("APPROVE") } }
        val checkout = JButton("Check out").apply { addActionListener { checkout() } }
        head.add(summary.full())
        val next = JButton("Next change ↓").apply { addActionListener { ReviewModeText.nextChange(project) } }
        head.add(KeelUi.row(submit, approve, checkout, next, prNote).full())
        markViewed.isSelected = dev.keel.jetbrains.settings.KeelSettings.get().state.markViewed
        markViewed.isOpaque = false
        markViewed.addActionListener { dev.keel.jetbrains.settings.KeelSettings.get().state.markViewed = markViewed.isSelected }
        head.add(KeelUi.row(markViewed, KeelUi.link("Exit review mode") { mode.exit() }).full())
        p.add(head, BorderLayout.NORTH)

        fileList.cellRenderer = FileRenderer()
        fileList.emptyText.text = "No changed files"
        fileList.addMouseListener(object : MouseAdapter() {
            override fun mouseClicked(e: MouseEvent) {
                val f = fileList.selectedValue ?: return
                openChanged(f, diff = e.clickCount > 1 && e.isAltDown)
            }
        })
        tabs.addTab("Changed files", JBScrollPane(fileList))

        val tp = JPanel(BorderLayout())
        val group = ButtonGroup()
        val chipRow = KeelUi.row()
        ThreadState2.entries.forEach { s ->
            val b = JToggleButton(s.label, s == chip).apply {
                addActionListener {
                    chip = s
                    fillThreads()
                }
            }
            group.add(b)
            chips[s] = b
            chipRow.add(b)
        }
        chipRow.border = JBUI.Borders.empty(4)
        tp.add(chipRow, BorderLayout.NORTH)
        threadList.cellRenderer = ThreadRenderer()
        threadList.emptyText.text = "No threads here"
        threadList.addMouseListener(object : MouseAdapter() {
            override fun mouseClicked(e: MouseEvent) {
                val t = threadList.selectedValue ?: return
                val path = t.path ?: return
                val inEditor = mode.active && !t.outdated && t.side != "LEFT" && t.line != null
                if (!inEditor || !KeelUi.openFile(project, path, t.line)) PrDiffEditor.open(project, path, t.line, t.side)
            }
        })
        tp.add(JBScrollPane(threadList), BorderLayout.CENTER)
        tabs.addTab("Threads", tp)
        tabs.addTab("Pending", JBScrollPane(pending).apply { border = JBUI.Borders.empty() })
        tabs.addTab("Overview", JBScrollPane(overview).apply { border = JBUI.Borders.empty() })
        tabs.selectedIndex = 1
        p.add(tabs, BorderLayout.CENTER)
        return p
    }

    private fun renderPr() {
        val v = session.view
        if (session.key == null) return
        if (v == null) {
            prNote.text = session.error.orEmpty()
            return
        }
        prTitle.text = "#${v.number ?: ""} ${v.title}"
        prSub.text = listOfNotNull(v.author, "${v.base} ← ${v.branch}", if (v.draft) "draft" else null).joinToString(" · ")
        ci.text = ciSummary(v.checks)
        prNote.text = session.error.orEmpty()
        files.replaceAll(v.files)
        val counts = v.threads.groupingBy { threadStateOf(it, v.me) }.eachCount()
        chips.forEach { (s, b) -> b.text = "${s.label} · ${counts[s] ?: 0}" }
        tabs.setTitleAt(0, "Changed files (${v.files.size})")
        summary.text = "+${v.added} −${v.removed} · ${v.files.size} file${if (v.files.size == 1) "" else "s"} · ${v.threads.size} thread${if (v.threads.size == 1) "" else "s"}" +
            if (mode.active && mode.prKey == v.key) " · ${mode.viewed.size} viewed" else ""
        tabs.setTitleAt(1, "Threads ${v.threads.size}")
        tabs.setTitleAt(2, if (v.drafts.isEmpty()) "Pending" else "Pending ${v.drafts.size}")
        fillThreads()
        fillPending(v)
        fillOverview(v)
    }

    private fun fillThreads() {
        val v = session.view ?: return
        threads.replaceAll(v.threads.filter { threadStateOf(it, v.me) == chip })
    }

    private fun fillPending(v: ReviewView) {
        pending.removeAll()
        if (v.drafts.isEmpty()) pending.add(KeelUi.muted("No pending comments. In a file's diff, right-click a line: keel: Comment on This Line.").full())
        v.drafts.forEach { d ->
            pending.add(KeelUi.row(KeelUi.bold("${d.path ?: "PR"}${d.line?.let { ":$it" } ?: ""}"), KeelUi.link("Delete") {
                session.call({ api, pid, _ -> api.deleteDraft(pid, d.id) })
            }).full())
            pending.add(KeelUi.prose(d.body).full())
            pending.add(KeelUi.gap(6))
        }
        if (v.drafts.isNotEmpty()) pending.add(KeelUi.row(JButton("Submit review").apply { addActionListener { submitReview(null) } }).full())
        pending.revalidate()
        pending.repaint()
    }

    private fun fillOverview(v: ReviewView) {
        overview.removeAll()
        overview.add(KeelUi.bold(v.title).full())
        v.url?.let { u -> overview.add(KeelUi.link("Open on the host") { com.intellij.ide.BrowserUtil.browse(u) }.full()) }
        overview.add(KeelUi.gap())
        overview.add(KeelUi.prose(v.body?.ifBlank { null } ?: "No description.").full())
        overview.add(KeelUi.gap())
        overview.add(KeelUi.muted("+${v.added} −${v.removed} in ${v.files.size} files").full())
        if (v.checks.isNotEmpty()) {
            overview.add(KeelUi.gap())
            overview.add(KeelUi.bold("Checks").full())
            v.checks.forEach { overview.add(KeelUi.muted("${it.state} · ${it.name}").full()) }
        }
        if (v.approved.isNotEmpty()) overview.add(KeelUi.muted("Approved by ${v.approved.joinToString()}").full())
        if (v.changesRequested.isNotEmpty()) overview.add(KeelUi.muted("Changes requested by ${v.changesRequested.joinToString()}").full())
        v.notes.forEach { overview.add(KeelUi.muted(it).full()) }
        overview.revalidate()
        overview.repaint()
    }

    private fun submitReview(fixed: String?) {
        val v = session.view ?: return
        var event = fixed ?: "COMMENT"
        var body = ""
        if (fixed == null) {
            val text = JBTextArea(5, 40).apply { lineWrap = true; wrapStyleWord = true }
            val comment = JBRadioButton("Comment", true)
            val changes = JBRadioButton("Request changes")
            val ok = JBRadioButton("Approve")
            ButtonGroup().apply { add(comment); add(changes); add(ok) }
            val form = KeelUi.stack()
            form.add(KeelUi.muted("${v.drafts.size} pending comment(s) go with it.").full())
            form.add(JBScrollPane(text).apply { preferredSize = Dimension(JBUI.scale(420), JBUI.scale(110)) }.full())
            form.add(KeelUi.row(comment, changes, ok).full())
            val b = DialogBuilder(project).title("Submit review").centerPanel(form)
            b.setPreferredFocusComponent(text)
            if (!b.showAndGet()) return
            event = when {
                changes.isSelected -> "REQUEST_CHANGES"
                ok.isSelected -> "APPROVE"
                else -> "COMMENT"
            }
            body = text.text.trim()
        } else if (Messages.showYesNoDialog(project, "Approve #${v.number}?", "Approve", null) != Messages.YES) {
            return
        }
        prNote.text = "Sending…"
        session.call({ api, pid, key -> api.submitReview(pid, key, event, body) }) { r ->
            prNote.text = r.fold({ "Sent (${it.posted} comment(s))." }, { KeelProjectService.say(it) })
        }
    }

    /** git fetch + switch to the PR's branch in the project folder, in the background. */
    private fun checkout() {
        val v = session.view ?: return
        val base = project.basePath ?: return
        val branch = v.branch.ifBlank { return }
        object : Task.Backgroundable(project, "Checking out $branch", true) {
            override fun run(indicator: ProgressIndicator) {
                val steps = listOf(listOf("git", "fetch", "origin", branch), listOf("git", "switch", branch))
                for (cmd in steps) {
                    indicator.text = cmd.joinToString(" ")
                    val p = ProcessBuilder(cmd).directory(File(base)).redirectErrorStream(true).start()
                    val out = p.inputStream.bufferedReader().readText()
                    if (!p.waitFor(120, TimeUnit.SECONDS) || p.exitValue() != 0) {
                        KeelNotifier.error(project, "Could not check out $branch", out.lines().lastOrNull { it.isNotBlank() } ?: cmd.joinToString(" "))
                        return
                    }
                }
                VirtualFileManager.getInstance().asyncRefresh(null)
                KeelNotifier.info(project, "On $branch now", "Review mode shows the PR's changes in the editor.")
                com.intellij.openapi.application.ApplicationManager.getApplication().invokeLater {
                    session.key?.let { k -> if (mode.prKey == k) mode.reload() else mode.enter(k) }
                }
            }
        }.queue()
    }

    /** A changed file: the normal file in review mode (deleted, or not in this folder: its diff). */
    private fun openChanged(f: ChangedFile, diff: Boolean) {
        val status = dev.keel.jetbrains.api.prFileStatusOf(f.status)
        if (diff || status == dev.keel.jetbrains.api.PrFileStatus.DELETED || f.binary || !KeelUi.openFile(project, f.path, mode.ranges[f.path]?.firstOrNull()?.start ?: 1)) {
            PrDiffEditor.open(project, f.path)
        }
        mode.markViewed(f.path)
    }

    private inner class FileRenderer : ListCellRenderer<ChangedFile> {
        override fun getListCellRendererComponent(list: JList<out ChangedFile>, value: ChangedFile, index: Int, sel: Boolean, focus: Boolean): Component {
            val st = dev.keel.jetbrains.api.prFileStatusOf(value.status)
            val p = KeelUi.row().apply {
                border = JBUI.Borders.empty(3, 6)
                isOpaque = true
                background = if (sel) UIUtil.getListSelectionBackground(focus) else UIUtil.getListBackground()
            }
            p.add(com.intellij.ui.components.JBLabel(st.mark).apply { foreground = ReviewModeText.color(st) })
            val name = com.intellij.ui.components.JBLabel(if (st == dev.keel.jetbrains.api.PrFileStatus.DELETED) "<html><s>${KeelUi.esc(value.path)}</s></html>" else value.path)
            if (sel) name.foreground = UIUtil.getListSelectionForeground(focus)
            p.add(name)
            val bits = listOfNotNull(
                ReviewModeText.fileThreads(mode, value.path),
                st.word.takeIf { st != dev.keel.jetbrains.api.PrFileStatus.MODIFIED },
                listOfNotNull(value.added.takeIf { it > 0 }?.let { "+$it" }, value.removed.takeIf { it > 0 }?.let { "−$it" }).joinToString(" ").ifBlank { null },
                "viewed ✓".takeIf { value.path in mode.viewed },
            )
            p.add(KeelUi.muted(bits.joinToString(" · ")))
            return p
        }
    }

    private inner class PrRenderer : ListCellRenderer<PrSummary> {
        override fun getListCellRendererComponent(list: JList<out PrSummary>, value: PrSummary, index: Int, sel: Boolean, focus: Boolean): Component {
            val p = KeelUi.stack().apply {
                border = JBUI.Borders.empty(4, 8)
                isOpaque = true
                background = if (sel) UIUtil.getListSelectionBackground(focus) else UIUtil.getListBackground()
            }
            p.add(KeelUi.row(KeelUi.bold("#${value.number} ${value.title}"), if (value.draft) KeelUi.chip("draft") else KeelUi.muted("")).full())
            p.add(KeelUi.muted("${value.author} · ${value.base} ← ${value.branch} · ${ago(value.updatedAt)}").full())
            return p
        }
    }

    private inner class ThreadRenderer : ListCellRenderer<ReviewThread> {
        override fun getListCellRendererComponent(list: JList<out ReviewThread>, value: ReviewThread, index: Int, sel: Boolean, focus: Boolean): Component {
            val p = KeelUi.stack().apply {
                border = JBUI.Borders.empty(4, 8)
                isOpaque = true
                background = if (sel) UIUtil.getListSelectionBackground(focus) else UIUtil.getListBackground()
            }
            val first = value.comments.firstOrNull()
            p.add(KeelUi.row(KeelUi.bold("${value.path ?: "PR"}${value.line?.let { " · L$it" } ?: ""}"), KeelUi.muted("${first?.author ?: ""} · ${firstLine(first?.body.orEmpty(), 50)}")).full())
            val state = threadStateOf(value, session.view?.me)
            val verdict = session.judgeOf(value.id)?.result?.items?.firstOrNull()?.verdict
            val sides = dev.keel.jetbrains.api.sidesOf(session.view?.me, session.view?.author, value)
            // 0.26.1 keel's check of an author's "fixed": "Fixed in abc1234" or "Still open: …"
            val fix = dev.keel.jetbrains.api.fixNote(session.fixOf(value.id), value)
            p.add(
                KeelUi.row(
                    KeelUi.chip(state.label),
                    verdict?.let { KeelUi.muted("keel: ${verdictLabel(it, sides)}") } ?: KeelUi.muted(""),
                    fix?.let { n -> KeelUi.muted(firstLine(n.text, 60)).apply { foreground = if (n.fixed) KeelUi.OK else KeelUi.WARN } } ?: KeelUi.muted(""),
                ).full(),
            )
            return p
        }
    }

    companion object {
        private const val LIST = "list"
        private const val PR = "pr"
    }
}
