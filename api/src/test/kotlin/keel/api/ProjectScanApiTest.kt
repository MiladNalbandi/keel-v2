package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

/**
 * Plan 5b: a project is scanned when it is added (core: the engine's scan runs the parts' on_scan hooks). The index
 * status and its rebuild are the Graph plugin's (plugins/graph/api, GraphApiTest).
 */
class ProjectScanApiTest : ApiTest() {

    private fun waitFor(what: String, check: () -> Boolean) {
        val until = System.currentTimeMillis() + 5000
        while (System.currentTimeMillis() < until) {
            if (check()) return
            Thread.sleep(20)
        }
        throw AssertionError("timed out waiting for $what")
    }

    @Test
    fun `adding a project starts a scan of its folder`() {
        val (pid, root) = newProject("index-scan")
        waitFor("the scan call") { engine.calls.any { it.path == "/projects/$pid/scan" } }
        val scan = engine.lastBody("/projects/$pid/scan")!!
        assertThat(scan["root"].asText()).isEqualTo(root.toString())
        assertThat(scan["rebuild"].asBoolean()).isFalse()
    }
}
