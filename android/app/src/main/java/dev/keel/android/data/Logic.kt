package dev.keel.android.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

// Pure helpers the screens share (no Android here, so the unit tests run on the JVM). Several are ports of the web's
// own (web/src/questions.ts) so a phone answer is the same answer the dashboard sends.

/** The four steps the mockup's bars show. */
val STEP_NAMES = listOf("Spec", "Test", "Code", "Review")

/** Which of the four steps a phase belongs to; 4 = all done. */
fun stepOf(phase: String?, status: String? = null): Int {
    if (status == "done") return 4
    val p = phase.orEmpty().lowercase()
    return when {
        p.isEmpty() || p == "none" -> 0
        p in setOf("setup", "preflight", "workspace", "triage", "spec", "spec-ok", "contract", "bug-report", "small-change", "trivial") -> 0
        p.startsWith("spec") || p.startsWith("triage") -> 0
        p in setOf("red", "bug-repro") || p.startsWith("red") -> 1
        p in setOf("green", "ac", "refactor", "integration", "lint-fix", "coverage-fix", "bug-investigate", "bug-fix") -> 2
        p.startsWith("green") || p.endsWith("-fix") -> 2
        else -> 3 // gate, ac-gate, review, e2e, smoke, ship, pr...
    }
}

/** A waiting question's choices as rows: keel's choice_info when it sent one, else the raw names (a reject needs a reason). */
fun choiceRows(choices: List<String>?, info: List<ChoiceInfo>?): List<ChoiceInfo> {
    if (!info.isNullOrEmpty()) return info
    return choices.orEmpty().mapIndexed { n, name ->
        ChoiceInfo(name = name, label = name.replaceFirstChar { it.uppercase() }, description = "", why = if (name == "reject") "required" else "none", default = n == 0)
    }
}

/** Why the answer cannot be sent yet ("" = it can). */
fun choiceProblem(c: ChoiceInfo?, why: String): String = when {
    c == null -> "Choose one first."
    c.why == "required" && why.isBlank() -> "Write why first: “${c.label}” needs a reason."
    else -> ""
}

fun defaultPick(q: ClarifyQuestion): List<String> = if (q.multiple) q.options.filter { it.recommended }.map { it.label } else emptyList()

/** The picked option of a single question when it takes a reason: the typed words are then that option's reason. */
fun reasonOption(q: ClarifyQuestion, picked: List<String>?): String? {
    if (q.multiple || picked.isNullOrEmpty()) return null
    val o = q.options.firstOrNull { it.label == picked.first() } ?: return null
    return if (o.why == "required" || o.why == "optional") o.label else null
}

/**
 * The answers to send (web questions.ts answersOf): a typed answer wins over a clicked option (an option that takes a
 * reason sends "<label>: <words>"); a `multiple` question sends its ticks plus the typed words, an untouched one its
 * recommended options; an unanswered single question is left out (keel then uses the recommended one).
 */
fun answersOf(questions: List<ClarifyQuestion>, picked: Map<String, List<String>>, typed: Map<String, String>): JsonObject {
    val out = LinkedHashMap<String, JsonElement>()
    for (q in questions) {
        val t = typed[q.id].orEmpty().trim()
        val p = picked[q.id]
        if (q.multiple) {
            val ticked = p ?: defaultPick(q)
            val list = ticked + listOfNotNull(t.ifEmpty { null })
            if (list.isNotEmpty()) out[q.id] = JsonArray(list.map { JsonPrimitive(it) })
        } else if (t.isNotEmpty()) {
            val r = reasonOption(q, p)
            out[q.id] = JsonPrimitive(if (r != null) "$r: $t" else t)
        } else if (!p.isNullOrEmpty()) {
            out[q.id] = JsonPrimitive(p.first())
        }
    }
    return JsonObject(out)
}

const val ANSWERS_PREFIX = "Answers to your questions:"

/** The answers as the next chat message to KeelBot (web questions.ts answersMessage). */
fun answersMessage(questions: List<ClarifyQuestion>, answers: JsonObject): String =
    (listOf(ANSWERS_PREFIX) + questions.mapIndexed { i, q ->
        val said = when (val a = answers[q.id]) {
            is JsonArray -> a.joinToString("; ") { (it as? JsonPrimitive)?.contentOrNull.orEmpty() }
            is JsonPrimitive -> a.contentOrNull.orEmpty()
            else -> ""
        }
        "${i + 1}. ${q.question}\n   → ${said.ifEmpty { "(not answered: use the recommended one)" }}"
    }).joinToString("\n")

private val QUESTIONS_FENCE = Regex("""(?m)^\s*(`{3,}|~{3,})\s*keel-questions\s*$""")

/** KeelBot's answer split into its text and the questions of a ```keel-questions``` block (web parseQuestions). */
fun splitKeelQuestions(text: String): Pair<String, List<ClarifyQuestion>> {
    val m = QUESTIONS_FENCE.find(text) ?: return text to emptyList()
    val fence = m.groupValues[1]
    val start = m.range.last + 1
    val end = text.indexOf("\n" + fence, start).takeIf { it >= 0 } ?: return text to emptyList()
    val body = text.substring(start, end).trim()
    val rest = (text.substring(0, m.range.first) + text.substring(minOf(text.length, end + fence.length + 1))).trim()
    return rest to parseQuestions(body)
}

fun parseQuestions(raw: String): List<ClarifyQuestion> {
    val v = runCatching { KeelJson.parseToJsonElement(raw) }.getOrNull() ?: return emptyList()
    val list = when (v) {
        is JsonArray -> v
        is JsonObject -> (v["questions"] as? JsonArray) ?: return emptyList()
        else -> return emptyList()
    }
    val out = mutableListOf<ClarifyQuestion>()
    list.forEachIndexed { i, q ->
        if (out.size >= 4 || q !is JsonObject) return@forEachIndexed
        val question = q.str("question")
        val opts = (q["options"] as? JsonArray).orEmpty().take(4).mapNotNull { o ->
            when (o) {
                is JsonPrimitive -> o.contentOrNull?.trim()?.takeIf { it.isNotEmpty() }?.let { QuestionOption(it) }
                is JsonObject -> o.str("label").takeIf { it.isNotEmpty() }?.let {
                    QuestionOption(it, o.str("description").ifEmpty { null }, (o["recommended"] as? JsonPrimitive)?.booleanOrNull == true)
                }
                else -> null
            }
        }.toMutableList()
        if (question.isEmpty() || opts.isEmpty()) return@forEachIndexed
        if (opts.none { it.recommended }) opts[0] = opts[0].copy(recommended = true)
        out += ClarifyQuestion(
            id = q.str("id").ifEmpty { "q${i + 1}" }, question = question, why = q.str("why").ifEmpty { null },
            options = opts, multiple = (q["multiple"] as? JsonPrimitive)?.booleanOrNull == true,
        )
    }
    return out
}

private fun JsonObject.str(k: String) = (this[k] as? JsonPrimitive)?.contentOrNull?.trim().orEmpty()
private fun JsonArray?.orEmpty(): List<JsonElement> = this ?: emptyList()

/** "12 min", "2 h", "1 d" since an ISO time; "" when it cannot be read. */
fun ago(iso: String?, now: Instant = Instant.now()): String {
    val t = parseTime(iso) ?: return ""
    val s = Duration.between(t, now).seconds.coerceAtLeast(0)
    return when {
        s < 60 -> "now"
        s < 3600 -> "${s / 60} min"
        s < 86_400 -> "${s / 3600} h"
        else -> "${s / 86_400} d"
    }
}

fun clock(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String =
    parseTime(iso)?.atZone(zone)?.format(DateTimeFormatter.ofPattern("HH:mm", Locale.ROOT)) ?: ""

fun parseTime(iso: String?): Instant? {
    if (iso.isNullOrBlank()) return null
    return runCatching { Instant.parse(iso) }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(iso).toInstant() }.getOrNull()
        ?: runCatching { java.time.LocalDateTime.parse(iso).atZone(ZoneId.of("UTC")).toInstant() }.getOrNull()
}

fun usd(v: Double): String = "$" + String.format(Locale.ROOT, "%.2f", v)

fun ktokens(n: Long): String = when {
    n >= 1_000_000 -> String.format(Locale.ROOT, "%.1fM", n / 1_000_000.0)
    n >= 1_000 -> "${(n + 500) / 1000}k"
    else -> n.toString()
}

/** One line of a diff snippet: its new-file line number (or null for a removed line), the mark and the text. */
data class DiffLine(val number: Int?, val mark: Char, val text: String)

/** The lines of a unified diff around a review comment's line (new side), at most [max] lines ending at `line`. */
fun diffSnippet(diff: String, line: Int?, startLine: Int? = null, max: Int = 4): List<DiffLine> {
    if (line == null || diff.isBlank()) return emptyList()
    val out = mutableListOf<DiffLine>()
    var newLine = 0
    val hunk = Regex("""^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@""")
    for (raw in diff.lines()) {
        val h = hunk.find(raw)
        if (h != null) { newLine = h.groupValues[1].toInt(); continue }
        if (raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("diff ") || raw.startsWith("index ")) continue
        if (newLine == 0) continue
        when {
            raw.startsWith("+") -> { out += DiffLine(newLine, '+', raw.drop(1)); newLine++ }
            raw.startsWith("-") -> out += DiffLine(null, '-', raw.drop(1))
            raw.startsWith("\\") -> Unit
            else -> { out += DiffLine(newLine, ' ', raw.drop(1)); newLine++ }
        }
    }
    val from = (startLine ?: (line - max + 1)).coerceAtMost(line)
    val idxEnd = out.indexOfLast { it.number == line }
    if (idxEnd < 0) return emptyList()
    val idxStart = out.indexOfFirst { (it.number ?: Int.MIN_VALUE) >= from }.takeIf { it in 0..idxEnd } ?: idxEnd
    return out.subList(maxOf(idxStart, idxEnd - 7), idxEnd + 1).toList()
}

/** "Ran 2 commands · read 3 files" from an answer's steps; "" when it used no tool. */
fun toolSummary(steps: List<JobStep>): String {
    val cmds = steps.count { it.kind == "tool" }
    val reads = steps.count { it.kind == "read" }
    val edits = steps.count { it.kind == "edit" || it.kind == "write" }
    val parts = buildList {
        if (cmds > 0) add("Ran $cmds command${if (cmds == 1) "" else "s"}")
        if (reads > 0) add("read $reads file${if (reads == 1) "" else "s"}")
        if (edits > 0) add("changed $edits file${if (edits == 1) "" else "s"}")
    }
    return parts.joinToString(" · ").replaceFirstChar { it.uppercase() }
}

/** The tool log's lines ("$ cmd", "read path"). */
fun toolLines(steps: List<JobStep>): String = steps.mapNotNull { s ->
    when (s.kind) {
        "tool" -> "$ " + s.text.lineSequence().firstOrNull().orEmpty() + if (s.ok == false) "  (failed)" else ""
        "read" -> "read ${s.path ?: s.text.lineSequence().firstOrNull().orEmpty()}"
        "edit", "write" -> "${s.kind} ${s.path.orEmpty()}"
        else -> null
    }
}.joinToString("\n")

/** How keel reaches a provider (the web's ModelPicker): its API, whatever mode a saved model still says (#101). */
fun reachWord(provider: String): String = when (provider) {
    "copilot" -> "API key (GitHub Models)"
    "vertex" -> "Google Cloud"
    "local" -> "Local server"
    else -> "API key"
}

/** #101 the flow doctor's pause: a step failed and keel could not repair it by itself. Kind "crash" (any step), or an
 *  agent step's "fix" whose title is "<step> failed" (flowdoctor/repair.py pause_of). */
fun isFailedStep(kind: String?, title: String?, crash: CrashInfo? = null): Boolean =
    crash != null || kind == "crash" || (kind == "fix" && title.orEmpty().endsWith(" failed"))

/** A failed step's text without what the screen already says: the crash's title and advice (shown above it) and the
 *  engine's "Approve to try again…" / "retry: …" lines (the buttons say it). What is left: what happened, and the repairs. */
fun crashDetail(detail: String, crash: CrashInfo?): String {
    if (crash == null) return detail.trim()
    return detail.split(Regex("\n\\s*\n")).map { it.trim() }.filter { p ->
        p.isNotEmpty() && !(crash.title.isNotBlank() && p.startsWith(crash.title)) &&
            !p.startsWith("Approve to try again") && !p.startsWith("retry:")
    }.joinToString("\n\n")
}

/** The two sides of a review thread by name ("you" when it is you; null when keel does not know the name). */
data class Sides(val reviewer: String?, val author: String?)

private fun sameName(a: String?, b: String?) = !a.isNullOrEmpty() && !b.isNullOrEmpty() && a.equals(b, ignoreCase = true)

/** A thread's reviewer (the first one in it who did not write the change) and the change's author (web verdict.ts). */
fun sidesOf(me: String?, author: String?, t: ReviewThread): Sides {
    fun name(n: String?) = if (n.isNullOrEmpty()) null else if (sameName(n, me)) "you" else n
    val reviewer = t.comments.firstOrNull { !sameName(it.author, author) }?.author ?: t.comments.firstOrNull()?.author
    return Sides(name(reviewer), name(author))
}

/** keel's verdict on one point in words that name the role and the person: "Reviewer (bo) is right" (0.26.1, the same
 *  words as the web's both views). */
fun verdictLabel(v: String, s: Sides): String {
    fun who(role: String, name: String?) = if (name != null) "$role ($name)" else role
    return when (v) {
        "reviewer_right" -> "${who("Reviewer", s.reviewer)} is right"
        "author_right" -> "${who("Author", s.author)} is right"
        "partly" -> "${who("Reviewer", s.reviewer)} is partly right"
        else -> "Unclear: keel could not decide"
    }
}

/** The link to another thread that makes the same point: "Same point as the thread on line 22 (bo)". */
fun samePointLabel(o: SameAs, me: String?): String {
    val name = if (o.author.isNullOrEmpty()) null else if (sameName(o.author, me)) "you" else o.author
    val where = if (o.line != null) "line ${o.line}" else o.path ?: "the pull request"
    return "Same point as the thread on $where" + (name?.let { " ($it)" } ?: "")
}

/** The button that puts keel's reply into every thread of the point: "Use as my reply on both" (or "on all 3"). */
fun useOnAllLabel(others: Int): String = "Use as my reply on ${if (others == 1) "both" else "all ${others + 1}"}"

/** keel's check of the author's "fixed", in words: "Fixed in abc1234" (then Resolve) or "Still open: …". Null when there
 *  is none to show: no check, not done, the head moved on since (a new one comes), or the thread is resolved. */
data class FixNote(val fixed: Boolean, val text: String)

fun fixNote(run: FixRun?, t: ReviewThread): FixNote? {
    val r = run?.result
    if (run == null || run.stale || t.resolved || run.status != "done" || r?.stage != "done") return null
    return if (r.fixed == true) FixNote(true, "Fixed in ${r.sha ?: run.headSha.take(7)}")
    else FixNote(false, "Still open: ${r.stillOpen?.ifBlank { null } ?: r.why?.ifBlank { null } ?: "keel does not see the fix in the new code"}")
}

/** The check's cost line: "checked 2 min ago · ~2k tokens for 2 threads · read 3 files · claude · sonnet · high". */
fun checkMeta(r: JudgeResult, now: Instant = Instant.now()): String = listOfNotNull(
    ago(r.checkedAt, now).takeIf { it.isNotEmpty() }?.let { if (it == "now") "checked just now" else "checked $it ago" },
    r.tokens?.let { "~${(it + 500) / 1000}k tokens" + if ((r.batchSize ?: 0) > 1) " for ${r.batchSize} threads" else "" },
    r.context?.files?.takeIf { it > 0 }?.let { "read $it file${if (it == 1) "" else "s"}" },
    r.model?.let { m -> m + (r.effort?.let { " · $it" } ?: "") },
).joinToString(" · ")

/** Initials for an avatar: "sara.k" → "SK", "you" → "YO". */
fun initials(name: String): String {
    val parts = name.split('.', '-', '_', ' ').filter { it.isNotBlank() }
    return when {
        parts.size >= 2 -> "${parts[0][0]}${parts[1][0]}".uppercase()
        name.length >= 2 -> name.take(2).uppercase()
        else -> name.uppercase()
    }
}

/** A JSON object for a gate answer's payload. */
fun payloadOf(vararg pairs: Pair<String, JsonElement>): JsonObject = JsonObject(pairs.toMap())

fun JsonElement.objOrNull(): JsonObject? = runCatching { jsonObject }.getOrNull()
fun JsonElement.arrOrNull(): JsonArray? = runCatching { jsonArray }.getOrNull()


private val PROVIDER_ORDER = listOf("claude", "codex", "copilot", "vertex", "local", "fake")

/**
 * The model catalog as the web's normalizeCatalog reads it: `{provider: {label, modes: {mode: [{id, label, efforts?}]},
 * efforts}}`, or the older `{provider: [{id, label}]}`. Anything malformed is dropped.
 */
fun parseCatalog(raw: JsonElement): List<CatalogProvider> {
    val obj = raw as? JsonObject ?: return emptyList()
    fun models(v: JsonElement?): List<CatalogModel> = (v as? JsonArray).orEmpty().mapNotNull { m ->
        val o = m as? JsonObject ?: return@mapNotNull null
        val id = o.str("id").ifEmpty { return@mapNotNull null }
        CatalogModel(id, o.str("label").ifEmpty { id }, (o["efforts"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull })
    }
    val out = obj.mapNotNull { (id, v) ->
        when (v) {
            is JsonArray -> models(v).takeIf { it.isNotEmpty() }?.let { CatalogProvider(id, id, mapOf("api" to it), emptyList()) }
            is JsonObject -> {
                val modes = (v["modes"] as? JsonObject)?.mapValues { (_, l) -> models(l) }?.filterValues { it.isNotEmpty() }.orEmpty()
                if (modes.isEmpty()) null
                else CatalogProvider(id, v.str("label").ifEmpty { id }, modes, (v["efforts"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }.orEmpty())
            }
            else -> null
        }
    }
    return out.sortedBy { PROVIDER_ORDER.indexOf(it.id).let { i -> if (i < 0) 99 else i } }
}

/** The model chip's words: "sonnet 5.5 · high". */
fun shortModel(m: Model): String = listOfNotNull(m.model.ifBlank { null }, m.effort?.ifBlank { null }).joinToString(" · ")

private val CHANGE_WORDS = Regex("""\b(fix|change|edit|apply|rename|update|rewrite|write|add|remove|delete|refactor|move)\b""", RegexOption.IGNORE_CASE)

/** In Ask mode, does this answer ask KeelBot to change files (so the chat has to switch to Edit first)? A flow is not. */
fun optionChangesFiles(o: QuestionOption): Boolean {
    val words = o.label + " " + o.description.orEmpty()
    return CHANGE_WORDS.containsMatchIn(words) && !Regex("""\bflow\b""", RegexOption.IGNORE_CASE).containsMatchIn(o.label)
}

/** A row of a unified diff: a hunk header, or a line with its old and new numbers (null on the side it is not on). */
sealed class DiffRow {
    data class Hunk(val header: String) : DiffRow()
    data class Line(val old: Int?, val new: Int?, val mark: Char, val text: String) : DiffRow()
}

/** A unified diff (the review plugin's GET .../review/diff) as rows; the file headers (diff, index, ---, +++) are left out. */
fun parseDiff(diff: String): List<DiffRow> {
    val out = mutableListOf<DiffRow>()
    val hunk = Regex("""^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$""")
    var old = 0
    var new = 0
    var inHunk = false
    for (raw in diff.lines()) {
        val h = hunk.find(raw)
        if (h != null) {
            old = h.groupValues[1].toInt()
            new = h.groupValues[2].toInt()
            inHunk = true
            out += DiffRow.Hunk(raw)
            continue
        }
        if (!inHunk) continue
        when {
            raw.startsWith("+") -> out += DiffRow.Line(null, new++, '+', raw.drop(1))
            raw.startsWith("-") -> out += DiffRow.Line(old++, null, '-', raw.drop(1))
            raw.startsWith("\\") -> Unit
            raw.isEmpty() -> Unit
            else -> out += DiffRow.Line(old++, new++, ' ', raw.drop(1))
        }
    }
    return out
}

/** Whether a review thread sits on this diff line (RIGHT: the new side, LEFT: the old one). */
fun threadOnLine(t: ReviewThread, l: DiffRow.Line): Boolean =
    t.line != null && if (t.side == "LEFT") l.old == t.line && l.mark != '+' else l.new == t.line && l.mark != '-'
