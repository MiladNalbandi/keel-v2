package dev.keel.jetbrains.ui

import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.ui.JBColor
import com.intellij.ui.components.ActionLink
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.panels.VerticalLayout
import com.intellij.util.ui.JBFont
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import java.awt.BorderLayout
import java.awt.Color
import java.awt.Component
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.RenderingHints
import javax.swing.BorderFactory
import javax.swing.Box
import javax.swing.JComponent
import javax.swing.JPanel
import javax.swing.border.Border

/** keel's look inside the IDE: the IDE's own components and colours, keel purple only as an accent. */
object KeelUi {
    val ACCENT = JBColor(Color(0x7C5CE0), Color(0xA48BFF))
    val ACCENT_BG = JBColor(Color(0xF1EDFF), Color(0x2E2945))
    val OK = JBColor(Color(0x2E7D32), Color(0x6CC57C))
    val WARN = JBColor(Color(0xB26A00), Color(0xE0A84E))
    val BAD = JBColor(Color(0xC62828), Color(0xF07178))
    val ADDED_BG = JBColor(Color(0xE6F4EA), Color(0x2A3A2C))
    val REMOVED_BG = JBColor(Color(0xFCE8E6), Color(0x3F2A2A))
    val CARD_BG = JBColor(Color(0xF7F7F9), Color(0x2B2D30))
    val LINE = JBColor.border()

    /** A colour as #rrggbb for HTML styles (the current theme's side of a JBColor). */
    fun hex(c: java.awt.Color): String = String.format("#%02x%02x%02x", c.red, c.green, c.blue)

    fun muted(text: String): JBLabel = JBLabel(text).apply {
        foreground = UIUtil.getContextHelpForeground()
        font = JBFont.small()
    }

    fun bold(text: String): JBLabel = JBLabel(text).apply { font = JBFont.label().asBold() }

    fun title(text: String): JBLabel = JBLabel(text).apply { font = JBFont.h4().asBold() }

    /** A small coloured tag: "Waiting for you", "REVIEWER'S RIGHT", "done". */
    fun chip(text: String, fg: Color = ACCENT, bg: Color = ACCENT_BG): JBLabel = object : JBLabel(text) {
        override fun paintComponent(g: Graphics) {
            val g2 = g.create() as Graphics2D
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
            g2.color = bg
            g2.fillRoundRect(0, 0, width, height, JBUI.scale(8), JBUI.scale(8))
            g2.dispose()
            super.paintComponent(g)
        }
    }.apply {
        foreground = fg
        font = JBFont.small().asBold()
        border = JBUI.Borders.empty(1, 6)
        isOpaque = false
    }

    /** Text that wraps, can be selected and copied, and looks like a label. */
    fun prose(text: String): JBTextArea = JBTextArea(text).apply {
        isEditable = false
        lineWrap = true
        wrapStyleWord = true
        isOpaque = false
        border = JBUI.Borders.empty()
        font = JBFont.label()
    }

    fun link(text: String, action: () -> Unit): ActionLink = ActionLink(text) { action() }

    fun row(vararg parts: Component, gap: Int = 6): JPanel = JPanel(FlowLayout(FlowLayout.LEFT, JBUI.scale(gap), 0)).apply {
        isOpaque = false
        parts.forEach { add(it) }
    }

    /** A vertical stack whose children keep their own height and take the full width. */
    fun stack(): JPanel = object : JPanel(VerticalLayout(0)) {
        override fun getMaximumSize(): Dimension = Dimension(Int.MAX_VALUE, preferredSize.height)
    }.apply {
        isOpaque = false
    }

    /** Tags on the left, text that wraps on the right (a criterion, a verdict). */
    fun line(vararg left: Component, text: JComponent): JPanel = JPanel(BorderLayout(JBUI.scale(6), 0)).apply {
        isOpaque = false
        add(row(*left), BorderLayout.WEST)
        add(text, BorderLayout.CENTER)
    }

    /** A vertical stack for a scroll pane: it takes the viewport's width, so text in it wraps. */
    fun scrollStack(): JPanel = ScrollStack()

    private class ScrollStack : JPanel(VerticalLayout(0)), javax.swing.Scrollable {

        override fun getPreferredScrollableViewportSize(): Dimension = preferredSize
        override fun getScrollableUnitIncrement(r: java.awt.Rectangle, o: Int, d: Int) = JBUI.scale(16)
        override fun getScrollableBlockIncrement(r: java.awt.Rectangle, o: Int, d: Int) = r.height
        override fun getScrollableTracksViewportWidth() = true
        override fun getScrollableTracksViewportHeight() = false
    }

    fun JComponent.full(): JComponent = apply {
        alignmentX = Component.LEFT_ALIGNMENT
    }

    fun gap(h: Int = 6): Component = Box.createVerticalStrut(JBUI.scale(h))

    fun card(inner: JComponent, bg: Color = CARD_BG, edge: Color = LINE): JPanel = object : JPanel(BorderLayout()) {
        override fun getMaximumSize(): Dimension = Dimension(Int.MAX_VALUE, preferredSize.height)
    }.apply {
        background = bg
        border = BorderFactory.createCompoundBorder(roundedLine(edge), JBUI.Borders.empty(8, 10))
        add(inner, BorderLayout.CENTER)
        alignmentX = Component.LEFT_ALIGNMENT
    }

    fun roundedLine(c: Color): Border = JBUI.Borders.customLine(c, 1)

    /** An avatar: the initials in a coloured circle. */
    fun avatar(name: String, initials: String): JComponent = object : JComponent() {
        init {
            preferredSize = Dimension(JBUI.scale(22), JBUI.scale(22))
            minimumSize = preferredSize
            toolTipText = name
        }

        override fun paintComponent(g: Graphics) {
            val g2 = g.create() as Graphics2D
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
            g2.color = ACCENT_BG
            g2.fillOval(0, 0, width - 1, height - 1)
            g2.color = ACCENT
            g2.font = JBFont.small().asBold()
            val fm = g2.fontMetrics
            g2.drawString(initials, (width - fm.stringWidth(initials)) / 2, (height - fm.height) / 2 + fm.ascent)
            g2.dispose()
        }
    }

    fun esc(s: String): String = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    /** Opens a file of the project (a path keel gives, relative to the project root) at a 1-based line. */
    fun openFile(project: Project, path: String, line: Int?): Boolean {
        val base = project.basePath ?: return false
        val clean = path.removePrefix("./").removePrefix("/")
        val vf = LocalFileSystem.getInstance().refreshAndFindFileByPath("$base/$clean")
            ?: LocalFileSystem.getInstance().findFileByPath(path)
            ?: return false
        OpenFileDescriptor(project, vf, ((line ?: 1) - 1).coerceAtLeast(0), 0).navigate(true)
        return true
    }
}
