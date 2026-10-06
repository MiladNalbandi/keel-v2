package keel.api.repo

import keel.api.events.EventHub
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/projects/{pid}/repo")
class RepoController(private val repo: RepoService, private val search: RepoSearch, private val hub: EventHub) {

    @GetMapping
    fun info(@PathVariable pid: String): RepoInfo = repo.info(pid)

    @GetMapping("/tree")
    fun tree(@PathVariable pid: String, @RequestParam(defaultValue = "4") depth: Int, @RequestParam(required = false) dir: String?): List<TreeNode> =
        repo.tree(pid, depth, dir)

    @GetMapping("/file")
    fun file(@PathVariable pid: String, @RequestParam path: String): FileView = repo.file(pid, path)

    /** The file's bytes (text, or an image). A sandbox CSP and nosniff: an SVG or HTML file never runs as keel's page. */
    @GetMapping("/raw")
    fun raw(@PathVariable pid: String, @RequestParam path: String): ResponseEntity<ByteArray> {
        val (rel, bytes) = repo.raw(pid, path)
        return ResponseEntity.ok()
            .contentType(mediaType(rel, bytes))
            .header("Content-Security-Policy", "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'")
            .header("X-Content-Type-Options", "nosniff")
            .header("Cache-Control", "no-store")
            .body(bytes)
    }

    @GetMapping("/files")
    fun files(@PathVariable pid: String): FileList = search.files(pid)

    @GetMapping("/search")
    fun search(
        @PathVariable pid: String,
        @RequestParam q: String,
        @RequestParam(defaultValue = "false") regex: Boolean,
        @RequestParam(name = "case", defaultValue = "false") matchCase: Boolean,
        @RequestParam(defaultValue = "false") word: Boolean,
        @RequestParam(required = false) include: String?,
        @RequestParam(required = false) exclude: String?,
        @RequestParam(defaultValue = "500") max: Int,
    ): SearchResult = search.search(pid, q, regex, matchCase, word, include, exclude, max)

    @GetMapping("/changes")
    fun changes(@PathVariable pid: String): List<Change> = repo.changes(pid)

    @GetMapping("/diff")
    fun diff(
        @PathVariable pid: String,
        @RequestParam path: String,
        @RequestParam(defaultValue = "head") against: String,
        @RequestParam(required = false) sha: String?,
    ): FileDiff = repo.diff(pid, path, against, sha)

    @GetMapping("/commit")
    fun commit(@PathVariable pid: String, @RequestParam sha: String): CommitView = repo.commit(pid, sha)

    @GetMapping("/commits")
    fun commits(@PathVariable pid: String, @RequestParam(defaultValue = "30") limit: Int, @RequestParam(required = false) range: String?): List<Commit> =
        repo.commits(pid, limit, range)

    @GetMapping("/history")
    fun history(@PathVariable pid: String, @RequestParam path: String): List<Commit> = repo.history(pid, path)

    @PostMapping("/update-from-base")
    fun updateFromBase(@PathVariable pid: String): MergeResult {
        val r = repo.updateFromBase(pid)
        if (r.merged) hub.publish(pid, "project.changed", mapOf("id" to pid))
        return r
    }

    private fun mediaType(rel: String, bytes: ByteArray): MediaType {
        val ext = rel.substringAfterLast('/').substringAfterLast('.', "").lowercase()
        IMAGES[ext]?.let { return MediaType.parseMediaType(it) }
        return if (RepoService.isBinary(bytes)) MediaType.APPLICATION_OCTET_STREAM else MediaType.parseMediaType("text/plain;charset=UTF-8")
    }

    companion object {
        val IMAGES = mapOf(
            "png" to "image/png", "jpg" to "image/jpeg", "jpeg" to "image/jpeg", "gif" to "image/gif", "webp" to "image/webp",
            "bmp" to "image/bmp", "ico" to "image/x-icon", "svg" to "image/svg+xml", "avif" to "image/avif",
        )
    }
}
