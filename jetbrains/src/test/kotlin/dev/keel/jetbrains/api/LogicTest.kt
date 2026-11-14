package dev.keel.jetbrains.api

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class LogicTest {
    private val multi = ClarifyQuestion(
        "suppliers", "Which suppliers first?",
        options = listOf(QuestionOption("Acme", recommended = true), QuestionOption("Globex", recommended = true), QuestionOption("Initech")),
        multiple = true,
    )
    private val single = ClarifyQuestion(
        "store", "Where to log?",
        options = listOf(QuestionOption("app log", recommended = true), QuestionOption("audit table"), QuestionOption("other", why = "required")),
    )

    @Test
    fun `answers follow the web's rules`() {
        // untouched: a multiple question sends its recommended options, a single one is left out
        assertEquals(JsonObject(mapOf("suppliers" to JsonArray(listOf(JsonPrimitive("Acme"), JsonPrimitive("Globex"))))), answersOf(listOf(multi, single), emptyMap(), emptyMap()))
        // typed words win over a clicked option; on a multiple question they are added
        val a = answersOf(listOf(multi, single), mapOf("suppliers" to listOf("Initech"), "store" to listOf("audit table")), mapOf("suppliers" to "Umbrella", "store" to "a file"))
        assertEquals(JsonArray(listOf(JsonPrimitive("Initech"), JsonPrimitive("Umbrella"))), a["suppliers"])
        assertEquals(JsonPrimitive("a file"), a["store"])
        // an option that takes a reason sends "<label>: <reason>"
        assertEquals(JsonPrimitive("other: syslog"), answersOf(listOf(single), mapOf("store" to listOf("other")), mapOf("store" to "syslog"))["store"])
    }

    @Test
    fun `reads KeelBot's question block and keeps the rest of the answer`() {
        val text = "Here is why.\n\n```keel-questions\n{\"questions\":[{\"question\":\"How?\",\"options\":[\"A\",{\"label\":\"B\",\"description\":\"bee\"}]}]}\n```\nAfter."
        val (rest, qs) = splitKeelQuestions(text)
        assertEquals(1, qs.size)
        assertEquals("q1", qs[0].id)
        assertEquals(listOf("A", "B"), qs[0].options.map { it.label })
        assertTrue("the first option is recommended when none is", qs[0].options[0].recommended)
        assertTrue(rest.startsWith("Here is why.") && rest.endsWith("After.") && !rest.contains("keel-questions"))
        assertEquals("Answers to your questions:\n1. How?\n   → B", answersMessage(qs, JsonObject(mapOf("q1" to JsonPrimitive("B")))))
    }

    @Test
    fun `maps phases to the four steps`() {
        assertEquals(0, stepOf("spec"))
        assertEquals(1, stepOf("red"))
        assertEquals(2, stepOf("green"))
        assertEquals(2, stepOf("review-fix"))
        assertEquals(3, stepOf("ac-gate"))
        assertEquals(4, stepOf("green", "done"))
    }

    @Test
    fun `cuts the diff lines around a comment`() {
        val diff = "diff --git a/x b/x\n@@ -10,3 +10,4 @@\n a\n-b\n+c\n+d\n e\n"
        val s = diffSnippet(diff, line = 12, startLine = 11)
        assertEquals(listOf(DiffLine(11, '+', "c"), DiffLine(12, '+', "d")), s)
        // no start line: the few lines before it, the removed one too
        assertEquals(listOf(DiffLine(10, ' ', "a"), DiffLine(null, '-', "b"), DiffLine(11, '+', "c"), DiffLine(12, '+', "d")), diffSnippet(diff, line = 12))
        assertEquals(emptyList<DiffLine>(), diffSnippet(diff, line = 99))
    }

    @Test
    fun `says how long ago and sums up tool calls`() {
        val now = Instant.parse("2026-10-23T12:00:00Z")
        assertEquals("12 min", ago("2026-10-23T11:48:00Z", now))
        assertEquals("2 h", ago("2026-10-23T10:00:00Z", now))
        assertEquals("", ago(null, now))
        assertEquals("Ran 2 commands · read 1 file", toolSummary(listOf(JobStep(kind = "tool"), JobStep(kind = "tool"), JobStep(kind = "read"), JobStep(kind = "text"))))
        assertEquals("SK", initials("sara.k"))
    }

    @Test
    fun `parses a unified diff with old and new numbers and finds threads on lines`() {
        val rows = parseDiff("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -5,3 +5,4 @@ fun x\n a\n-b\n+c\n+d\n e")
        assertEquals(DiffRow.Hunk("@@ -5,3 +5,4 @@ fun x"), rows[0])
        assertEquals(DiffRow.Line(5, 5, ' ', "a"), rows[1])
        assertEquals(DiffRow.Line(6, null, '-', "b"), rows[2])
        assertEquals(DiffRow.Line(null, 6, '+', "c"), rows[3])
        assertEquals(DiffRow.Line(7, 8, ' ', "e"), rows[5])
        assertTrue(threadOnLine(ReviewThread("t", "x", 6), rows[3] as DiffRow.Line))
        assertTrue(!threadOnLine(ReviewThread("t", "x", 6), rows[2] as DiffRow.Line))
        assertTrue(threadOnLine(ReviewThread("t", "x", 6, side = "LEFT"), rows[2] as DiffRow.Line))
    }

    @Test
    fun `reads the model catalog like the web and says which answers change files`() {
        val c = parseCatalog(KeelJson.parseToJsonElement(
            """{"codex":{"label":"Codex","modes":{"subscription":[{"id":"gpt-5"}]},"efforts":["low","high"]},
               "copilot":{"label":"Copilot","modes":{"subscription":[{"id":"sonnet 5.5","efforts":["none","max"]}],"api":[]},"efforts":[]},
               "old":[{"id":"m1","label":"M one"}],"broken":5}""",
        ))
        assertEquals(listOf("codex", "copilot", "old"), c.map { it.id })
        assertEquals(listOf("low", "high"), c[0].effortsOf("gpt-5", "subscription"))
        assertEquals(listOf("none", "max"), c[1].effortsOf("sonnet 5.5", "subscription"))
        assertEquals(setOf("subscription"), c[1].modes.keys)
        assertEquals("sonnet 5.5 · high", shortModel(Model("copilot", "subscription", "sonnet 5.5", "high")))
        assertTrue(optionChangesFiles(QuestionOption("Fix the webhook", "1 file")))
        assertTrue(!optionChangesFiles(QuestionOption("Start a fix flow", "test first")))
        assertTrue(!optionChangesFiles(QuestionOption("Explain more")))
    }

    @Test
    fun `says who is right by role and name, the same point and the fix check (0_26_1)`() {
        val t = ReviewThread("a", comments = listOf(ReviewComment("1", "bo", "x"), ReviewComment("2", "ana", "y")))
        val mine = sidesOf("ana", "ana", t)
        assertEquals(Sides("bo", "you"), mine)
        assertEquals("Reviewer (bo) is right", verdictLabel("reviewer_right", mine))
        assertEquals("Author (you) is right", verdictLabel("author_right", mine))
        assertEquals("Reviewer (bo) is partly right", verdictLabel("partly", mine))
        assertEquals("Unclear: keel could not decide", verdictLabel("unclear", mine))
        assertEquals("Reviewer is right", verdictLabel("reviewer_right", Sides(null, null)))
        // you review someone else's change: you are the reviewer
        assertEquals(Sides("you", "bo"), sidesOf("ana", "bo", ReviewThread("b", comments = listOf(ReviewComment("1", "ana", "x")))))
        assertEquals("Same point as the thread on line 22 (bo)", samePointLabel(SameAs("b", "a.py", 22, "bo"), "ana"))
        assertEquals("Same point as the thread on a.py (you)", samePointLabel(SameAs("b", "a.py", null, "ana"), "ana"))
        assertEquals("Use as my reply on both", useOnAllLabel(1))
        assertEquals("Use as my reply on all 3", useOnAllLabel(2))
        val done = FixRun("f", "done", headSha = "abc1234ff", result = FixResult("done", fixed = true))
        assertEquals(FixNote(true, "Fixed in abc1234"), fixNote(done, t))
        assertEquals(FixNote(false, "Still open: the max is hard-coded"), fixNote(done.copy(result = FixResult("done", fixed = false, stillOpen = "the max is hard-coded")), t))
        assertEquals(null, fixNote(done.copy(stale = true), t))
        assertEquals(null, fixNote(done, t.copy(resolved = true)))
        assertEquals(null, fixNote(done.copy(status = "running"), t))
        val now = Instant.parse("2026-11-14T12:00:00Z")
        assertEquals(
            "checked 2 min ago · ~3k tokens for 2 threads · read 3 files · claude · sonnet · high",
            checkMeta(JudgeResult(tokens = 3_400, model = "claude · sonnet", effort = "high", checkedAt = "2026-11-14T11:58:00Z", batchSize = 2, context = JudgeContext(files = 3)), now),
        )
    }
}
