package keel.api

import keel.api.pluginhost.ApiExit
import keel.api.pluginhost.PluginRestart
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The restart's own thread (no Spring). */
class PluginRestartTest {
    @Test
    fun `the restart thread is not a daemon, even when a daemon thread asks for it (as Tomcat's request threads are)`() {
        val done = CountDownLatch(1)
        var daemon: Boolean? = null
        var code: Int? = null
        val restart = PluginRestart("1", ApiExit { c ->
            daemon = Thread.currentThread().isDaemon
            code = c
            done.countDown()
        })
        // a new thread copies the daemon flag of the thread that makes it; Tomcat's request threads are daemons, and a
        // daemon restart thread lets the JVM end with code 0 once Spring has stopped, before the exit with 75
        val asker = Thread { restart.restart() }.apply { isDaemon = true }
        asker.start()
        asker.join()
        assertThat(done.await(5, TimeUnit.SECONDS)).isTrue()
        assertThat(daemon).isFalse()
        assertThat(code).isEqualTo(PluginRestart.EXIT_CODE)
    }
}
