package dev.keel.jetbrains.editor

import com.intellij.diff.DiffContentFactory
import com.intellij.diff.DiffManager
import com.intellij.diff.requests.SimpleDiffRequest
import com.intellij.openapi.fileTypes.FileTypeManager
import com.intellij.openapi.project.Project
import dev.keel.jetbrains.api.ReviewFile
import dev.keel.jetbrains.api.oldNewPaths
import dev.keel.jetbrains.core.KeelProjectService
import dev.keel.jetbrains.core.ReviewMode
import dev.keel.jetbrains.ui.KeelNotifier

/**
 * 0.4.0 Old | New: a changed file of the pull request in review mode, whole, side by side in the IDE's diff viewer.
 * The old file (the review's base) is on the left, the new one (its head) on the right; a new file has an empty left
 * side, a deleted one an empty right side.
 */
object OldNewDiff {
    fun open(project: Project, path: String) {
        val mode = ReviewMode.of(project)
        val key = mode.prKey ?: return
        val pid = KeelProjectService.of(project).pid ?: return
        val (oldPath, newPath) = oldNewPaths(path, mode.files.firstOrNull { it.path == path })
        KeelProjectService.of(project).io({ api ->
            // a side keel cannot read (not in that commit) stays empty
            val base = oldPath?.let { runCatching { api.reviewFile(pid, key, it, "base") }.getOrNull() }
            val head = newPath?.let { runCatching { api.reviewFile(pid, key, it, "head") }.getOrNull() }
            base to head
        }) { r ->
            val (base, head) = r.getOrNull() ?: (null to null)
            if (base == null && head == null) {
                KeelNotifier.error(project, "Could not read $path", "keel could not read it from the pull request.")
                return@io
            }
            val type = FileTypeManager.getInstance().getFileTypeByFileName(path.substringAfterLast('/'))
            val f = DiffContentFactory.getInstance()
            val side = { file: ReviewFile? -> file?.let { f.create(project, it.text, type) } ?: f.createEmpty() }
            val pr = mode.number?.let { "PR #$it" } ?: key
            DiffManager.getInstance().showDiff(
                project,
                SimpleDiffRequest(
                    "${path.substringAfterLast('/')}: Old | New ($pr)",
                    side(base),
                    side(head),
                    "Old · ${oldPath ?: path} @ ${base?.ref ?: "base"}",
                    "New · $path @ ${head?.ref ?: "head"}",
                ),
            )
        }
    }
}
