package keel.api.pluginhost

import keel.api.common.Conflict
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.ExitCodeGenerator
import org.springframework.boot.SpringApplication
import org.springframework.context.ApplicationContext
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.system.exitProcess

/** Ends the api with an exit code. Tests put a fake one in its place. */
fun interface ApiExit {
    fun exit(code: Int)
}

/** The real one: stops Spring as on a normal stop, then ends the JVM with the code. */
@Component
class JvmExit(private val context: ApplicationContext) : ApiExit {
    override fun exit(code: Int) {
        exitProcess(SpringApplication.exit(context, ExitCodeGenerator { code }))
    }
}

/**
 * Restarts keel so a newly installed or changed plugin loads. Only keel-start can do this (it runs the api with
 * KEEL_SUPERVISED=1): the api ends with code 75, and keel-start resolves the plugins again and starts everything.
 */
@Service
class PluginRestart(@Value("\${keel.supervised:}") supervised: String, private val exit: ApiExit) {
    private val supervised = supervised.trim() == "1"
    private val asked = AtomicBoolean(false)

    /** Answers at once; the api ends about [DELAY_MS] later, so the answer reaches the browser first. */
    fun restart() {
        if (!supervised) {
            throw Conflict("keel can restart itself only when keel-start runs it (KEEL_SUPERVISED=1)", "Restart keel by hand: keel2 restart")
        }
        if (!asked.compareAndSet(false, true)) return
        // Not a daemon: SpringApplication.exit stops Tomcat's threads, and with only daemon threads left the JVM would
        // end by itself with code 0 before exitProcess(75) runs (keel-start then stops the container). Set it here:
        // a new thread copies the flag of the thread that makes it, and Tomcat's request threads are daemons.
        Thread({
            Thread.sleep(DELAY_MS)
            exit.exit(EXIT_CODE)
        }, "keel-restart").apply { isDaemon = false }.start()
    }

    companion object {
        /** keel-start's code for "resolve the plugins again and start again". */
        const val EXIT_CODE = 75
        const val DELAY_MS = 500L
    }
}
