package dev.keel.android.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** What a gate answer does: an answer bound to its question, or a stop of the whole flow. */
sealed class GateAction {
    data class Answer(val threadId: String, val body: InboxAnswer) : GateAction()
    data class Stop(val threadId: String) : GateAction()
}

/**
 * The answer form of a waiting question (the Question screen and the Flow screen's "what now?"), as the web builds it
 * (web Inbox.tsx / Gate.tsx): keel's named choices when it sends them; else Approve / Send back (and Stop the flow);
 * the questions of a clarify step with their options and free text. Pure, so the tests read it without Android.
 */
data class GateForm(
    val threadId: String,
    val waiting: Waiting,
    val rows: List<ChoiceInfo>,
    /** the rows are not keel's named choices but approve / reject / stop made here */
    val plain: Boolean,
    val picked: String?,
    val why: String = "",
    val qPicked: Map<String, List<String>> = emptyMap(),
    val qTyped: Map<String, String> = emptyMap(),
) {
    val questions: List<ClarifyQuestion> get() = waiting.questions.orEmpty()
    val pickedRow: ChoiceInfo? get() = rows.firstOrNull { it.name == picked }

    /** Whether to show the reason box, and its label. */
    val reasonLabel: String?
        get() {
            val r = pickedRow ?: return null
            return when {
                r.why == "none" -> null
                plain && r.name == REJECT && waiting.kind == "fix" -> "A note for keel (optional)"
                plain && r.name == REJECT -> "What should keel change?"
                r.why == "required" -> "Why? (needed)"
                else -> "A note for keel (optional)"
            }
        }

    fun pick(name: String) = copy(picked = name)
    fun setWhy(v: String) = copy(why = v)

    fun toggle(q: ClarifyQuestion, label: String): GateForm {
        val now = qPicked[q.id] ?: defaultPick(q)
        val next = if (q.multiple) (if (label in now) now - label else now + label) else listOf(label)
        return copy(qPicked = qPicked + (q.id to next))
    }

    fun type(q: ClarifyQuestion, text: String) = copy(qTyped = qTyped + (q.id to text))

    fun isPicked(q: ClarifyQuestion, label: String) = label in (qPicked[q.id] ?: defaultPick(q))

    /** Why it cannot be sent yet ("" = it can). */
    fun problem(): String {
        if (rows.isEmpty()) return ""
        return choiceProblem(pickedRow, why)
    }

    val sendLabel: String
        get() {
            val r = pickedRow
            return when {
                r == null -> if (questions.isNotEmpty()) "Send the answers" else "Choose one first"
                plain && r.name == APPROVE -> waiting.labels?.approve ?: r.label
                plain && r.name == REJECT -> if (waiting.kind == "fix") r.label else "Send it back"
                plain && r.name == STOP -> "Stop the flow"
                else -> r.label
            }
        }

    fun action(): GateAction {
        val r = pickedRow
        val reason = why.trim().ifEmpty { null }
        if (plain && r?.name == STOP) return GateAction.Stop(threadId)
        val answers = if (questions.isNotEmpty()) answersOf(questions, qPicked, qTyped) else null
        val payload = buildMap<String, kotlinx.serialization.json.JsonElement> {
            if (!plain && r != null) put("choice", JsonPrimitive(r.name))
            if (answers != null) put("answers", answers)
        }.takeIf { it.isNotEmpty() }?.let { JsonObject(it) }
        val decision = if (plain && r?.name == REJECT) REJECT else APPROVE
        return GateAction.Answer(threadId, InboxAnswer(decision = decision, id = waiting.id, why = reason, payload = payload))
    }

    companion object {
        const val APPROVE = "approve"
        const val REJECT = "reject"
        const val STOP = "__stop"

        fun of(threadId: String, w: Waiting, allowStop: Boolean = true): GateForm {
            val named = choiceRows(w.choices, w.choiceInfo)
            if (named.isNotEmpty()) {
                return GateForm(threadId, w, named, plain = false, picked = (named.firstOrNull { it.default } ?: named.first()).name)
            }
            if (w.kind == "fix" && w.labels?.approve == null && APPROVE in w.options && REJECT in w.options) {
                // a step that failed (the flow doctor) or got stuck: approve runs it again, reject stops the flow
                val rows = listOf(
                    ChoiceInfo(APPROVE, "Try again", "keel runs the step again. Fix what the message says first.", "none", true),
                    ChoiceInfo(REJECT, "Stop the flow", "The flow ends here. Its commits so far stay.", "optional"),
                )
                return GateForm(threadId, w, rows, plain = true, picked = APPROVE)
            }
            val rows = buildList {
                if (APPROVE in w.options) add(ChoiceInfo(APPROVE, w.labels?.approve ?: "Approve", "The flow goes on to its next step.", "none", true))
                if (REJECT in w.options) add(ChoiceInfo(REJECT, w.labels?.reject ?: "Send it back", "The step runs again, with your reason.", "required"))
                if (allowStop && isNotEmpty()) add(ChoiceInfo(STOP, "Stop the flow", "The flow ends here. Its work so far stays.", "none"))
            }
            return GateForm(threadId, w, rows, plain = true, picked = rows.firstOrNull()?.name)
        }
    }
}
