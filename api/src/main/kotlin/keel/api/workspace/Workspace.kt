package keel.api.workspace

import keel.api.common.Proc
import keel.api.common.ProcResult
import org.springframework.stereotype.Service
import java.nio.file.Path

/**
 * A project's git folder as core sees it: run git there and find the base branch. Flows, the workspace Doctor, the
 * knowledge base, KeelBot and the Code page use it.
 *
 * Part of keel's api SDK (keel-api-sdk): a plugin may inject it to read or change a project's repo.
 */
interface Workspace {
    /** Runs `git <args>` with cwd = [root] and a timeout in seconds. A failing git does not throw: read `ok`, `out`, `err`. */
    fun git(root: Path, vararg args: String, timeout: Long = 10): ProcResult

    /** git's output, trimmed, or null when it fails. */
    fun gitOut(root: Path, vararg args: String): String?

    /** The base branch: main, else master, else null. */
    fun base(root: Path): String?

    /** When code last changed (commit time, seconds): the newest commit outside docs/ and .keel/. */
    fun lastCodeCommit(root: Path): Long?

    /** When [rel] last changed (commit time, seconds), or null when no commit touched it. */
    fun lastCommitTime(root: Path, rel: String): Long?
}

/** The [Workspace] keel runs: plain git in the project folder. */
@Service
class GitWorkspace : Workspace {

    override fun git(root: Path, vararg args: String, timeout: Long): ProcResult = Proc.run(listOf("git", *args), root, timeout)

    override fun gitOut(root: Path, vararg args: String): String? = git(root, *args).takeIf { it.ok }?.out?.trim()

    override fun base(root: Path): String? = listOf("main", "master").firstOrNull {
        git(root, "show-ref", "--verify", "--quiet", "refs/heads/$it").ok
    }

    override fun lastCodeCommit(root: Path): Long? =
        gitOut(root, "log", "-1", "--format=%ct", "--", ".", ":(exclude)docs", ":(exclude).keel")?.toLongOrNull()

    override fun lastCommitTime(root: Path, rel: String): Long? = gitOut(root, "log", "-1", "--format=%ct", "--", rel)?.toLongOrNull()
}
