package dev.keel.jetbrains.ide

import com.intellij.testFramework.PlatformTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.openapi.util.Disposer
import dev.keel.jetbrains.api.AiState
import dev.keel.jetbrains.api.ChangedFile
import dev.keel.jetbrains.api.Draft
import dev.keel.jetbrains.api.ReviewView
import dev.keel.jetbrains.core.ReviewSession
import dev.keel.jetbrains.settings.KeelSettings
import dev.keel.jetbrains.ui.KeelBotPanel
import dev.keel.jetbrains.ui.ReviewPanel
import java.awt.Component
import java.awt.Container
import java.awt.image.BufferedImage
import java.io.File
import javax.imageio.ImageIO
import javax.swing.JComponent

/** Pictures of the panels with demo data, only when KEEL_SHOTS names a folder. */
class ShotsTest : BasePlatformTestCase() {
    private fun shot(c: JComponent, name: String, w: Int = 460, h: Int = 640) {
        val dir = System.getenv("KEEL_SHOTS") ?: return
        c.setSize(w, h)
        fun lay(x: Component) { if (x is Container) { x.doLayout(); x.components.forEach(::lay) } }
        repeat(3) { lay(c) }
        val img = BufferedImage(w, h, BufferedImage.TYPE_INT_RGB)
        val g = img.createGraphics()
        g.color = c.background ?: java.awt.Color.WHITE
        g.fillRect(0, 0, w, h)
        c.printAll(g)
        g.dispose()
        ImageIO.write(img, "png", File(dir, "$name.png"))
    }

    fun `test pictures`() {
        if (System.getenv("KEEL_SHOTS") == null) return
        KeelSettings.get().state.url = "http://127.0.0.1:9"
        val d = Disposer.newDisposable()
        try {
            val s = ReviewSession.of(project)
            ReviewSession::class.java.getDeclaredField("key").apply { isAccessible = true }.set(s, "pr:42")
            s.showForTest(ReviewView(
                key = "pr:42", number = 42, title = "Add a retry to the label printer", author = "demo-dev", base = "main", branch = "feat/label-retry",
                files = listOf(ChangedFile("src/printer/Retry.kt", "modified", 24, 6), ChangedFile("src/printer/Queue.kt", "added", 40, 0),
                    ChangedFile("src/printer/Old.kt", "removed", 0, 80), ChangedFile("src/label/Label.kt", "modified", 3, 9),
                    ChangedFile("src/label/Cancel.kt", "removed", 0, 65), ChangedFile("config/services.yaml", "modified", 0, 12), ChangedFile("README.md", "modified", 4, 4)), added = 64, removed = 6,
                drafts = listOf(
                    Draft("d1", "src/printer/Retry.kt", 18, body = "Should this wait grow each time? A fixed 2 s may hammer the printer."),
                    Draft("d2", "src/printer/Queue.kt", 7, body = "Nit: name this `pendingJobs`."),
                ),
            ), AiState())
            val rp = ReviewPanel(project, d)
            ReviewPanel::class.java.getDeclaredMethod("renderPr").apply { isAccessible = true }.invoke(rp)
            rp.showPending()
            val tabs = ReviewPanel::class.java.getDeclaredField("tabs").apply { isAccessible = true }.get(rp) as javax.swing.JTabbedPane
            tabs.selectedIndex = 0
            ReviewPanel::class.java.getDeclaredField("closedFolders").apply { isAccessible = true }.get(rp).let { @Suppress("UNCHECKED_CAST") (it as MutableSet<String>).add("src/label") }
            ReviewPanel::class.java.getDeclaredMethod("fillFiles").apply { isAccessible = true }.invoke(rp)
            val fl = ReviewPanel::class.java.getDeclaredField("fileList").apply { isAccessible = true }.get(rp) as javax.swing.JList<*>
            println("ROWS=" + fl.model.size)
            fl.fixedCellHeight = 24
            fl.invalidate()
            shot(rp, "review-files")
            @Suppress("UNCHECKED_CAST") val jl = fl as javax.swing.JList<Any>
            val stack = javax.swing.JPanel().apply { layout = javax.swing.BoxLayout(this, javax.swing.BoxLayout.Y_AXIS); background = java.awt.Color.WHITE }
            stack.add(javax.swing.JTextField("Search files (words, or *.php)").apply { maximumSize = java.awt.Dimension(460, 28) })
            (0 until jl.model.size).forEach { i ->
                val c = jl.cellRenderer.getListCellRendererComponent(jl, jl.model.getElementAt(i), i, i == 3, false) as JComponent
                c.alignmentX = 0f
                c.maximumSize = java.awt.Dimension(460, 26)
                stack.add(c)
            }
            shot(stack, "review-files-list", 460, 30 + 26 * jl.model.size)
            tabs.selectedIndex = 2
            PlatformTestUtil.dispatchAllEventsInIdeEventQueue()
            shot(rp, "review-pending")

            val kb = KeelBotPanel(project, d)
            val pane = KeelBotPanel::class.java.getDeclaredMethod("answer", String::class.java).apply { isAccessible = true }
                .invoke(kb, "## What this does\n\n`retry()` tries the print **3 times** before it gives up:\n\n- waits 2 s between tries\n- logs each failure, see src/printer/Retry.kt:18\n\n```kotlin\nfun retry(job: Job) = repeat(3) {\n    if (printer.send(job)) return\n    Thread.sleep(2_000)\n}\n```\n\nMore in [the docs](https://example.com/printer).") as JComponent
            pane.background = java.awt.Color.WHITE
            pane.isOpaque = true
            shot(pane, "keelbot-answer", 460, 360)
        } finally {
            Disposer.dispose(d)
        }
    }
}
