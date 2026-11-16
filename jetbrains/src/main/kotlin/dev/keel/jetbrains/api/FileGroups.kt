package dev.keel.jetbrains.api

/** One row of the Changed files list: a folder header, or a file under it. */
sealed class FileRow {
    data class Folder(val path: String, val count: Int, val open: Boolean) : FileRow()
    data class File(val file: ChangedFile, val name: String, val indent: Boolean) : FileRow()
}

/** Does [path] match a search: every word of [query] is in it (any case); `*.php` style words match the end. */
fun fileMatches(path: String, query: String): Boolean {
    val p = path.lowercase()
    return query.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }.all { w ->
        if (w.startsWith("*")) p.endsWith(w.removePrefix("*")) else p.contains(w)
    }
}

/**
 * The Changed files rows: files that match [query], grouped by their folder when [folders] (folders in path order, a
 * folder in [closed] shows only its header), or the plain list.
 */
fun fileRows(files: List<ChangedFile>, query: String, folders: Boolean, closed: Set<String>): List<FileRow> {
    val hits = files.filter { fileMatches(it.path, query) }
    if (!folders) return hits.map { FileRow.File(it, it.path, false) }
    val out = mutableListOf<FileRow>()
    hits.groupBy { it.path.substringBeforeLast('/', "") }.toSortedMap().forEach { (dir, inDir) ->
        val open = dir !in closed || query.isNotBlank()
        out += FileRow.Folder(dir.ifEmpty { "(top folder)" }, inDir.size, open)
        if (open) inDir.sortedBy { it.path }.forEach { out += FileRow.File(it, it.path.substringAfterLast('/'), true) }
    }
    return out
}
