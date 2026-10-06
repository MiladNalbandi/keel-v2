package keel.api

import keel.api.common.ProcResult
import keel.api.connections.MachineTools
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.atomic.AtomicInteger

/** Connections › This machine: the version checks run at the same time, so the answer takes about the slowest one. */
class MachineToolsTest {

    private val names = listOf("node", "git", "java", MachineTools.DOCKER, "claude", "codex", "copilot", "opencode")

    /** Stand-in programs that take `seconds` to print their version (docker prints its engine version). */
    private fun slowBin(seconds: Double): Path {
        val dir = Files.createTempDirectory("keel-slow-bin")
        (names - MachineTools.DOCKER + "docker").forEach { n ->
            val f = dir.resolve(n)
            val answer = if (n == "docker") "27.1.0" else "$n 1.0.0"
            Files.writeString(f, "#!/bin/sh\nsleep $seconds\necho '$answer'\n")
            f.toFile().setExecutable(true)
        }
        return dir
    }

    @Test
    fun `eight slow version checks take about the slowest one, not their sum`() {
        val bin = slowBin(1.0)
        val tools = MachineTools(which = { n -> bin.resolve(n).toString() })
        val t0 = System.nanoTime()
        val found = tools.check(names)
        val ms = (System.nanoTime() - t0) / 1_000_000
        assertThat(ms).describedAs("8 checks of 1 s each, one after another, took 8 s").isLessThan(3_000)
        assertThat(found.map { it.name }).containsExactly("node", "git", "java", "docker", "claude", "codex", "copilot", "opencode")
        assertThat(found.all { it.ok }).isTrue()
        assertThat(found.first { it.name == "claude" }.version).isEqualTo("claude 1.0.0")
        assertThat(found.first { it.name == "docker" }.version).isEqualTo("engine 27.1.0")

        // cached for a minute: the second answer runs nothing
        val t1 = System.nanoTime()
        tools.check(names)
        assertThat((System.nanoTime() - t1) / 1_000_000).isLessThan(500)
    }

    @Test
    fun `each check keeps its timeout and a missing program runs nothing`() {
        val runs = AtomicInteger()
        val tools = MachineTools(
            which = { n -> if (n == "copilot" || n == "docker") null else "/bin/$n" },
            run = { cmd, timeout ->
                runs.incrementAndGet()
                assertThat(timeout).isEqualTo(5)
                Thread.sleep(300)
                if (cmd[0].endsWith("codex")) ProcResult(-1, "", "", timedOut = true) else ProcResult(0, "${cmd[0].substringAfterLast('/')} 2.0\nmore", "")
            },
        )
        val t0 = System.nanoTime()
        val found = tools.check(names).associateBy { it.name }
        assertThat((System.nanoTime() - t0) / 1_000_000).isLessThan(1_500)     // 6 × 300 ms one after another = 1.8 s
        assertThat(runs.get()).isEqualTo(6)
        assertThat(found["copilot"]!!.ok).isFalse()
        assertThat(found["docker"]!!.ok).isFalse()
        assertThat(found["docker"]!!.version).isEqualTo("not installed")
        assertThat(found["node"]!!.version).isEqualTo("node 2.0")
        assertThat(found["codex"]!!.ok).isTrue()                               // installed, but it did not answer in time
        assertThat(found["codex"]!!.version).isNull()
    }

    @Test
    fun `a check that throws reports the program as missing instead of failing the page`() {
        val tools = MachineTools(which = { n -> if (n == "git") error("boom") else null })
        val found = tools.check(listOf("git", "node"))
        assertThat(found.map { it.name to it.ok }).containsExactly("git" to false, "node" to false)
    }
}
