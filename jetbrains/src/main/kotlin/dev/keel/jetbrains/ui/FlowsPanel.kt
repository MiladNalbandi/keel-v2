package dev.keel.jetbrains.ui

import com.intellij.openapi.Disposable
import com.intellij.openapi.options.ShowSettingsUtil
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.ui.CollectionListModel
import com.intellij.ui.JBSplitter
import com.intellij.ui.components.JBList
import com.intellij.ui.components.JBScrollPane
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import dev.keel.jetbrains.api.FlowView
import dev.keel.jetbrains.api.GateForm
import dev.keel.jetbrains.api.RunRow
import dev.keel.jetbrains.api.STEP_NAMES
import dev.keel.jetbrains.api.ago
import dev.keel.jetbrains.api.stepOf
import dev.keel.jetbrains.api.usd
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.ui.KeelUi.full
import java.awt.BorderLayout
import java.awt.Component
import java.io.File
import javax.swing.JButton
import javax.swing.JList
import javax.swing.JPanel
import javax.swing.ListCellRenderer
import javax.swing.ListSelectionModel
import javax.swing.SwingUtilities

/** Flows: the project's flows, one flow's steps and criteria, and the question it waits at. */
class FlowsPanel(private val project: Project, parent: Disposable) : JPanel(BorderLayout()) {
    private val svc = KeelProjectService.of(project)
    private val model = CollectionListModel<RunRow>()
    private val list = JBList(model)
    private val detail = KeelUi.scrollStack().apply { border = JBUI.Borders.empty(10) }
    private val status = KeelUi.muted("")
    private var selected: String? = null
    private val life = parent
    private var activity: FlowActivity? = null
    private var pendingFocus = false
    private var pendingSpec = false
    private var loading = false

    init {
        val start = JButton("+ Start a flow").apply { addActionListener { StartFlowDialog.open(project, null) } }
        val top = KeelUi.row(start, KeelUi.link("Refresh") { reload() }, status).apply { border = JBUI.Borders.empty(6, 4) }
        add(top, BorderLayout.NORTH)
        list.selectionMode = ListSelectionModel.SINGLE_SELECTION
        list.cellRenderer = RunRenderer()
        list.emptyText.text = "No flows yet"
        list.addListSelectionListener {
            if (!it.valueIsAdjusting) list.selectedValue?.let { r -> if (r.threadId != selected) showFlow(r.threadId) }
        }
        // a click on the flow that is already selected opens it again (it may have failed to load, or show old state)
        list.addMouseListener(object : java.awt.event.MouseAdapter() {
            override fun mouseClicked(e: java.awt.event.MouseEvent) {
                val i = list.locationToIndex(e.point).takeIf { it >= 0 && list.getCellBounds(it, it)?.contains(e.point) == true } ?: return
                val r = model.getElementAt(i)
                if (r.threadId == selected) showFlow(r.threadId)
            }
        })
        val split = JBSplitter(true, 0.38f).apply {
            firstComponent = JBScrollPane(list)
            secondComponent = JBScrollPane(detail).apply { border = JBUI.Borders.empty(); verticalScrollBar.unitIncrement = JBUI.scale(16) }
        }
        add(split, BorderLayout.CENTER)
        svc.onChange(parent) { reload() }
        svc.onEvent(parent) { ev ->
            if (ev.threadId.isEmpty() || ev.threadId != selected) return@onEvent
            activity?.accept(ev)
            // the flow view (steps, criteria, the gate) again only when a step, a gate or the flow changed, not per agent step
            if (ev.type.startsWith("step.") || ev.type.startsWith("gate.") || ev.type.startsWith("thread.")) showFlow(ev.threadId, quiet = true)
        }
        reload()
    }

    /** Opens one flow (from a balloon, the status bar or Start a flow). */
    fun open(threadId: String, focusGate: Boolean, openSpec: Boolean) {
        pendingFocus = focusGate
        pendingSpec = openSpec
        showFlow(threadId)
        reload()
    }

    private fun reload() {
        val pid = svc.pid
        if (pid == null) {
            model.removeAll()
            status.text = ""
            if (waitingOpen == null) showEmpty()
            return
        }
        waitingOpen?.let { waitingOpen = null; showFlow(it) }
        if (loading) return
        loading = true
        svc.io({ it.runs(pid) }) { r ->
            loading = false
            r.onSuccess { rows ->
                val keep = selected
                model.replaceAll(rows)
                status.text = svc.keelProject?.name.orEmpty()
                val idx = rows.indexOfFirst { it.threadId == keep }
                if (idx >= 0) list.selectedIndex = idx
                if (keep == null && rows.isNotEmpty()) list.selectedIndex = rows.indexOfFirst { it.waiting != null }.coerceAtLeast(0)
            }.onFailure { status.text = KeelProjectService.say(it) }
        }
    }

    private fun showEmpty() {
        detail.removeAll()
        val e = svc.error
        if (e == null && !svc.connected) {
            detail.add(KeelUi.muted("Connecting to keel…").full())
            detail.revalidate()
            return
        }
        detail.add(KeelUi.title(if (e != null) "keel is not connected" else "This project is not one of keel's").full())
        detail.add(KeelUi.gap())
        detail.add(KeelUi.prose(if (e != null) KeelProjectService.say(e) else "Pick its keel project in Settings › Tools › keel.").full())
        detail.add(KeelUi.gap())
        detail.add(KeelUi.link("Open keel settings") { ShowSettingsUtil.getInstance().showSettingsDialog(project, "keel") }.full())
        detail.revalidate()
        detail.repaint()
    }

    private var waitingOpen: String? = null

    private fun showFlow(tid: String, quiet: Boolean = false) {
        selected = tid
        val pid = svc.pid ?: run {
            // keel is not connected yet: open it once the project is known
            waitingOpen = tid
            detail.removeAll()
            detail.add(KeelUi.muted("Connecting to keel…").full())
            detail.revalidate()
            detail.repaint()
            return
        }
        if (activity?.threadId != tid) {
            activity?.let { com.intellij.openapi.util.Disposer.dispose(it) }
            activity = FlowActivity(project, pid, tid, life)
        }
        if (!quiet) {
            detail.removeAll()
            detail.add(KeelUi.muted("Loading the flow…").full())
            detail.revalidate()
            detail.repaint()
        }
        svc.io({ it.flowOf(pid, tid) }) { r ->
            if (selected != tid) return@io
            r.onSuccess { if (it.thread != null) render(it) else if (!quiet) failed(tid, "keel has no flow $tid in this project any more.") }
                .onFailure { if (!quiet) failed(tid, KeelProjectService.say(it)) }
        }
    }

    private fun render(v: FlowView) {
        val t = v.thread ?: return
        val row = model.items.firstOrNull { it.threadId == t.threadId }
        detail.removeAll()
        val waits = t.waiting != null && t.status !in setOf("done", "failed", "stopped", "cancelled")
        val head = mutableListOf<Component>(statusChip(t.status, t.waiting != null))
        listOfNotNull(t.workflowId.ifBlank { null }, row?.where, row?.costUsd?.takeIf { it > 0 }?.let(::usd) ?: t.usage.costUsd.takeIf { it > 0 }?.let(::usd))
            .joinToString(" · ").takeIf { it.isNotBlank() }?.let { head += KeelUi.muted(it) }
        detail.add(KeelUi.row(*head.toTypedArray()).full())
        detail.add(KeelUi.gap(4))
        detail.add(KeelUi.title(t.title.ifBlank { t.threadId }).full())
        val w = t.waiting
        if (w != null && waits) {
            detail.add(KeelUi.gap(10))
            val gate = GatePanel(project, GateForm.of(t.threadId, w)) { said ->
                status.text = said
                showFlow(t.threadId)
                reload()
            }
            detail.add(gate.full())
            if (pendingFocus) {
                pendingFocus = false
                SwingUtilities.invokeLater { gate.scrollRectToVisible(gate.bounds) }
            }
        }
        detail.add(KeelUi.gap(8))
        detail.add(stepBar(stepOf(t.phase, t.status), t.status).full())
        t.error?.takeIf { it.isNotBlank() }?.let {
            detail.add(KeelUi.gap(6))
            detail.add(KeelUi.prose(it).apply { foreground = KeelUi.BAD }.full())
        }
        activity?.takeIf { it.threadId == t.threadId }?.let { a ->
            a.setStep(t.current?.let { c -> v.workflow?.steps?.firstOrNull { it.id == c }?.name ?: c } ?: t.phase.ifBlank { null })
            detail.add(KeelUi.gap(10))
            detail.add(a.full())
        }
        val links = mutableListOf<Component>()
        specFile(t.title)?.let { f -> links += KeelUi.link("Open the spec") { KeelUi.openFile(project, f, 1) } }
        row?.branch?.let { b -> links += KeelUi.muted("branch $b") }
        if (t.acs.isNotEmpty() || links.isNotEmpty()) {
            detail.add(KeelUi.gap(10))
            detail.add(KeelUi.row(KeelUi.bold(if (t.acs.isEmpty()) "Links" else "Acceptance criteria"), *links.toTypedArray(), gap = 12).full())
            t.acs.forEach { ac ->
                val mark = when (ac.status) {
                    "done", "green", "passed" -> KeelUi.chip("✓", KeelUi.OK, KeelUi.CARD_BG)
                    "red", "failed" -> KeelUi.chip("✗", KeelUi.BAD, KeelUi.CARD_BG)
                    "doing", "running", "active", "in_progress" -> KeelUi.chip("◆", KeelUi.ACCENT, KeelUi.ACCENT_BG)
                    else -> KeelUi.chip(" ", UIUtil.getContextHelpForeground(), KeelUi.CARD_BG)
                }
                detail.add(KeelUi.line(mark, KeelUi.bold(ac.id), KeelUi.muted(ac.layer.lowercase()), text = KeelUi.prose(ac.title)).full())
            }
        }
        detail.add(KeelUi.gap(10))
        val actions = mutableListOf<Component>()
        if (t.status in setOf("running", "waiting", "paused", "interrupted")) {
            actions += KeelUi.link("Stop the flow") { svc.io({ it.stop(t.threadId) }) { r -> after(r.exceptionOrNull(), "The flow stopped.", t.threadId) } }
        }
        if (t.status in setOf("stopped", "failed", "error", "cancelled")) {
            actions += KeelUi.link("Resume") {
                svc.io({ api ->
                    val cp = api.history(t.threadId).maxByOrNull { it.n } ?: error("This flow has no saved step to resume from.")
                    api.rewind(t.threadId, cp.id)
                }) { r -> after(r.exceptionOrNull(), "The flow goes on.", t.threadId) }
            }
        }
        if (t.updatedAt.isNotBlank()) actions += KeelUi.muted("updated ${ago(t.updatedAt)}")
        detail.add(KeelUi.row(*actions.toTypedArray(), gap = 12).full())
        val others = model.items.filter { it.threadId != t.threadId }.take(6)
        if (others.isNotEmpty()) {
            detail.add(KeelUi.gap(14))
            detail.add(KeelUi.muted("OTHER FLOWS").full())
            others.forEach { o ->
                detail.add(KeelUi.row(KeelUi.link(o.title.ifBlank { o.threadId }) { list.setSelectedValue(o, true) }, KeelUi.muted(runState(o) + (o.branch?.let { " · branch" } ?: ""))).full())
            }
        }
        if (pendingSpec) {
            pendingSpec = false
            specFile(t.title)?.let { KeelUi.openFile(project, it, 1) }
        }
        detail.revalidate()
        detail.repaint()
    }

    private fun failed(tid: String, why: String) {
        detail.removeAll()
        detail.add(KeelUi.bold("Could not open the flow").full())
        detail.add(KeelUi.prose(why).full())
        detail.add(KeelUi.gap(6))
        detail.add(KeelUi.link("Try again") { showFlow(tid) }.full())
        detail.revalidate()
        detail.repaint()
    }

    private fun after(e: Throwable?, ok: String, tid: String) {
        status.text = if (e == null) ok else KeelProjectService.say(e)
        showFlow(tid)
        reload()
    }

    /** `.keel/specs/<title as a slug>.md` when the project has it (the spec step writes it there). */
    private fun specFile(title: String): String? {
        val base = project.basePath ?: return null
        val slug = title.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-')
        val rel = ".keel/specs/$slug.md"
        return rel.takeIf { slug.isNotEmpty() && (File(base, rel).isFile || LocalFileSystem.getInstance().findFileByPath("$base/$rel") != null) }
    }

    private fun stepBar(current: Int, status: String): JPanel {
        val parts = mutableListOf<Component>()
        STEP_NAMES.forEachIndexed { i, name ->
            if (i > 0) parts += KeelUi.muted("→")
            parts += when {
                i < current || status == "done" -> KeelUi.chip("✓ $name", KeelUi.OK, KeelUi.CARD_BG)
                i == current -> KeelUi.chip("◆ $name")
                else -> KeelUi.muted(name)
            }
        }
        return KeelUi.row(*parts.toTypedArray(), gap = 4)
    }

    private fun statusChip(status: String, waits: Boolean) = when {
        waits -> KeelUi.chip("◆ Waits for you")
        status == "done" -> KeelUi.chip("done", KeelUi.OK, KeelUi.CARD_BG)
        status in setOf("failed", "error") -> KeelUi.chip(status, KeelUi.BAD, KeelUi.CARD_BG)
        else -> KeelUi.chip(status, UIUtil.getLabelForeground(), KeelUi.CARD_BG)
    }

    private fun runState(r: RunRow) = when {
        r.waiting != null -> "waits for you"
        else -> r.status
    }

    private inner class RunRenderer : ListCellRenderer<RunRow> {
        override fun getListCellRendererComponent(list: JList<out RunRow>, value: RunRow, index: Int, sel: Boolean, focus: Boolean): Component {
            val p = KeelUi.stack().apply {
                border = JBUI.Borders.empty(4, 8)
                isOpaque = true
                background = if (sel) UIUtil.getListSelectionBackground(focus) else UIUtil.getListBackground()
            }
            val title = KeelUi.bold(value.title.ifBlank { value.threadId }).apply { if (sel) foreground = UIUtil.getListSelectionForeground(focus) }
            p.add(KeelUi.row(statusChip(value.status, value.waiting != null), title).full())
            val sub = listOfNotNull(value.workflowId, value.where, value.phase?.ifBlank { null },
                if (value.acsTotal > 0) "${value.acsDone}/${value.acsTotal} criteria" else null,
                value.costUsd.takeIf { it > 0 }?.let(::usd), ago(value.updatedAt).ifBlank { null }).joinToString(" · ")
            p.add(KeelUi.muted(sub).apply { border = JBUI.Borders.emptyLeft(6) }.full())
            return p
        }
    }
}
