package keel.api.jira

import keel.api.support.ApiTest
import keel.api.tasks.SyncInfo
import keel.api.tasks.SyncResult
import keel.api.tasks.TicketClient
import keel.api.tasks.TicketTracker
import keel.api.tasks.TrackerConnection
import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** 0.21.4 a project follows one tracker: Jira is refused while another one (here a stand-in GitHub Issues) is connected. */
class JiraOneTrackerApiTest : ApiTest() {

    @TestConfiguration
    class OtherTracker {
        @Bean
        fun standInGitHub(): TicketTracker = object : TicketTracker {
            override val source = "github"
            override val title = "GitHub"
            override fun connection(pid: String) = if (pid in connected) TrackerConnection("https://github.com/acme/app") else null
            override fun client(pid: String): TicketClient? = null
            override fun syncInfo(pid: String) = SyncInfo.NONE
            override fun sync(pid: String): SyncResult? = null
            override fun poll() {}
        }
    }

    companion object {
        val connected: MutableSet<String> = java.util.concurrent.ConcurrentHashMap.newKeySet()
    }

    @Test
    fun `Jira is refused while the project follows GitHub Issues, and the keel-wide site is not a project`() {
        val (pid, _) = newProject("jira-one-tracker")
        val body = mapOf("kind" to "server", "base_url" to "http://127.0.0.1:9", "token" to "t-0000")
        connected += pid
        put("/api/projects/$pid/jira", body).andExpect(status().isConflict)
        connected -= pid
        put("/api/projects/$pid/jira", body).andExpect(status().isOk)
        delete("/api/projects/$pid/jira").andExpect(status().isOk)
    }
}
