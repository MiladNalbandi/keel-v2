package dev.keel.jetbrains.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReviewModeLogicTest {
    private val prefs = """
        diff --git a/src/Prefs.kt b/src/Prefs.kt
        index 1111111..2222222 100644
        --- a/src/Prefs.kt
        +++ b/src/Prefs.kt
        @@ -1,3 +1,7 @@
         class Prefs {
        -    fun pageSize(user: String) = 20
        +    private val cache = mutableMapOf<String, Int>()
        +    fun savePageSize(user: String, size: Int) {
        +        cache[user] = size
        +    }
        +    fun pageSize(user: String) = cache[user] ?: 20
         }
    """.trimIndent()

    @Test
    fun `a replaced block is modified, on the head lines, with the old text`() {
        val r = prChangeRanges(prefs).single()
        assertEquals(ChangeKind.MODIFIED, r.kind)
        assertEquals(2 to 6, r.start to r.end)
        assertEquals(2 to 2, r.oldStart to r.oldEnd)
        assertEquals(listOf("    fun pageSize(user: String) = 20"), r.oldLines)
        assertEquals(5, r.newLines.size)
        assertEquals("lines 2–6", r.label)
    }

    @Test
    fun `added and removed blocks, several in a hunk and several hunks`() {
        val diff = "@@ -5,2 +5,4 @@\n a\n+b\n+c\n d\n@@ -10,4 +12,2 @@\n x\n-y\n-z\n w\n@@ -20,3 +20,3 @@ fun f()\n p\n-q\n+Q\n r\n+s\n"
        val rs = prChangeRanges(diff)
        assertEquals(listOf(ChangeKind.ADDED, ChangeKind.DELETED, ChangeKind.MODIFIED, ChangeKind.ADDED), rs.map { it.kind })
        assertEquals(6 to 7, rs[0].start to rs[0].end)
        assertEquals(6 to 5, rs[0].oldStart to rs[0].oldEnd) // nothing replaced: an empty old range after line 5
        assertEquals(13 to 12, rs[1].start to rs[1].end) // removed before head line 13
        assertEquals(11 to 12, rs[1].oldStart to rs[1].oldEnd)
        assertEquals(listOf("y", "z"), rs[1].oldLines)
        assertEquals(21 to 21, rs[2].start to rs[2].end)
        assertEquals(23 to 23, rs[3].start to rs[3].end)
    }

    @Test
    fun `a removal at the end of the file sits after the last line`() {
        val r = prChangeRanges("@@ -3,2 +3,1 @@\n keep\n-gone\n\\ No newline at end of file\n").single()
        assertEquals(ChangeKind.DELETED, r.kind)
        assertEquals(4, r.start)
        assertTrue(r.touches(3) && r.touches(4))
    }

    @Test
    fun `renames - no hunks is no change, hunks count on the new name's lines`() {
        val pure = "diff --git a/Old.kt b/New.kt\nsimilarity index 100%\nrename from Old.kt\nrename to New.kt\n"
        assertTrue(prChangeRanges(pure).isEmpty())
        val edited = "diff --git a/Old.kt b/New.kt\nsimilarity index 90%\nrename from Old.kt\nrename to New.kt\n--- a/Old.kt\n+++ b/New.kt\n@@ -1,2 +1,2 @@\n-class Old\n+class New\n x\n"
        val r = prChangeRanges(edited).single()
        assertEquals(ChangeKind.MODIFIED, r.kind)
        assertEquals(1 to 1, r.start to r.end)
        assertEquals(PrFileStatus.RENAMED, prFileStatusOf("renamed"))
    }

    @Test
    fun `Old and New read the base's file (a renamed one's old path) and the head's, none where a side has no file`() {
        assertEquals("src/A.kt" to "src/A.kt", oldNewPaths("src/A.kt", ChangedFile("src/A.kt", "modified")))
        assertEquals("src/Old.kt" to "src/New.kt", oldNewPaths("src/New.kt", ChangedFile("src/New.kt", "renamed", from = "src/Old.kt")))
        assertEquals(null to "src/N.kt", oldNewPaths("src/N.kt", ChangedFile("src/N.kt", "added")))
        assertEquals("src/D.kt" to null, oldNewPaths("src/D.kt", ChangedFile("src/D.kt", "removed")))
        assertEquals("src/X.kt" to "src/X.kt", oldNewPaths("src/X.kt", null))
    }

    @Test
    fun `file status from the host's words and git's letters`() {
        assertEquals(PrFileStatus.NEW, prFileStatusOf("added"))
        assertEquals(PrFileStatus.NEW, prFileStatusOf("A"))
        assertEquals(PrFileStatus.MODIFIED, prFileStatusOf("modified"))
        assertEquals(PrFileStatus.MODIFIED, prFileStatusOf("changed"))
        assertEquals(PrFileStatus.MODIFIED, prFileStatusOf(""))
        assertEquals(PrFileStatus.DELETED, prFileStatusOf("removed"))
        assertEquals(PrFileStatus.DELETED, prFileStatusOf("D"))
        assertEquals(PrFileStatus.RENAMED, prFileStatusOf("R100"))
        assertEquals(PrFileStatus.NEW, prFileStatusOf("copied"))
    }

    @Test
    fun `threads go on their head line, outdated and removed-line and past-the-end ones to the top`() {
        fun t(id: String, line: Int?, side: String = "RIGHT", outdated: Boolean = false, path: String = "src/Prefs.kt") =
            ReviewThread(id, path, line, side, outdated = outdated)
        val all = listOf(t("a", 2), t("b", 6), t("c", 2, side = "LEFT"), t("d", 4, outdated = true), t("e", 99), t("f", null), t("g", 1, path = "src/Orders.kt"))
        val p = placeThreads(all, "src/Prefs.kt", lineCount = 7)
        assertEquals(listOf(2 to "a", 6 to "b"), p.inline.map { it.first to it.second.id })
        assertEquals(listOf("c", "d", "e", "f"), p.top.map { it.id })
    }

    @Test
    fun `the block at a line, and what KeelBot is asked`() {
        val rs = prChangeRanges(prefs)
        assertEquals(rs[0], rangeAt(rs, 4))
        assertNull(rangeAt(rs, 7))
        val ask = askWhatChanged("src/Prefs.kt", rs[0], 7)
        assertTrue(ask.startsWith("What changed here in PR #7, and why might it matter? @src/Prefs.kt:2-6\n```diff\n-    fun pageSize(user: String) = 20\n+    private val cache"))
    }
}
