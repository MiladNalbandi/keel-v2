package dev.keel.jetbrains.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FileGroupsTest {
    private val files = listOf(ChangedFile("src/order/Order.php"), ChangedFile("src/order/Unit.php"), ChangedFile("README.md"), ChangedFile("src/label/Label.kt"))

    @Test fun searchByWordsAndEnding() {
        assertTrue(fileMatches("src/order/Order.php", "order php"))
        assertTrue(fileMatches("src/order/Order.php", "*.php"))
        assertFalse(fileMatches("src/label/Label.kt", "*.php"))
    }

    @Test fun groupsByFolderAndFolds() {
        val rows = fileRows(files, "", folders = true, closed = setOf("src/label"))
        assertEquals(listOf("(top folder)", "README.md", "src/label", "src/order", "Order.php", "Unit.php"),
            rows.map { if (it is FileRow.Folder) it.path else (it as FileRow.File).name })
        assertEquals(FileRow.Folder("src/label", 1, false), rows[2])
    }

    @Test fun searchOpensFoldedFolders() {
        val rows = fileRows(files, "label", folders = true, closed = setOf("src/label"))
        assertEquals(2, rows.size)
        assertTrue((rows[0] as FileRow.Folder).open)
    }
}
