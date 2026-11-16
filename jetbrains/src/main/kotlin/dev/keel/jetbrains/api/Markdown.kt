package dev.keel.jetbrains.api

/**
 * KeelBot's Markdown as simple HTML for a Swing HTML pane: ``` fences (with the language), headings, - and 1. lists,
 * > quotes, paragraphs, `code`, **bold**, *italic*, [links](url), and every path:line outside code as an `open:` link.
 */
fun markdownHtml(md: String): String {
    val out = StringBuilder()
    val lines = md.replace("\r\n", "\n").split("\n")
    var i = 0
    var list: String? = null
    val para = mutableListOf<String>()
    fun flushPara() {
        if (para.isNotEmpty()) out.append("<p>").append(para.joinToString("<br>") { mdInline(it) }).append("</p>")
        para.clear()
    }
    fun closeList() {
        list?.let { out.append("</$it>") }
        list = null
    }
    while (i < lines.size) {
        val line = lines[i]
        val t = line.trim()
        val fence = Regex("^(```+|~~~+)\\s*([\\w#+.-]*)").find(t)
        when {
            fence != null -> {
                flushPara(); closeList()
                val mark = fence.groupValues[1]
                val lang = fence.groupValues[2]
                val code = mutableListOf<String>()
                i++
                while (i < lines.size && !lines[i].trim().startsWith(mark)) code += lines[i++]
                if (lang.isNotEmpty()) out.append("<div class=\"lang\">").append(esc(lang)).append("</div>")
                out.append("<pre>").append(esc(code.joinToString("\n"))).append("</pre>")
            }
            t.isEmpty() -> { flushPara(); closeList() }
            Regex("^#{1,6}\\s").containsMatchIn(t) -> {
                flushPara(); closeList()
                val n = t.takeWhile { it == '#' }.length.coerceIn(1, 6)
                val tag = "h" + (n + 1).coerceAtMost(5)
                out.append("<$tag>").append(mdInline(t.drop(n).trim())).append("</$tag>")
            }
            Regex("^([-*+])\\s+").containsMatchIn(t) || Regex("^\\d+[.)]\\s+").containsMatchIn(t) -> {
                flushPara()
                val kind = if (t[0].isDigit()) "ol" else "ul"
                if (list != kind) { closeList(); out.append("<$kind>"); list = kind }
                out.append("<li>").append(mdInline(t.replaceFirst(Regex("^([-*+]|\\d+[.)])\\s+"), ""))).append("</li>")
            }
            t.startsWith(">") -> {
                flushPara(); closeList()
                out.append("<blockquote>").append(mdInline(t.removePrefix(">").trim())).append("</blockquote>")
            }
            Regex("^(-{3,}|\\*{3,}|_{3,})$").matches(t) -> { flushPara(); closeList(); out.append("<hr>") }
            else -> { closeList(); para += t }
        }
        i++
    }
    flushPara(); closeList()
    return out.toString()
}

private fun esc(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

/** One line: `code` first (nothing inside it is touched), then links, file refs, bold and italic. */
fun mdInline(s: String): String {
    val out = StringBuilder()
    var at = 0
    Regex("`([^`]+)`").findAll(s).forEach { m ->
        out.append(mdText(s.substring(at, m.range.first)))
        out.append("<code>").append(esc(m.groupValues[1])).append("</code>")
        at = m.range.last + 1
    }
    out.append(mdText(s.substring(at)))
    return out.toString()
}

private val LINK = Regex("\\[([^\\]]+)]\\((https?://[^)\\s]+)\\)")
private val BARE = Regex("(?<![\"(])https?://[^\\s<)]+")

private fun mdText(s: String): String {
    val out = StringBuilder()
    var at = 0
    LINK.findAll(s).forEach { m ->
        out.append(plain(s.substring(at, m.range.first)))
        out.append("<a href=\"").append(esc(m.groupValues[2])).append("\">").append(style(esc(m.groupValues[1]))).append("</a>")
        at = m.range.last + 1
    }
    out.append(plain(s.substring(at)))
    return out.toString()
}

private fun plain(s: String): String {
    val out = StringBuilder()
    var at = 0
    val urls = BARE.findAll(s).map { it.range }.toList()
    fileRefs(s).filter { r -> urls.none { r.start in it } }.forEach { ref ->
        out.append(urlsAndStyle(s.substring(at, ref.start)))
        out.append("<a href=\"open:").append(esc(ref.path)).append(':').append(ref.line).append("\">")
            .append(esc(s.substring(ref.start, ref.end))).append("</a>")
        at = ref.end
    }
    out.append(urlsAndStyle(s.substring(at)))
    return out.toString()
}

private fun urlsAndStyle(s: String): String {
    val out = StringBuilder()
    var at = 0
    BARE.findAll(s).forEach { m ->
        out.append(style(esc(s.substring(at, m.range.first))))
        out.append("<a href=\"").append(esc(m.value)).append("\">").append(esc(m.value)).append("</a>")
        at = m.range.last + 1
    }
    out.append(style(esc(s.substring(at))))
    return out.toString()
}

private fun style(s: String): String = s
    .replace(Regex("\\*\\*([^*]+)\\*\\*")) { "<b>${it.groupValues[1]}</b>" }
    .replace(Regex("__([^_]+)__")) { "<b>${it.groupValues[1]}</b>" }
    .replace(Regex("(?<![*\\w])\\*([^*\\s][^*]*)\\*(?![*\\w])")) { "<i>${it.groupValues[1]}</i>" }
