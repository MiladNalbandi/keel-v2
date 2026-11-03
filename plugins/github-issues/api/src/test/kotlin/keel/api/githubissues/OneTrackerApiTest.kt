package keel.api.githubissues

import keel.api.support.ApiTest
import keel.api.tasks.SyncInfo
import keel.api.tasks.SyncResult
import keel.api.tasks.TicketClient
import keel.api.tasks.TicketTracker
import keel.api.tasks.TrackerConnection
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** 0.22.0 a project follows one tracker: GitHub Issues is refused while another tracker (here a stand-in Jira) is connected. */
class OneTrackerApiTest : ApiTest() {

    @TestConfiguration
    class OtherTracker {
        @Bean
        fun standInJira(): TicketTracker = object : TicketTracker {
            override fun connection(pid: String) = if (pid in connected) TrackerConnection("https://jira.test") else null
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
    fun `GitHub Issues is refused while the project follows Jira`() {
        put("/api/secrets/GITHUB_REPO_TOKEN", mapOf("value" to github.token)).andExpect(status().isOk)
        try {
            val (pid, root) = newProject("one-tracker")
            git(root, "remote", "add", "origin", "https://github.com/acme/app.git")
            connected += pid
            put("/api/projects/$pid/github-issues", emptyMap<String, Any>()).andExpect(status().isConflict)
            assertThat(get("/api/projects/$pid/github-issues").json()["other_tracker"].asText()).isEqualTo("Jira")
            connected -= pid
            put("/api/projects/$pid/github-issues", emptyMap<String, Any>()).andExpect(status().isOk)
            assertThat(get("/api/projects/$pid/github-issues").json()["other_tracker"].isNull).isTrue()
        } finally {
            delete("/api/secrets/GITHUB_REPO_TOKEN")
        }
    }
}
