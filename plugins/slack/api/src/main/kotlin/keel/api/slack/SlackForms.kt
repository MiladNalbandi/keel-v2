package keel.api.slack

import com.fasterxml.jackson.databind.JsonNode

/**
 * The Slack modals: a gate's form with the same options as keel's Inbox, and the reason and one confirm of a gate that keel normally keeps.
 * No rule lives here. What a person sends goes through [keel.api.inbox.InboxService.act], the same check as the Inbox.
 *
 *     a gate with questions ──▶ fits? ──▶ a button ──▶ views.open(form) ──▶ view_submission ──▶ read ──▶ the Inbox's call
 *                                  └─ no: "answer it in keel"
 *
 * Slack's own limits decide what fits: a title of 24 characters, an option of 75, at most 100 options and 100 blocks. Option values are numbers,
 * so a long label never has to fit in one. Slack cannot add a box when an option is ticked, so a question whose options take a reason (a skip menu) has one box under it:
 * one line per ticked option, `name: reason`. keel's first form, the spec gate, is such a form.
 */
object SlackForms {
    const val MAX_QUESTIONS = 8
    const val MAX_OPTIONS = 100
    const val MAX_LABEL = 75
    const val TEXT_MAX = 1900
    const val FORM = "keel_form"
    const val KEPT = "keel_kept"
    private const val PICK = "pick"
    private const val TEXT = "text"

    private fun questions(w: JsonNode) = w.path("questions").takeIf { it.isArray }?.toList().orEmpty()

    fun hasForm(w: JsonNode) = questions(w).isNotEmpty()

    /** Can Slack show this form? No: the person answers it in keel. */
    fun fits(w: JsonNode): Boolean {
        val qs = questions(w)
        if (qs.isEmpty() || qs.size > MAX_QUESTIONS) return false
        val choices = choices(w)
        if (choices.size > MAX_OPTIONS || choices.any { it.second.length > MAX_LABEL }) return false
        return qs.all { q ->
            val opts = q.path("options").toList()
            q.path("id").asText().isNotEmpty() && q.path("question").asText().isNotEmpty() && opts.size <= MAX_OPTIONS &&
                (opts.isNotEmpty() || q.path("own_words").asBoolean(true)) &&
                opts.all { o -> o.path("label").asText().let { it.isNotEmpty() && it.length <= MAX_LABEL } }
        }
    }

    /** A named choice's name and its label. */
    fun choices(w: JsonNode): List<Pair<String, String>> {
        val info = w.path("choice_info").toList()
        if (info.isNotEmpty()) return info.map { it.path("name").asText() to it.path("label").asText(it.path("name").asText()) }
        return w.path("choices").map { it.asText() to it.asText() }
    }

    private fun plain(text: String) = mapOf("type" to "plain_text", "text" to text)
    private fun option(n: Int, label: String, about: String = "") =
        mapOf("text" to plain(label), "value" to n.toString()) + (about.takeIf { it.isNotBlank() }?.let { mapOf("description" to plain(it.take(MAX_LABEL))) } ?: emptyMap())

    private fun takesReason(o: JsonNode) = o.path("why").asText("") in setOf("required", "optional")

    private fun textBlock(id: String, label: String, multiline: Boolean = false, optional: Boolean = true, hint: String? = null) = mapOf(
        "type" to "input", "block_id" to id, "optional" to optional, "label" to plain(label.take(TEXT_MAX)),
        "element" to mapOf("type" to "plain_text_input", "action_id" to TEXT, "multiline" to multiline, "max_length" to WHY_LIMIT),
    ) + (hint?.let { mapOf("hint" to plain(it.take(TEXT_MAX))) } ?: emptyMap())

    /** The form: one input per question, a box for own words where the Inbox has one, the named choice, and a reason. */
    fun formView(w: JsonNode, metadata: String): Map<String, Any?> {
        val blocks = mutableListOf<Map<String, Any?>>()
        questions(w).forEachIndexed { i, q ->
            val opts = q.path("options").toList()
            if (opts.isNotEmpty()) {
                val multiple = q.path("multiple").asBoolean(false)
                val options = opts.mapIndexed { n, o -> option(n, o.path("label").asText(), o.path("description").asText("")) }
                val recommended = opts.mapIndexedNotNull { n, o -> if (o.path("recommended").asBoolean(false)) n else null }
                val element = mutableMapOf<String, Any?>("type" to if (multiple) "multi_static_select" else "static_select", "action_id" to PICK, "options" to options,
                    "placeholder" to plain(if (multiple) "Pick any" else "Pick one"))
                // as in the Inbox: a multiple question starts with its recommended options ticked; a single one left empty takes the recommended one
                if (multiple && recommended.isNotEmpty()) element["initial_options"] = recommended.map { options[it] }
                blocks += mapOf("type" to "input", "block_id" to "q$i", "optional" to true, "label" to plain(q.path("question").asText().take(TEXT_MAX)), "element" to element) +
                    (if (!multiple && recommended.isNotEmpty()) mapOf("hint" to plain("Left empty: ${opts[recommended[0]].path("label").asText()} (recommended). keel writes that down.")) else emptyMap())
            }
            if (q.path("multiple").asBoolean(false) && opts.any(::takesReason)) {
                blocks += textBlock("r$i", "Why, for each one you tick", multiline = true, hint = "One line each: name: reason. For example: ${opts.first(::takesReason).path("label").asText()}: not needed here")
            }
            if (q.path("own_words").asBoolean(true)) {
                val single = !q.path("multiple").asBoolean(false) && opts.any(::takesReason)
                blocks += textBlock("w$i", if (opts.isEmpty()) q.path("question").asText() else if (single) "Your reason, or your own words" else "…or in your own words")
            }
        }
        val named = choices(w)
        if (named.isNotEmpty()) {
            val options = named.mapIndexed { n, c -> option(n, c.second) }
            val first = w.path("choice_info").indexOfFirst { it.path("default").asBoolean(false) }.coerceAtLeast(0)
            blocks += mapOf("type" to "input", "block_id" to "choice", "optional" to false, "label" to plain("Your choice"),
                "element" to mapOf("type" to "static_select", "action_id" to PICK, "options" to options, "initial_option" to options[first]))
        }
        blocks += textBlock("why", "Reason", multiline = true, hint = "Goes to the next agent, as a reason typed in keel does.")
        return mapOf("type" to "modal", "callback_id" to FORM, "private_metadata" to metadata, "title" to plain("Answer the form"), "submit" to plain("Send"),
            "close" to plain("Cancel"), "blocks" to blocks)
    }

    private fun values(view: JsonNode) = view.path("state").path("values")

    /** What the person sent, as the Inbox sends it (web/src/questions.ts answersOf): typed words win on a single question, and join a multiple one. */
    fun readForm(w: JsonNode, view: JsonNode): FormAnswer {
        val state = values(view)
        val answers = linkedMapOf<String, Any>()
        val reasons = linkedMapOf<String, String>()
        var missing: String? = null
        questions(w).forEachIndexed { i, q ->
            val opts = q.path("options").toList()
            val labels = opts.map { it.path("label").asText() }
            val typed = state.path("w$i").path(TEXT).path("value").asText("").trim()
            val pick = state.path("q$i").path(PICK)
            if (q.path("multiple").asBoolean(false)) {
                val ticked = pick.path("selected_options").mapNotNull { o -> o.path("value").asText().toIntOrNull()?.let(labels::getOrNull) }
                val list = ticked + listOfNotNull(typed.takeIf { it.isNotEmpty() })
                if (list.isNotEmpty()) answers[q.path("id").asText()] = list
                val lines = state.path("r$i").path(TEXT).path("value").asText("").lines().map { it.trim() }.filter { it.isNotEmpty() }
                val needing = ticked.filter { label -> opts.first { it.path("label").asText() == label }.let(::takesReason) }
                for (label in needing) {
                    val said = lines.firstOrNull { it.startsWith("$label:") }?.substringAfter(':')?.trim()
                        ?: lines.singleOrNull()?.takeIf { needing.size == 1 && !it.contains(':') }
                    if (!said.isNullOrEmpty()) reasons[label] = said
                    else if (opts.first { it.path("label").asText() == label }.path("why").asText() == "required") missing = missing ?: "Write why for \"$label\" first: it needs a reason."
                }
            } else {
                val one = pick.path("selected_option").path("value").asText("").toIntOrNull()?.let(labels::getOrNull)
                val chosen = one?.let { label -> opts.first { it.path("label").asText() == label } }
                // as in the Inbox: the typed words are then that option's reason, not an answer of their own
                if (chosen != null && takesReason(chosen)) {
                    if (typed.isEmpty() && chosen.path("why").asText() == "required") missing = missing ?: "Write why for \"$one\" first: it needs a reason."
                    answers[q.path("id").asText()] = if (typed.isEmpty()) one else "$one: $typed"
                } else (typed.ifEmpty { one })?.let { answers[q.path("id").asText()] = it }
            }
        }
        val named = choices(w)
        val choice = state.path("choice").path(PICK).path("selected_option").path("value").asText("").toIntOrNull()?.let(named::getOrNull)?.first
        return FormAnswer(answers, reasons, choice, state.path("why").path(TEXT).path("value").asText("").trim().ifEmpty { null }, missing)
    }

    /** A gate keel normally keeps: the answer it would give, a reason that must be there, and Send as the one confirm. */
    fun keptView(gate: String, word: String?, metadata: String): Map<String, Any?> = mapOf(
        "type" to "modal", "callback_id" to KEPT, "private_metadata" to metadata, "title" to plain("Answer from Slack"), "submit" to plain("Yes, answer it"), "close" to plain("Cancel"),
        "blocks" to listOf(
            mapOf("type" to "section", "text" to plain("keel normally keeps \"$gate\" in keel. You answer it here as: ${word ?: "approve"}. keel records your name, your reason and that it came from Slack.".take(TEXT_MAX))),
            textBlock("why", "Why this answer?", multiline = true, optional = false),
        ),
    )

    fun readWhy(view: JsonNode): String? = values(view).path("why").path(TEXT).path("value").asText("").trim().ifEmpty { null }

    /** The start and cancel buttons of `start ...`, with the one id keel keeps the request under. */
    fun startBlocks(text: String, id: String, start: String, cancel: String): List<Map<String, Any?>> = listOf(
        mapOf("type" to "section", "text" to mapOf("type" to "mrkdwn", "text" to text)),
        mapOf("type" to "actions", "elements" to listOf(
            mapOf("type" to "button", "action_id" to start, "style" to "primary", "text" to plain("Start it"), "value" to id),
            mapOf("type" to "button", "action_id" to cancel, "text" to plain("Cancel"), "value" to id),
        )),
    )

    private const val WHY_LIMIT = 2000
}

data class FormAnswer(val answers: Map<String, Any>, val reasons: Map<String, String>, val choice: String?, val why: String?, val missing: String?)
