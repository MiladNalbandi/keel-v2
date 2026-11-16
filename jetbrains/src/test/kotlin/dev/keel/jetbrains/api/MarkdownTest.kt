package dev.keel.jetbrains.api

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTest {
    @Test fun fencesKeepCodeAsIs() {
        val h = markdownHtml("Look:\n```kotlin\nval a = b < c && **x**\n```\nDone.")
        assertTrue(h, h.contains("<div class=\"lang\">kotlin</div><pre>val a = b &lt; c &amp;&amp; **x**</pre>"))
        assertTrue(h, h.contains("<p>Done.</p>"))
        assertFalse(h.contains("```"))
    }

    @Test fun listsHeadingsAndInline() {
        val h = markdownHtml("## Why\n- **one** in `a.kt`\n- see src/Main.kt:12\n1. first")
        assertTrue(h, h.contains("<h3>Why</h3>"))
        assertTrue(h, h.contains("<ul><li><b>one</b> in <code>a.kt</code></li><li>see <a href=\"open:src/Main.kt:12\">src/Main.kt:12</a></li></ul>"))
        assertTrue(h, h.contains("<ol><li>first</li></ol>"))
    }

    @Test fun linksAndNoRefsInsideCode() {
        val h = markdownHtml("[docs](https://example.com/a) and `src/A.kt:3` and *it*")
        assertTrue(h, h.contains("<a href=\"https://example.com/a\">docs</a>"))
        assertTrue(h, h.contains("<code>src/A.kt:3</code>"))
        assertTrue(h, h.contains("<i>it</i>"))
        assertFalse(h.contains("open:src/A.kt"))
    }
}
