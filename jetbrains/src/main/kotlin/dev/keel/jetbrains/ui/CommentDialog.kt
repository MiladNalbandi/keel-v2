package dev.keel.jetbrains.ui

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.openapi.ui.ValidationInfo
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.ui.components.JBTextField
import com.intellij.util.ui.HTMLEditorKitBuilder
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import dev.keel.jetbrains.api.commentAskText
import dev.keel.jetbrains.api.markdownHtml
import dev.keel.jetbrains.api.withAnswer
import dev.keel.jetbrains.core.ReviewAsk
import kotlinx.coroutines.Job
import java.awt.BorderLayout
import java.awt.Dimension
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JEditorPane
import javax.swing.JPanel

/**
 * 0.36 The comment box of a review comment (Review tab, PR diff editor, review mode): your comment, and Ask KeelBot
 * about the lines it is on without leaving the box. The answer shows under it; "Use in my comment" puts it in the
 * comment, "Open the chat" shows the chat in KeelBot (the box stays open: it does not block the IDE). It asks in the PR's review chat (core/ReviewAsk), the one the review
 * cards use.
 *
 *   ┌ Comment ──────────────────────────────────────────┐
 *   │ A comment on refunds.py:12–18                     │
 *   │   │ if amount > refundable_amount(order):         │
 *   │ [your comment…                                  ] │
 *   │ [Ask about these lines…            ] [Ask KeelBot]│
 *   │ KeelBot: The check misses the coupon: …           │
 *   │ [Use in my comment]  Open the chat                │
 *   │                     [Cancel] [Add to my review]   │
 *   └───────────────────────────────────────────────────┘
 */
class CommentDialog(
    private val project: Project,
    private val where: String,
    private val code: String?,
    private val chatKey: String?,
    private val pr: Int?,
    private val onAdd: (String) -> Unit,
) : DialogWrapper(project, null, true, IdeModalityType.MODELESS) {
    private val comment = JBTextArea(5, 64).apply {
        lineWrap = true
        wrapStyleWord = true
        accessibleContext.accessibleName = "Your comment"
    }
    private val question = JBTextField().apply {
        emptyText.text = "Ask KeelBot about these lines… (empty: explain it and find problems)"
        accessibleContext.accessibleName = "Your question to KeelBot"
    }
    private val askButton = JButton("Ask KeelBot")
    private val status = KeelUi.prose("")
    private val answerPane = JEditorPane().apply {
        val kit = HTMLEditorKitBuilder.simple()
        val mono = com.intellij.openapi.editor.colors.EditorColorsManager.getInstance().globalScheme.editorFontName
        kit.styleSheet.addRule("body { font-family: ${UIUtil.getLabelFont().family}; font-size: ${JBUI.scale(12)}pt; margin: 0; }")
        kit.styleSheet.addRule("p { margin: 0 0 6px 0; } ul, ol { margin: 0 0 6px 18px; }")
        kit.styleSheet.addRule("code, pre { font-family: '$mono'; background-color: ${KeelUi.hex(KeelUi.CARD_BG)}; }")
        editorKit = kit
        isEditable = false
        isOpaque = false
        border = JBUI.Borders.empty()
    }
    private val answerScroll = JBScrollPane(answerPane).apply {
        preferredSize = Dimension(JBUI.scale(560), JBUI.scale(180))
        isVisible = false
    }
    private val useButton = JButton("Use in my comment").apply { isVisible = false }
    private val openLink = KeelUi.link("Open the chat") { openChat() }.apply { isVisible = false }
    private var answer: String? = null
    private var sid: String? = null
    private var job: Job? = null

    init {
        title = "Comment"
        setOKButtonText("Add to my review")
        askButton.addActionListener { ask() }
        question.addActionListener { ask() }
        useButton.addActionListener {
            answer?.let { comment.text = withAnswer(comment.text, it) }
            comment.requestFocusInWindow()
        }
        init()
    }

    override fun createCenterPanel(): JComponent {
        val body = KeelUi.stack()
        body.add(KeelUi.prose("A comment on $where. It stays pending (only you see it) until you press Submit review."))
        val quote = code?.lines()?.take(8)?.joinToString("\n") { "  │ $it" }
        if (!quote.isNullOrBlank()) {
            body.add(KeelUi.gap(4))
            body.add(KeelUi.prose(quote).apply { font = com.intellij.util.ui.JBFont.small() })
        }
        body.add(KeelUi.gap(6))
        body.add(JBScrollPane(comment))
        if (chatKey != null) {
            body.add(KeelUi.gap(8))
            body.add(JPanel(BorderLayout(JBUI.scale(6), 0)).apply {
                isOpaque = false
                add(question, BorderLayout.CENTER)
                add(askButton, BorderLayout.EAST)
            })
            body.add(KeelUi.gap(4))
            body.add(status)
            body.add(answerScroll)
            body.add(KeelUi.row(useButton, openLink))
        }
        return body
    }

    override fun getPreferredFocusedComponent(): JComponent = comment

    override fun doValidate(): ValidationInfo? = if (comment.text.isBlank()) ValidationInfo("Write your comment first.", comment) else null

    // the box does not block the IDE (Open the chat shows KeelBot next to it); Add to my review hands the comment on
    override fun doOKAction() {
        val t = comment.text.trim()
        super.doOKAction()
        if (t.isNotEmpty()) onAdd(t)
    }

    override fun dispose() {
        job?.cancel() // the answer still lands in the chat
        super.dispose()
    }

    private fun ask() {
        val key = chatKey ?: return
        if (job?.isActive == true) return
        askButton.isEnabled = false
        question.isEnabled = false
        useButton.isVisible = false
        status.text = "KeelBot reads the code…"
        val text = commentAskText(where, code, question.text, pr)
        job = ReviewAsk.ask(project, key, text, onChat = { sid = it; openLink.isVisible = true }) { s, done ->
            showAnswer(s)
            if (done) {
                answer = s
                status.text = ""
                useButton.isVisible = true
                askButton.isEnabled = true
                question.isEnabled = true
                question.text = ""
                question.requestFocusInWindow()
            }
        }
        if (job == null) {
            status.text = "No keel project is open."
            askButton.isEnabled = true
            question.isEnabled = true
        }
    }

    private fun showAnswer(md: String) {
        answerPane.text = "<html><body>${markdownHtml(md)}</body></html>"
        answerPane.caretPosition = 0
        if (!answerScroll.isVisible) {
            answerScroll.isVisible = true
            pack()
        }
        contentPanel.revalidate()
    }

    private fun openChat() {
        val s = sid ?: return
        KeelToolWindow.show(project, KeelToolWindow.KEELBOT) { (it as? KeelBotPanel)?.openSession(s) }
    }

    companion object {
        /** Shows the box; [onAdd] gets the comment when you press Add to my review. */
        fun open(project: Project, where: String, code: String?, chatKey: String?, pr: Int?, onAdd: (String) -> Unit) =
            CommentDialog(project, where, code, chatKey, pr, onAdd).show()
    }
}
