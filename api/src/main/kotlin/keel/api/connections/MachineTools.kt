package keel.api.connections

import keel.api.common.Proc
import keel.api.common.ProcResult
import java.util.concurrent.Callable
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutorService
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/**
 * Which programs this machine has, and their versions (Connections › This machine). Every check that is not cached
 * runs at the same time on a small pool, each one at most `timeoutSec`, so the whole answer takes about as long as the
 * slowest single check; answers are cached for `ttlMs` (some CLIs are slow to start).
 */
class MachineTools(
    private val which: (String) -> String? = Proc::which,
    private val run: (List<String>, Long) -> ProcResult = { cmd, timeout -> Proc.run(cmd, null, timeout) },
    private val timeoutSec: Long = 5,
    private val ttlMs: Long = 60_000,
    private val pool: ExecutorService = sharedPool,
) {
    private data class Cached(val at: Long, val tool: MachineTool)
    private val cache = ConcurrentHashMap<String, Cached>()

    /** Every program by name ([DOCKER] is the Docker engine, not just its CLI), checked in parallel; in the given order. */
    fun check(names: List<String>): List<MachineTool> {
        val now = System.currentTimeMillis()
        val fresh = names.distinct().filter { n -> cache[n]?.takeIf { now - it.at < ttlMs } == null }
        val probed = parallel(fresh) { n -> runCatching { probe(n) }.getOrElse { MachineTool(display(n), false) } }
        fresh.zip(probed).forEach { (n, t) -> cache[n] = Cached(System.currentTimeMillis(), t) }
        return names.map { n -> cache[n]?.tool ?: MachineTool(display(n), false) }
    }

    fun check(name: String): MachineTool = check(listOf(name)).first()

    private fun display(name: String) = if (name == DOCKER) "docker" else name

    private fun probe(name: String): MachineTool = if (name == DOCKER) docker() else {
        val path = which(name)
        if (path == null) MachineTool(name, false) else {
            val r = run(listOf(path, "--version"), timeoutSec)
            MachineTool(name, true, r.out.ifBlank { r.err }.lineSequence().firstOrNull()?.trim()?.take(80)?.ifBlank { null })
        }
    }

    /** Can the project's tests use Docker? Needs the CLI and a reachable engine (keel2 --docker mounts the host's). */
    private fun docker(): MachineTool {
        val path = which("docker") ?: return MachineTool("docker", false, "not installed")
        val r = run(listOf(path, "version", "--format", "{{.Server.Version}}"), timeoutSec)
        return if (r.ok && r.out.isNotBlank()) MachineTool("docker", true, "engine ${r.out.trim()}")
        else MachineTool("docker", false, "CLI only — start with ./keel2 --docker so tests can use Docker")
    }

    /** `f` on every item at once (at most the pool's size at a time); the answers in the items' order. */
    private fun <T, R> parallel(items: List<T>, f: (T) -> R): List<R> = when (items.size) {
        0 -> emptyList()
        1 -> listOf(f(items[0]))
        else -> pool.invokeAll(items.map { Callable { f(it) } }).map { it.get() }
    }

    companion object {
        /** The key of the Docker engine check (`docker version`), next to plain `--version` checks. */
        const val DOCKER = "docker-engine"

        /** Eight threads at most (one per program Connections checks); idle ones end after 30 s. */
        private val sharedPool: ExecutorService = ThreadPoolExecutor(8, 8, 30, TimeUnit.SECONDS, LinkedBlockingQueue()) { r ->
            Thread(r, "keel-machine-tools").apply { isDaemon = true }
        }.apply { allowCoreThreadTimeOut(true) }
    }
}
