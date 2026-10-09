package keel.api.projects

import keel.api.common.Forbidden
import keel.api.common.KeelProperties
import keel.api.common.NotFound
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

// v0.15.7 Add projects from your folders: keel finds the git repos inside the folders it was given (KEEL_FOLDERS) and
// lets the web app walk those folders, so a person ticks repos instead of typing paths.

data class FolderRoot(val path: String, val exists: Boolean)

data class FoundRepo(val path: String, val name: String, val root: String, val projectId: String?)

data class Folders(val roots: List<FolderRoot>, val repos: List<FoundRepo>, val truncated: Boolean)

data class BrowseDir(val path: String, val name: String, val repo: Boolean, val projectId: String?)

data class Browse(val path: String, val root: String, val parent: String?, val dirs: List<BrowseDir>, val truncated: Boolean)

@Service
class FolderService(private val props: KeelProperties, private val projects: ProjectService) {

    /** The folders keel looks in, with whether each one is there. */
    fun roots(): List<FolderRoot> = props.folderRoots.map { FolderRoot(it.toString(), Files.isDirectory(it)) }

    /**
     * The git repos inside every root: the root itself when it is a repo, else its children, grandchildren and
     * great-grandchildren. A repo is not looked into (its submodules are its own business). At most [MAX_REPOS].
     */
    fun list(): Folders {
        val ids = projects.idsByRoot()
        val repos = mutableListOf<FoundRepo>()
        val seen = mutableSetOf<Path>()
        val inside = realRoots()
        var truncated = false
        for (root in props.folderRoots) {
            if (!Files.isDirectory(root)) continue
            val found = mutableListOf<Path>()
            if (isRepo(root)) found.add(root) else truncated = walk(root, 1, found, seen, inside) || truncated
            for (p in found.sorted()) {
                if (repos.size >= MAX_REPOS) {
                    truncated = true
                    break
                }
                if (repos.none { it.path == p.toString() }) repos.add(FoundRepo(p.toString(), p.fileName?.toString() ?: p.toString(), root.toString(), ids[p.toString()]))
            }
        }
        return Folders(roots(), repos, truncated)
    }

    /** Looks for repos under `dir` (depth = its children's depth below the root); true when it stopped at the cap. */
    private fun walk(dir: Path, depth: Int, found: MutableList<Path>, seen: MutableSet<Path>, inside: List<Path>): Boolean {
        if (depth > MAX_DEPTH) return false
        val real = runCatching { dir.toRealPath() }.getOrNull() ?: return false
        if (!seen.add(real)) return false // a symlink loop, or a folder reached twice
        for (child in children(dir, inside)) {
            if (found.size >= MAX_REPOS) return true
            if (isRepo(child)) found.add(child)
            else if (walk(child, depth + 1, found, seen, inside)) return true
        }
        return false
    }

    /**
     * One folder's sub folders, for the web app's folder walk. Only inside a root or keel's data folder: a path with
     * `..`, or one that leaves them (also through a symlink), is refused with 403. A blank path is the first root.
     */
    fun browse(pathText: String?): Browse {
        // listOf: a Path is Iterable, so `list + path` would add its name parts
        val allowed = (props.folderRoots + listOf(props.dataDir)).distinct()
        val raw = pathText?.trim().orEmpty()
        val path = if (raw.isEmpty()) {
            props.folderRoots.firstOrNull { Files.isDirectory(it) } ?: props.dataDir
        } else {
            if (raw.split('/', '\\').any { it == ".." }) throw outside(raw)
            Paths.get(raw).toAbsolutePath().normalize()
        }
        val root = allowed.filter { path.startsWith(it) }.maxByOrNull { it.nameCount } ?: throw outside(raw)
        if (!Files.isDirectory(path)) throw NotFound("There is no folder at $path", "GET /api/folders lists the folders keel may look in.")
        val inside = realRoots()
        val real = runCatching { path.toRealPath() }.getOrNull() ?: throw outside(raw)
        if (inside.none { real.startsWith(it) }) throw outside(raw)
        val ids = projects.idsByRoot()
        val all = children(path, inside)
        val dirs = all.take(MAX_DIRS).map { BrowseDir(it.toString(), it.fileName.toString(), isRepo(it), ids[it.toString()]) }
        val parent = if (path == root) null else path.parent?.toString()
        return Browse(path.toString(), root.toString(), parent, dirs, all.size > MAX_DIRS)
    }

    /** The roots and the data folder with their symlinks resolved: where a followed link may land. */
    private fun realRoots(): List<Path> =
        (props.folderRoots + listOf(props.dataDir)).mapNotNull { runCatching { it.toRealPath() }.getOrNull() }.distinct()

    /** Sub folders worth showing: no hidden ones, no build output or dependencies, no symlink that leaves the roots. */
    private fun children(dir: Path, inside: List<Path>): List<Path> =
        runCatching {
            Files.list(dir).use { s ->
                s.filter { Files.isDirectory(it) }
                    .filter { val n = it.fileName.toString(); !n.startsWith(".") && n !in SKIP }
                    .filter { !Files.isSymbolicLink(it) || runCatching { it.toRealPath() }.getOrNull()?.let { r -> inside.any { ok -> r.startsWith(ok) } } == true }
                    .toList()
            }
        }.getOrDefault(emptyList()).sortedBy { it.fileName.toString().lowercase() }

    private fun outside(path: String) =
        Forbidden("$path is not inside the folders keel may look in", "Add the folder with `keel2 add <folder>`, then restart keel.")

    private fun isRepo(p: Path) = Files.exists(p.resolve(".git"))

    companion object {
        const val MAX_DEPTH = 3
        const val MAX_REPOS = 500
        const val MAX_DIRS = 1000
        val SKIP = setOf("node_modules", "build", "dist", "target", "out", "vendor", "__pycache__", "venv")
    }
}
