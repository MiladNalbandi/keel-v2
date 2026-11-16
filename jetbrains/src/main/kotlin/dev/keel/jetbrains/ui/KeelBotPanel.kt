package dev.keel.jetbrains.ui

import dev.keel.jetbrains.api.markdownHtml

import com.intellij.openapi.Disposable
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.intellij.openapi.util.Disposer
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.util.ui.HTMLEditorKitBuilder
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import dev.keel.jetbrains.api.CatalogProvider
import dev.keel.jetbrains.api.HelperChange
import dev.keel.jetbrains.api.HelperMessage
import dev.keel.jetbrains.api.HelperSession
import dev.keel.jetbrains.api.JobSteps
import dev.keel.jetbrains.api.Model
import dev.keel.jetbrains.api.fileRefs
import dev.keel.jetbrains.api.ktokens
import dev.keel.jetbrains.api.shortModel
import dev.keel.jetbrains.api.toolLines
import dev.keel.jetbrains.api.toolSummary
import dev.keel.jetbrains.api.usd
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.editor.KeelBotMarks
import dev.keel.jetbrains.ui.KeelUi.full
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.awt.BorderLayout
import java.awt.event.KeyAdapter
import java.awt.event.KeyEvent
import javax.swing.ButtonGroup
import javax.swing.JButton
import javax.swing.JEditorPane
import javax.swing.JPanel
import javax.swing.JToggleButton
import javax.swing.SwingUtilities
import javax.swing.event.HyperlinkEvent

/** KeelBot: a chat with Ask / Fix / Edit, its model, tool log, answers with file links, and the files it changed. */
class KeelBotPanel(private val project: Project, parent: Disposable) : JPanel(BorderLayout()), Disposable {
    private val svc = KeelProjectService.of(project)
    private var session: HelperSession? = null
    private var changes: List<HelperChange> = emptyList()
    private val steps = HashMap<String, JobSteps>()
    private val openLogs = HashSet<String>()
    private var mode = "ask"
    private var sending = false
    private var poll: Job? = null
    private var liveJob: Job? = null
    private var catalog: List<CatalogProvider>? = null

    private val modes = linkedMapOf("ask" to JToggleButton("Ask"), "fix" to JToggleButton("Fix"), "edit" to JToggleButton("Edit"))
    private val modelChip = JButton("model")
    private val usage = KeelUi.muted("")
    private val messages = KeelUi.scrollStack().apply { border = JBUI.Borders.empty(8) }
    private val scroll = JBScrollPane(messages).apply { border = JBUI.Borders.empty() }
    private val changed = KeelUi.stack().apply { border = JBUI.Borders.empty(4, 8) }
    private val input = JBTextArea(3, 40).apply {
        lineWrap = true
        wrapStyleWord = true
        emptyText.text = "Message KeelBot (Ctrl+Enter sends)"
    }
    private val send = JButton("↑")
    private val note = KeelUi.muted("")

    private val busy get() = sending || session?.status == "running" || session?.busy == true

    init {
        Disposer.register(parent, this)
        val group = ButtonGroup()
        modes.forEach { (m, b) ->
            group.add(b)
            b.isSelected = m == mode
            b.addActionListener { setMode(m) }
        }
        modelChip.addActionListener { pickModel() }
        val top = KeelUi.row(*modes.values.toTypedArray(), modelChip, usage, KeelUi.link("New chat") { newChat() }, KeelUi.link("Chats") { pickChat() })
            .apply { border = JBUI.Borders.empty(6, 4) }
        add(top, BorderLayout.NORTH)
        add(scroll, BorderLayout.CENTER)
        send.addActionListener { if (busy) stop() else sendNow() }
        input.addKeyListener(object : KeyAdapter() {
            override fun keyPressed(e: KeyEvent) {
                if (e.keyCode == KeyEvent.VK_ENTER && (e.isControlDown || e.isMetaDown)) {
                    e.consume()
                    sendNow()
                }
            }
        })
        val at = JButton("@").apply {
            toolTipText = "Add the open file"
            addActionListener { mentionOpenFile() }
        }
        val bottom = KeelUi.stack()
        bottom.add(changed.full())
        val box = JPanel(BorderLayout()).apply {
            border = JBUI.Borders.empty(4, 8, 8, 8)
            add(JBScrollPane(input), BorderLayout.CENTER)
            add(KeelUi.stack().apply { add(send); add(at) }, BorderLayout.EAST)
            add(note, BorderLayout.SOUTH)
        }
        bottom.add(box.full())
        add(bottom, BorderLayout.SOUTH)
        svc.onChange(this) { if (session == null) load() }
        svc.onEvent(this) { ev ->
            if (ev.type.startsWith("helper") && session != null && !ev.type.endsWith(".delta") && !ev.type.endsWith(".step") && ev.type != "helper.tool") reloadChat()
        }
        liveJob = svc.scope.launch { svc.live.liveText.collect { SwingUtilities.invokeLater { if (busy) render() } } }
        load()
    }

    /** Puts text in the box (Ask KeelBot from the editor) and sends it when asked to. */
    fun prefill(text: String, sendIt: Boolean) {
        input.text = text
        input.requestFocusInWindow()
        input.caretPosition = input.text.length
        if (sendIt) sendNow()
    }

    /** Shows one chat (Open in KeelBot from review mode's answer card). */
    fun openSession(sid: String) {
        val pid = svc.pid ?: return
        svc.io({ it.chat(pid, sid) }) { r ->
            r.onSuccess {
                session = it
                mode = it.mode
                render()
                loadChanges()
                if (busy) watch()
            }.onFailure { note.text = KeelProjectService.say(it) }
        }
    }

    private fun load() {
        val pid = svc.pid ?: run {
            messages.removeAll()
            messages.add(KeelUi.prose(KeelProjectService.say(svc.error).ifBlank { "This project is not one of keel's (Settings › Tools › keel)." }).full())
            messages.revalidate()
            return
        }
        svc.io({ api -> api.chats(pid).maxByOrNull { it.updatedAt }?.let { api.chat(pid, it.id) } }) { r ->
            r.onSuccess { s ->
                session = s
                s?.let { mode = it.mode }
                render()
                if (s != null) loadChanges()
            }.onFailure { note.text = KeelProjectService.say(it) }
        }
    }

    private fun reloadChat() {
        val pid = svc.pid ?: return
        val sid = session?.id ?: return
        svc.io({ it.chat(pid, sid) }) { r ->
            r.onSuccess {
                val was = busy
                session = it
                render()
                if (was && !busy) loadChanges()
            }
        }
    }

    private fun loadChanges() {
        val pid = svc.pid ?: return
        val sid = session?.id ?: return
        svc.io({ it.changes(pid, sid) }) { r ->
            r.onSuccess {
                changes = it
                KeelBotMarks.of(project).update(sid, it)
                renderChanges()
            }
        }
    }

    private fun setMode(m: String) {
        mode = m
        val pid = svc.pid ?: return
        val sid = session?.id ?: return
        svc.io({ it.setChatMode(pid, sid, m) }) { r -> r.onSuccess { session = it; render() }.onFailure { note.text = KeelProjectService.say(it) } }
    }

    private fun newChat() {
        session = null
        changes = emptyList()
        steps.clear()
        render()
        renderChanges()
    }

    private fun pickChat() {
        val pid = svc.pid ?: return
        svc.io({ it.chats(pid) }) { r ->
            val list = r.getOrNull().orEmpty().sortedByDescending { it.updatedAt }
            if (list.isEmpty()) return@io
            JBPopupFactory.getInstance().createPopupChooserBuilder(list)
                .setRenderer(com.intellij.ui.SimpleListCellRenderer.create("") { "${it.title.ifBlank { it.id }} · ${it.mode}" })
                .setItemChosenCallback { s -> svc.io({ api -> api.chat(pid, s.id) }) { rr -> rr.onSuccess { session = it; mode = it.mode; render(); loadChanges() } } }
                .createPopup().showUnderneathOf(modelChip)
        }
    }

    private fun pickModel() {
        val pid = svc.pid ?: return
        val got = catalog
        if (got == null) {
            svc.io({ it.providerModels() }) { r -> r.onSuccess { catalog = it; pickModel() }.onFailure { note.text = KeelProjectService.say(it) } }
            return
        }
        val items = got.flatMap { p ->
            p.modes.flatMap { (pm, ms) -> ms.flatMap { cm -> p.effortsOf(cm.id, pm).ifEmpty { listOf("") }.map { e -> Model(p.id, pm, cm.id, e.ifBlank { null }) to "${p.label} · ${cm.label}${if (e.isNotBlank()) " · $e" else ""}" } } }
        }
        if (items.isEmpty()) return
        JBPopupFactory.getInstance().createPopupChooserBuilder(items.map { it.second })
            .setItemChosenCallback { label ->
                val model = items.first { it.second == label }.first
                val sid = session?.id
                if (sid == null) {
                    note.text = "Send a first message; then pick the model."
                    return@setItemChosenCallback
                }
                svc.io({ it.setChatModel(pid, sid, model) }) { r -> r.onSuccess { session = it; render() }.onFailure { note.text = KeelProjectService.say(it) } }
            }
            .createPopup().showUnderneathOf(modelChip)
    }

    private fun mentionOpenFile() {
        val vf = FileEditorManager.getInstance(project).selectedFiles.firstOrNull() ?: return
        val rel = project.basePath?.let { vf.path.removePrefix("$it/") } ?: vf.name
        input.insert("@$rel ", input.caretPosition)
        input.requestFocusInWindow()
    }

    private fun sendNow() {
        val text = input.text.trim()
        if (text.isEmpty() || busy) return
        val pid = svc.pid ?: return
        sending = true
        note.text = ""
        render()
        val m = mode
        svc.io({ api ->
            val s = session ?: api.newChat(pid, m)
            api.send(pid, s.id, text)
            api.chat(pid, s.id)
        }) { r ->
            sending = false
            r.onSuccess {
                input.text = ""
                session = it
                render()
                watch()
            }.onFailure {
                note.text = KeelProjectService.say(it)
                render()
            }
        }
    }

    /** While KeelBot works: read the chat again every 1.5 s (the live stream also tells), then its changes. */
    private fun watch() {
        poll?.cancel()
        val pid = svc.pid ?: return
        val sid = session?.id ?: return
        poll = svc.scope.launch {
            repeat(400) {
                delay(1_500)
                val s = runCatching { dev.keel.jetbrains.core.KeelConnection.get().ensure().chat(pid, sid) }.getOrNull() ?: return@repeat
                SwingUtilities.invokeLater {
                    session = s
                    render()
                }
                if (s.status != "running" && s.busy != true) {
                    SwingUtilities.invokeLater { loadChanges() }
                    return@launch
                }
            }
        }
    }

    private fun stop() {
        val pid = svc.pid ?: return
        val sid = session?.id ?: return
        svc.io({ it.stopChat(pid, sid) }) { r -> r.onSuccess { session = it; render() } }
    }

    private fun render() {
        val s = session
        modes.forEach { (m, b) -> b.isSelected = m == (s?.mode ?: mode) }
        modelChip.text = s?.model?.let { shortModel(it) }?.ifBlank { null }?.let { "$it ▾" } ?: "model ▾"
        usage.text = s?.let { "${ktokens(it.tokensIn + it.tokensOut)} · ${usd(it.costUsd)}" }.orEmpty()
        send.text = if (busy) "■" else "↑"
        send.toolTipText = if (busy) "Stop" else "Send (Ctrl+Enter)"
        messages.removeAll()
        val list = s?.messages.orEmpty()
        if (list.isEmpty()) {
            messages.add(KeelUi.title("KeelBot").full())
            messages.add(KeelUi.prose("Ask about the code, have it fix something, or edit files. Select code in the editor and press Ctrl+Alt+K to ask about it.").full())
        }
        list.forEach { messages.add(message(it).full()); messages.add(KeelUi.gap(8)) }
        val live = svc.live.liveText.value
        list.lastOrNull { it.callId != null }?.callId?.let { cid -> live[cid] }?.takeIf { busy && it.isNotBlank() }?.let {
            messages.add(answer(it).full())
        }
        if (busy) messages.add(KeelUi.muted("KeelBot works…").full())
        messages.revalidate()
        messages.repaint()
        SwingUtilities.invokeLater { scroll.verticalScrollBar.value = scroll.verticalScrollBar.maximum }
    }

    private fun message(m: HelperMessage): JPanel {
        val p = KeelUi.stack()
        if (m.role == "user") {
            p.add(KeelUi.card(KeelUi.prose(m.text), KeelUi.CARD_BG, KeelUi.LINE))
            return p
        }
        p.add(KeelUi.row(KeelUi.chip("k"), KeelUi.bold(if (m.role == "assistant") "KeelBot" else m.role)).full())
        m.callId?.let { cid ->
            val st = steps[cid]
            if (st == null && m.role == "assistant") loadSteps(cid)
            if (st != null && st.steps.isNotEmpty()) {
                val opened = cid in openLogs
                p.add(KeelUi.link("✓ ${toolSummary(st.steps)} ${if (opened) "▾" else "▸"}") {
                    if (!openLogs.remove(cid)) openLogs += cid
                    render()
                }.full())
                if (opened) p.add(KeelUi.prose(toolLines(st.steps)).apply { foreground = UIUtil.getContextHelpForeground() }.full())
            }
        }
        if (m.text.isNotBlank()) p.add(answer(m.text).full())
        m.data.files?.takeIf { it.isNotEmpty() }?.let { p.add(KeelUi.muted("changed: " + it.joinToString()).full()) }
        return p
    }

    private fun loadSteps(cid: String) {
        steps[cid] = JobSteps()
        svc.io({ it.jobSteps(cid) }) { r -> r.onSuccess { steps[cid] = it; if (it.steps.isNotEmpty()) render() } }
    }

    /** An answer as HTML (KeelBot's Markdown: code blocks, lists, bold, links); every path:line opens the file there. */
    private fun answer(text: String): JEditorPane = JEditorPane().apply {
        val kit = HTMLEditorKitBuilder.simple()
        val mono = com.intellij.openapi.editor.colors.EditorColorsManager.getInstance().globalScheme.editorFontName
        val code = KeelUi.hex(KeelUi.CARD_BG)
        val line = KeelUi.hex(KeelUi.LINE)
        val muted = KeelUi.hex(UIUtil.getContextHelpForeground())
        kit.styleSheet.addRule("body { font-family: ${UIUtil.getLabelFont().family}; font-size: ${JBUI.scale(13)}pt; margin: 0; }")
        kit.styleSheet.addRule("p { margin: 0 0 6px 0; } ul, ol { margin: 0 0 6px 18px; } li { margin: 0 0 2px 0; }")
        kit.styleSheet.addRule("h2, h3, h4, h5 { margin: 8px 0 4px 0; }")
        kit.styleSheet.addRule("code { font-family: '$mono'; background-color: $code; }")
        kit.styleSheet.addRule("pre { font-family: '$mono'; background-color: $code; border: 1px solid $line; padding: 6px 8px; margin: 0 0 8px 0; }")
        kit.styleSheet.addRule(".lang { color: $muted; font-size: ${JBUI.scale(10)}pt; margin: 4px 0 0 0; }")
        kit.styleSheet.addRule("blockquote { color: $muted; margin: 0 0 6px 8px; }")
        editorKit = kit
        isEditable = false
        isOpaque = false
        border = JBUI.Borders.empty()
        this.text = "<html><body>${markdownHtml(text)}</body></html>"
        addHyperlinkListener { e ->
            if (e.eventType != HyperlinkEvent.EventType.ACTIVATED) return@addHyperlinkListener
            val href = e.description
            if (!href.startsWith("open:")) return@addHyperlinkListener com.intellij.ide.BrowserUtil.browse(href)
            val d = href.removePrefix("open:")
            val ln = d.substringAfterLast(':').toIntOrNull()
            val path = d.substringBeforeLast(':')
            if (!KeelUi.openFile(project, path, ln)) note.text = "$path is not in this project folder."
        }
    }

    private fun renderChanges() {
        changed.removeAll()
        if (changes.isNotEmpty()) {
            changed.add(KeelUi.muted("CHANGED FILES").full())
            changes.forEach { c ->
                changed.add(
                    KeelUi.row(
                        KeelUi.link(c.path) { KeelUi.openFile(project, c.path, 1) },
                        KeelUi.muted("+${c.added} −${c.removed}"),
                        KeelUi.link("Diff") { KeelBotMarks.of(project).diff(c.path) },
                        KeelUi.link("Undo") { KeelBotMarks.of(project).undo(c.path); SwingUtilities.invokeLater { loadChanges() } },
                    ).full(),
                )
            }
            val done = JButton("Done: run the checks and commit").apply { addActionListener { keep() } }
            val undoAll = KeelUi.link("Undo all") { KeelBotMarks.of(project).undo(null); SwingUtilities.invokeLater { loadChanges() } }
            val flow = KeelUi.link("Start a small flow") {
                val ask = session?.messages?.lastOrNull { it.role == "user" }?.text.orEmpty()
                StartFlowDialog.open(project, StartFlowDialog.Prefill(ask, "KeelBot's chat"))
            }
            changed.add(KeelUi.row(done, undoAll, flow).full())
        }
        changed.revalidate()
        changed.repaint()
    }

    private fun keep() {
        val pid = svc.pid ?: return
        val sid = session?.id ?: return
        note.text = "keel runs the checks…"
        svc.io({ it.keep(pid, sid) }) { r ->
            r.onSuccess { d ->
                note.text = if (d.ok) "Committed ${d.sha?.take(8).orEmpty()} ${d.message.orEmpty()}".trim() else (d.error ?: "The checks did not pass.")
                loadChanges()
            }.onFailure { note.text = KeelProjectService.say(it) }
        }
    }

    override fun dispose() {
        poll?.cancel()
        liveJob?.cancel()
    }
}
