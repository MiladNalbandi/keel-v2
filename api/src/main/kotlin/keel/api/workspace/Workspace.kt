package keel.api.workspace

import keel.api.common.Proc
import keel.api.common.ProcResult
import keel.api.common.Yaml
import org.springframework.stereotype.Service
import java.nio.file.Files
import java.nio.file.Path

/**
 * 0.18.65 the project's base as keel compares work with it (the engine's runtime/bases.py resolve, field report R2):
 *
 * [name] the base branch's name (base_branch without its remote; main or master when the config names none),
 * [local] that branch here (null: the repo has no such local branch), [remote] the canonical remote's copy
 * (`<remote>/<name>`, else that remote's default branch when git.remote names it or there is no local base, else the
 * local branch's upstream), [ref] what work is compared with (the remote copy when there is one, else the local branch),
 * [ahead] / [behind] the local base against the remote copy, [warning] that drift in words.
 */
data class BaseInfo(
    val name: String?,
    val local: String?,
    val remote: String?,
    val canonical: String?,
    val ref: String?,
    val ahead: Int? = null,
    val behind: Int? = null,
    val warning: String? = null,
    /** base_branch names the remote itself (github/main) */
    val explicit: Boolean = false,
    /** base_branch as .keel/config.yml writes it */
    val configured: String? = null,
) {
    /** The ref as git reads it: refs/remotes/<remote> or refs/heads/<local>. */
    val fullRef: String? get() = remote?.let { "refs/remotes/$it" } ?: local?.let { "refs/heads/$it" }

    /** base_branch names a branch with no local copy (R5): the person chooses, keel never guesses. */
    val missing: Boolean get() = !configured.isNullOrBlank() && !explicit && local == null

    /** R5 a start asks first: base_branch names no local branch and a remote has a branch of that name. */
    val mustAsk: Boolean get() = missing && remote != null && remote.substringAfter('/') == name
}

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

    /** The local base branch: 0.18.65 base_branch from .keel/config.yml (its branch part: github/main is main) when the
     *  repo has it, else main, else master, else null. What work is compared with is [baseInfo]'s ref: the canonical
     *  remote's copy when there is one. */
    fun base(root: Path): String?

    /** 0.18.65 the base work is compared with, and its canonical remote (BaseInfo). */
    fun baseInfo(root: Path): BaseInfo = base(root).let { BaseInfo(it, it, null, null, it) }

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

    override fun base(root: Path): String? =
        baseInfo(root).local ?: listOf("main", "master").firstOrNull { local(root, it) }

    private fun local(root: Path, name: String) = name.isNotBlank() && git(root, "show-ref", "--verify", "--quiet", "refs/heads/$name").ok
    private fun remoteRef(root: Path, ref: String) = ref.isNotBlank() && git(root, "show-ref", "--verify", "--quiet", "refs/remotes/$ref").ok

    private fun config(root: Path): Map<String, Any?> {
        val f = root.resolve(".keel/config.yml")
        return if (Files.isRegularFile(f)) runCatching { Yaml.readMap(Files.readString(f)) }.getOrNull() ?: emptyMap() else emptyMap()
    }

    /** (remote, branch) of a base name: github/main is (github, main) when github is a remote; feat/x stays local. */
    private fun split(value: String, remotes: List<String>): Pair<String?, String> {
        val v = value.trim().removePrefix("refs/heads/").removePrefix("refs/remotes/")
        val first = v.substringBefore('/')
        val rest = v.substringAfter('/', "")
        return if (rest.isNotEmpty() && first in remotes) first to rest else null to v
    }

    private fun upstream(root: Path, name: String): String? {
        if (!local(root, name)) return null
        val up = gitOut(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "$name@{upstream}") ?: return null
        return up.takeIf { it.isNotBlank() && remoteRef(root, it) }
    }

    private fun remoteDefault(root: Path, name: String): String? =
        gitOut(root, "symbolic-ref", "-q", "--short", "refs/remotes/$name/HEAD")?.takeIf { it.isNotBlank() }
            ?: listOf("main", "master").firstOrNull { remoteRef(root, "$name/$it") }?.let { "$name/$it" }

    /** The same order as the engine's bases.canonical_remote: git.remote, the remote base_branch names, the base's
     *  upstream's remote, a remote that has the base branch (origin first), origin, the only remote. */
    private fun canonical(root: Path, setting: String?, named: String?, name: String, remotes: List<String>): String? {
        if (remotes.isEmpty()) return null
        if (setting != null && setting in remotes) return setting
        if (named != null) return named
        upstream(root, name)?.substringBefore('/')?.takeIf { it in remotes }?.let { return it }
        remotes.sortedBy { it != "origin" }.firstOrNull { name.isNotBlank() && remoteRef(root, "$it/$name") }?.let { return it }
        if ("origin" in remotes) return "origin"
        return remotes.singleOrNull()
    }

    override fun baseInfo(root: Path): BaseInfo {
        if (!git(root, "rev-parse", "--git-dir").ok) return BaseInfo(null, null, null, null, null)
        val cfg = config(root)
        val configured = cfg["base_branch"]?.toString()?.trim()?.removePrefix("refs/heads/")?.ifBlank { null }
        val setting = ((cfg["git"] as? Map<*, *>)?.get("remote"))?.toString()?.trim()?.ifBlank { null }
        val remotes = gitOut(root, "remote")?.lines()?.map { it.trim() }?.filter { it.isNotEmpty() }.orEmpty()
        val (named, part) = split(configured.orEmpty(), remotes)
        val name = part.ifBlank { listOf("main", "master").firstOrNull { local(root, it) }.orEmpty() }
        val canonical = canonical(root, setting, named, name, remotes)
        val localBase = name.takeIf { local(root, it) }
        var remote: String? = null
        if (named != null) {
            remote = "$named/$name".takeIf { remoteRef(root, it) }
        } else if (canonical != null) {
            remote = when {
                name.isNotBlank() && remoteRef(root, "$canonical/$name") -> "$canonical/$name"
                setting == canonical || localBase == null -> remoteDefault(root, canonical)
                else -> null
            }
        }
        if (remote == null && localBase != null) remote = upstream(root, localBase)
        val counts = if (localBase != null && remote != null)
            gitOut(root, "rev-list", "--left-right", "--count", "refs/heads/$localBase...refs/remotes/$remote")
                ?.split(Regex("\\s+"))?.mapNotNull { it.toIntOrNull() }?.takeIf { it.size == 2 } else null
        val warning = counts?.takeIf { it[0] > 0 || it[1] > 0 }?.let { "${drift(localBase!!, remote!!, it[0], it[1])}; keel compares with $remote" }
        return BaseInfo(name.ifBlank { null }, localBase, remote, canonical, remote ?: localBase, counts?.get(0), counts?.get(1),
            warning, named != null, configured)
    }

    override fun lastCodeCommit(root: Path): Long? =
        gitOut(root, "log", "-1", "--format=%ct", "--", ".", ":(exclude)docs", ":(exclude).keel")?.toLongOrNull()

    override fun lastCommitTime(root: Path, rel: String): Long? = gitOut(root, "log", "-1", "--format=%ct", "--", rel)?.toLongOrNull()

    companion object {
        private fun n(k: Int) = "$k commit${if (k == 1) "" else "s"}"

        /** "the local master is 3 commits behind and 3 ahead of github/main" (the engine's bases.drift_words). */
        fun drift(local: String, remote: String, ahead: Int, behind: Int): String = when {
            ahead > 0 && behind > 0 -> "the local $local is ${n(behind)} behind and $ahead ahead of $remote"
            behind > 0 -> "the local $local is ${n(behind)} behind $remote"
            else -> "the local $local is ${n(ahead)} ahead of $remote (commits only it has)"
        }
    }
}
