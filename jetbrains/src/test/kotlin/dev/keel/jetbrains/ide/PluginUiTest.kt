package dev.keel.jetbrains.ide

import com.intellij.openapi.editor.ex.EditorEx
import com.intellij.openapi.util.Disposer
import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import dev.keel.jetbrains.api.ChoiceInfo
import dev.keel.jetbrains.api.ClarifyQuestion
import dev.keel.jetbrains.api.GateForm
import dev.keel.jetbrains.api.QuestionOption
import dev.keel.jetbrains.api.ReviewComment
import dev.keel.jetbrains.api.ReviewThread
import dev.keel.jetbrains.api.AiRun
import dev.keel.jetbrains.api.AiState
import dev.keel.jetbrains.api.FixResult
import dev.keel.jetbrains.api.FixRun
import dev.keel.jetbrains.api.JudgeContext
import dev.keel.jetbrains.api.JudgeItem
import dev.keel.jetbrains.api.JudgeResult
import dev.keel.jetbrains.api.ReviewView
import dev.keel.jetbrains.api.SameAs
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.api.Waiting
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.editor.ComponentInlays
import dev.keel.jetbrains.editor.ThreadComponent
import dev.keel.jetbrains.settings.KeelConfigurable
import dev.keel.jetbrains.settings.KeelSettings
import dev.keel.jetbrains.ui.FlowsPanel
import dev.keel.jetbrains.ui.GatePanel
import dev.keel.jetbrains.ui.KeelBotPanel
import dev.keel.jetbrains.ui.ReviewPanel
import dev.keel.jetbrains.ui.StartFlowDialog

/**
 * The plugin inside a headless IDE: its services and extensions load from plugin.xml, the panels build, a gate form
 * renders keel's choices, and a review thread sits between the lines of an editor. keel itself is not reached (the
 * address is a closed port).
 */
class PluginUiTest : BasePlatformTestCase() {
    override fun setUp() {
        super.setUp()
        KeelSettings.get().state.url = "http://127.0.0.1:9"
        KeelSettings.get().state.mode = KeelSettings.MODE_PAIRED
    }

    fun `test the tool window panels build without keel`() {
        val d = Disposer.newDisposable()
        try {
            FlowsPanel(project, d)
            ReviewPanel(project, d)
            KeelBotPanel(project, d)
            KeelProjectService.of(project).start()
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
        } finally {
            Disposer.dispose(d)
        }
    }

    fun `test a gate shows keel's named choices and the clarify questions`() {
        val w = Waiting(
            title = "Approve the spec?",
            id = "q-1",
            choices = listOf("approve", "change"),
            choiceInfo = listOf(
                ChoiceInfo("approve", "Approve", "The test step starts.", "none", true),
                ChoiceInfo("change", "Change it", "keel rewrites the spec and asks again.", "required"),
            ),
            questions = listOf(ClarifyQuestion("q1", "Partial refunds too?", options = listOf(QuestionOption("Yes", recommended = true), QuestionOption("No")))),
        )
        val panel = GatePanel(project, GateForm.of("t1", w)) {}
        panel.setSize(500, 800)
        panel.doLayout()
        assertTrue(panel.preferredSize.height > 100)
    }

    fun `test a review thread sits between the lines of an editor`() {
        myFixture.configureByText("Prefs.kt", "class Prefs {\n    private val cache = mutableMapOf<String, Int>()\n}\n")
        val editor = myFixture.editor as EditorEx
        val d = Disposer.newDisposable()
        try {
            val inlays = ComponentInlays(editor, d)
            val t = ReviewThread("T1", "src/Prefs.kt", 2, comments = listOf(ReviewComment("c1", "bo", "This is a plain map. Should it be a ConcurrentHashMap?")))
            val c = ThreadComponent(project, t) { inlays.relayout(it) }
            assertNotNull(inlays.add(editor.document.getLineEndOffset(1), c))
            assertEquals(1, editor.inlayModel.getBlockElementsInRange(0, editor.document.textLength).size)
            assertSame(editor.contentComponent, c.parent)
            inlays.clear()
            assertEquals(0, editor.inlayModel.getBlockElementsInRange(0, editor.document.textLength).size)
        } finally {
            Disposer.dispose(d)
        }
    }

    fun `test a thread shows keel's 0_26_1 check - who is right by name, the same point, what it read, and the fix`() {
        val a = ReviewThread("T1", "src/Prefs.kt", 2, comments = listOf(ReviewComment("c1", "bo", "This is a plain map. Should it be a ConcurrentHashMap?")))
        val b = ReviewThread("T2", "src/Prefs.kt", 4, comments = listOf(ReviewComment("c2", "alex", "Two saves at once can lose a write here.")))
        val c = ReviewThread("T3", "src/Page.kt", 9, comments = listOf(ReviewComment("c3", "bo", "Read the max from config."), ReviewComment("c4", "ana", "Fixed.")))
        val check = JudgeResult(
            "done", listOf(JudgeItem("a plain map loses writes", "reviewer_right", "savePageSize runs on two threads.", "ConcurrentHashMap<String, Int>()")),
            reply = "Good catch: it is a ConcurrentHashMap now.", model = "claude · sonnet", tokens = 3_400,
            sameAs = listOf(SameAs("T2", "src/Prefs.kt", 4, "alex")), context = JudgeContext(60, 14, 3, listOf("src/Prefs.kt", "src/PrefsTest.kt", "src/Page.kt")),
        )
        ReviewSession.of(project).showForTest(
            ReviewView("pr:7", author = "ana", me = "ana", canPost = true, threads = listOf(a, b, c), threadGroups = listOf(listOf("T1", "T2"))),
            AiState(
                judges = mapOf("T1" to AiRun("j1", "judge", "done", result = check)),
                fixed = mapOf("T3" to FixRun("f1", "done", headSha = "9f3c2e1aa", result = FixResult("done", "T3", fixed = true, sha = "9f3c2e1"))),
            ),
        )
        val one = ThreadComponent(project, a, startOpen = true) {}
        val fixed = ThreadComponent(project, c, startOpen = true) {}
        val words = texts(one)
        assertTrue(words.toString(), words.any { it == "· keel: Reviewer (bo) is right" })
        assertTrue(words.toString(), words.any { it == "Reviewer (bo) is right" })
        assertTrue(words.toString(), words.any { it == "↳ Same point as the thread on line 4 (alex)" })
        assertTrue(words.toString(), words.any { it == "Use as my reply on both" })
        assertTrue(words.toString(), words.any { it.startsWith("keel checked the code · ") && it.contains("read 3 files") })
        assertTrue(tips(one).any { it.contains("src/PrefsTest.kt") })
        val fixWords = texts(fixed)
        assertTrue(fixWords.toString(), fixWords.containsAll(listOf("Fixed in 9f3c2e1", "Resolve", "· fixed in 9f3c2e1")))
        // a picture of both, for a person to look at (KEEL_UI_SHOT names a folder)
        System.getenv("KEEL_UI_SHOT")?.let { dir ->
            listOf("thread-check" to one, "thread-fixed" to fixed).forEach { (name, comp) ->
                comp.setSize(720, 10)
                comp.setSize(720, comp.preferredSize.height)
                layoutAll(comp)
                val img = java.awt.image.BufferedImage(comp.width, comp.height, java.awt.image.BufferedImage.TYPE_INT_RGB)
                val g = img.createGraphics()
                g.color = com.intellij.util.ui.UIUtil.getPanelBackground()
                g.fillRect(0, 0, img.width, img.height)
                comp.printAll(g)
                g.dispose()
                javax.imageio.ImageIO.write(img, "png", java.io.File(dir, "$name.png"))
            }
        }
    }

    private fun texts(c: java.awt.Component): List<String> = buildList {
        when (c) {
            is javax.swing.JLabel -> add(c.text.orEmpty())
            is javax.swing.AbstractButton -> add(c.text.orEmpty())
            is javax.swing.text.JTextComponent -> add(c.text.orEmpty())
        }
        if (c is java.awt.Container) c.components.forEach { addAll(texts(it)) }
    }

    private fun tips(c: java.awt.Component): List<String> = buildList {
        (c as? javax.swing.JComponent)?.toolTipText?.let { add(it) }
        if (c is java.awt.Container) c.components.forEach { addAll(tips(it)) }
    }

    private fun layoutAll(c: java.awt.Component) {
        c.doLayout()
        if (c is java.awt.Container) c.components.forEach { layoutAll(it) }
    }

    fun `test the Start a flow dialog and the settings page build`() {
        val dialog = StartFlowDialog(project, StartFlowDialog.Prefill("Refunds for paid orders\n\nCode: orders.py:6-9", "the selection in orders.py"))
        try {
            assertNotNull(dialog.preferredFocusedComponent) // init() built the form without a DSL error
            assertEquals("Start a keel flow", dialog.title)
        } finally {
            Disposer.dispose(dialog.disposable)
        }
        val page = KeelConfigurable(project)
        assertNotNull(page.createComponent())
        assertFalse(page.isModified)
        page.disposeUIResources()
    }
}
