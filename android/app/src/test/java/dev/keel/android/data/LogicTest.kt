package dev.keel.android.data

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
    fun `names a failed step and how keel reaches a provider (0_26)`() {
        org.junit.Assert.assertTrue(isFailedStep("crash", "commit failed"))
        org.junit.Assert.assertTrue(isFailedStep("fix", "make the change failed"))
        org.junit.Assert.assertTrue(isFailedStep("gate", "x", CrashInfo("auth")))
        org.junit.Assert.assertFalse(isFailedStep("fix", "AC-3 is still red after 3 tries. What now?"))
        assertEquals("API key", reachWord("claude"))
        assertEquals("API key (GitHub Models)", reachWord("copilot"))
        assertEquals("Local server", reachWord("local"))
        val c = CrashInfo("auth", "The provider refused the key", "Check the key, then retry.")
        val detail = "The provider refused the key. Check the key, then retry.\n\nWhat happened (auth): 401\n\nkeel tried 1 repair(s) by itself first.\n\n" +
            "Approve to try again, reject to stop the flow."
        assertEquals("What happened (auth): 401\n\nkeel tried 1 repair(s) by itself first.", crashDetail(detail, c))
        assertEquals("x\n\ny", crashDetail("x\n\ny\n", null))
    }
}
