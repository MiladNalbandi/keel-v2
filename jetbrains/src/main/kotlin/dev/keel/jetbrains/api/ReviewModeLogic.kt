package dev.keel.jetbrains.api

/** Review mode's pure parts: a PR's diff as changed line ranges of the head file, file badges, thread places. */

enum class ChangeKind { ADDED, MODIFIED, DELETED }

/**
 * One changed block of a file, in the head (new) file's 1-based lines. ADDED / MODIFIED: lines [start]..[end].
 * DELETED: nothing left on the head side; the removed lines sat just before line [start] ([end] = start - 1).
 * [oldStart]..[oldEnd] are the base lines it replaced (empty for ADDED: oldEnd = oldStart - 1).
 */
data class ChangeRange(
    val kind: ChangeKind,
    val start: Int,
    val end: Int,
    val oldStart: Int,
    val oldEnd: Int,
    val oldLines: List<String>,
    val newLines: List<String>,
) {
    /** Whether a head line (1-based) belongs to this block; a removal belongs to the lines around its gap. */
    fun touches(line: Int): Boolean = if (kind == ChangeKind.DELETED) line == start || line == start - 1 else line in start..end

    val label: String
        get() = when {
            kind == ChangeKind.DELETED -> "lines removed before line $start"
            start == end -> "line $start"
            else -> "lines $start–$end"
        }
}

private val HUNK_HEAD = Regex("""^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@""")

/** A unified diff (keel's review diff or `git diff base...HEAD -- path`) as changed blocks of the head file. */
fun prChangeRanges(diff: String): List<ChangeRange> {
    val out = mutableListOf<ChangeRange>()
    val removed = mutableListOf<Pair<Int, String>>()
    val added = mutableListOf<Pair<Int, String>>()
    var nextNew = 1
    var nextOld = 1
    var inHunk = false
    fun flush() {
        if (removed.isEmpty() && added.isEmpty()) return
        val oldStart = removed.firstOrNull()?.first ?: nextOld
        val oldEnd = removed.lastOrNull()?.first ?: (oldStart - 1)
        out += when {
            added.isEmpty() -> ChangeRange(ChangeKind.DELETED, nextNew, nextNew - 1, oldStart, oldEnd, removed.map { it.second }, emptyList())
            removed.isEmpty() -> ChangeRange(ChangeKind.ADDED, added.first().first, added.last().first, oldStart, oldEnd, emptyList(), added.map { it.second })
            else -> ChangeRange(ChangeKind.MODIFIED, added.first().first, added.last().first, oldStart, oldEnd, removed.map { it.second }, added.map { it.second })
        }
        removed.clear()
        added.clear()
    }
    for (raw in diff.lines()) {
        val h = HUNK_HEAD.find(raw)
        if (h != null) {
            flush()
            nextOld = h.groupValues[1].toInt()
            nextNew = h.groupValues[2].toInt()
            inHunk = true
            continue
        }
        if (!inHunk) continue
        when {
            raw.startsWith("diff --git") -> { flush(); inHunk = false }
            raw.startsWith("+") -> added += nextNew++ to raw.drop(1)
            raw.startsWith("-") -> {
                if (added.isNotEmpty()) flush() // a new block: unified diffs list a block's removals first
                removed += nextOld++ to raw.drop(1)
            }
            raw.startsWith("\\") -> Unit
            else -> {
                flush()
                nextOld++
                nextNew++
            }
        }
    }
    flush()
    return out
}

/** The block a head line (1-based) is in, if any. */
fun rangeAt(ranges: List<ChangeRange>, line: Int): ChangeRange? =
    ranges.firstOrNull { it.kind != ChangeKind.DELETED && line in it.start..it.end } ?: ranges.firstOrNull { it.touches(line) }

/** A changed file's badge in the project view and the Changed files list. */
/**
 * 0.4.0 Old | New: which files to read for the two sides. The old side is the base's file (a renamed file's old path),
 * none for a new file; the new side is the head's file, none for a deleted one.
 */
fun oldNewPaths(path: String, file: ChangedFile?): Pair<String?, String?> {
    val status = prFileStatusOf(file?.status ?: "")
    val old = if (status == PrFileStatus.NEW) null else file?.from?.takeIf { it.isNotBlank() } ?: path
    val new = if (status == PrFileStatus.DELETED) null else path
    return old to new
}

enum class PrFileStatus(val mark: String, val word: String) {
    NEW("✚", "new"), MODIFIED("●", "changed"), RENAMED("●", "renamed"), DELETED("✖", "deleted")
}

/** The host's file status (GitHub: added, modified, removed, renamed, copied, changed; git: A, M, D, R, C). */
fun prFileStatusOf(status: String): PrFileStatus = when (status.trim().lowercase()) {
    "added", "a", "new", "copied", "c" -> PrFileStatus.NEW
    "removed", "deleted", "d" -> PrFileStatus.DELETED
    "renamed", "r" -> PrFileStatus.RENAMED
    else -> if (status.trim().lowercase().startsWith("r")) PrFileStatus.RENAMED else PrFileStatus.MODIFIED
}

/** Where a file's review threads go in a normal editor: by their head line, or as a note at the top. */
data class ThreadPlaces(val inline: List<Pair<Int, ReviewThread>>, val top: List<ReviewThread>)

/**
 * A thread sits on its head line (RIGHT side) when the line still exists. Outdated threads, comments on removed
 * lines (LEFT side), and lines past the end of the file go to the top of the file as a note.
 */
fun placeThreads(threads: List<ReviewThread>, path: String, lineCount: Int): ThreadPlaces {
    val mine = threads.filter { it.path == path }
    val (inline, top) = mine.partition { t -> !t.outdated && t.side != "LEFT" && t.line != null && t.line in 1..lineCount }
    return ThreadPlaces(inline.map { it.line!! to it }.sortedBy { it.first }, top)
}

/** The text KeelBot gets for "Ask KeelBot what changed here". */
fun askWhatChanged(path: String, r: ChangeRange, pr: Int?): String = buildString {
    val where = when {
        r.kind == ChangeKind.DELETED -> "$path (removed before line ${r.start})"
        r.start == r.end -> "$path:${r.start}"
        else -> "$path:${r.start}-${r.end}"
    }
    append("What changed here")
    if (pr != null) append(" in PR #").append(pr)
    append(", and why might it matter? @").append(where).append('\n')
    append("```diff\n")
    r.oldLines.forEach { append('-').append(it).append('\n') }
    r.newLines.forEach { append('+').append(it).append('\n') }
    append("```\n")
}
