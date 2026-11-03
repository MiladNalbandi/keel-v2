package keel.api

import keel.api.common.Proc
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files

class ProcTest {
    @Test
    fun `a command that times out is killed with the processes it started`() {
        val pidFile = Files.createTempFile("keel-pid", ".txt")
        val r = Proc.run(listOf("sh", "-c", "sleep 60 & echo \$! > $pidFile; sleep 60"), timeoutSec = 1)
        assertThat(r.timedOut).isTrue()
        val pid = Files.readString(pidFile).trim().toLong()
        var alive = true
        repeat(30) { if (alive) { alive = ProcessHandle.of(pid).map { it.isAlive }.orElse(false); if (alive) Thread.sleep(100) } }
        assertThat(alive).describedAs("the background sleep ($pid) is still running").isFalse()
        ProcessHandle.of(pid).ifPresent { it.destroyForcibly() }
    }

    @Test
    fun `a child gets none of the api's secrets, only the variables it needs`() {
        val env = Proc.childEnv(mapOf("GIT_AUTHOR_NAME" to "keel"),
            mapOf("PATH" to "/usr/bin", "HOME" to "/home/keel", "LC_ALL" to "C.UTF-8", "KEEL_INTERNAL_TOKEN" to "t0k3n",
                "GITHUB_TOKEN" to "ghp_x", "KEEL_DATA" to "/data", "ANTHROPIC_API_KEY" to "sk-x"))
        assertThat(env).containsEntry("PATH", "/usr/bin").containsEntry("HOME", "/home/keel").containsEntry("LC_ALL", "C.UTF-8")
            .containsEntry("GIT_AUTHOR_NAME", "keel").containsEntry("GIT_TERMINAL_PROMPT", "0")
            .doesNotContainKeys("KEEL_INTERNAL_TOKEN", "GITHUB_TOKEN", "KEEL_DATA", "ANTHROPIC_API_KEY")
    }

    @Test
    fun `Proc run passes no secret of its own environment`() {
        val r = Proc.run(listOf("env"))
        assertThat(r.ok).isTrue()
        assertThat(r.out).doesNotContain("KEEL_INTERNAL_TOKEN").contains("GIT_TERMINAL_PROMPT=0")
    }

    @Test
    fun `every git turns the repo's fsmonitor and hooks off`() {
        assertThat(Proc.hardened(listOf("git", "status"))).containsExactly(
            "git", "-c", "protocol.ext.allow=never", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "status")
        assertThat(Proc.hardened(listOf("/usr/bin/git", "log")).subList(1, 7)).isEqualTo(Proc.GIT_SAFE_ARGS)
        assertThat(Proc.hardened(listOf("docker", "version"))).containsExactly("docker", "version")
    }

    @Test
    fun `a repo's fsmonitor never runs from the api's git status`() {
        val repo = Files.createTempDirectory("keel-fsmon")
        val marker = repo.resolve("ran")
        assertThat(Proc.run(listOf("git", "init", "-q"), repo).ok).isTrue()
        val evil = repo.resolve("evil.sh")
        Files.writeString(evil, "#!/bin/sh\ntouch $marker\n")
        evil.toFile().setExecutable(true)
        assertThat(Proc.run(listOf("git", "config", "core.fsmonitor", evil.toString()), repo).ok).isTrue()
        Proc.run(listOf("git", "status", "--porcelain"), repo)
        assertThat(Files.exists(marker)).describedAs("the fsmonitor ran").isFalse()
        repo.toFile().deleteRecursively()
    }
}
