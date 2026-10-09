package keel.api.helper

import keel.api.flow.FlowContributor
import keel.api.flow.TaskSink
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/**
 * KeelBot with other parts, through core's extension points only (no other plugin on this test's classpath): a
 * [FlowContributor]'s keys ride along with every turn (the Database plugin's connections do), and "Make a task" hands a
 * side session to the one [TaskSink] (the Tasks plugin gives it). Its own context (the two beans), keel's test data.
 */
class HelperPartsApiTest : ApiTest() {
    @Autowired lateinit var sink: RecordingSink

    /** A Tasks plugin of its own: it keeps what it was asked. */
    class RecordingSink : TaskSink {
        val made = mutableListOf<Map<String, String>>()
        override fun create(pid: String, title: String, description: String, type: String): Any {
            made += mapOf("pid" to pid, "title" to title, "description" to description, "type" to type)
            return mapOf("id" to "T-${made.size}", "title" to title, "type" to type, "status" to "todo")
        }
    }

    @TestConfiguration
    class Parts {
        @Bean
        fun recordingSink() = RecordingSink()

        @Bean
        fun dbLikeKeys(): FlowContributor = object : FlowContributor {
            override fun keys(pid: String) = mapOf("db:local" to """{"name":"local","url":"sqlite:app.db"}""")
        }
    }

    @Test
    fun `a turn carries every contributor's keys`() {
        val (pid, _) = newProject("keelbot-keys")
        val sid = post("/api/projects/$pid/helper/sessions", emptyMap<String, Any>()).json()["id"].asText()
        post("/api/projects/$pid/helper/sessions/$sid/turn", mapOf("text" to "How many scores?")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid/turn")!!["keys"]["db:local"].asText()).contains("sqlite:app.db")
    }

    @Test
    fun `a side session is handed over as a task, with what it did and where the work is`() {
        val (pid, root) = newProject("keelbot-side-task")
        val sid = post("/api/projects/$pid/helper/sessions", mapOf("mode" to "side")).andExpect(status().isOk).json()["id"].asText()
        val sha = git(root, "rev-parse", "HEAD").trim()
        git(root, "branch", "keel/helper/abc")
        val before = engine.helperHandover
        engine.helperHandover = engine.helperHandover + mapOf("base" to sha,
            "commits" to listOf(mapOf("sha" to "1234567abc", "subject" to "fix(helper): price helper")))
        try {
            val t = post("/api/projects/$pid/helper/sessions/$sid/task", mapOf("type" to "story")).andExpect(status().isOk).json()
            assertThat(t["title"].asText()).isEqualTo("Add a price helper")
            assertThat(t["status"].asText()).isEqualTo("todo")
            val asked = sink.made.last()
            assertThat(asked["pid"]).isEqualTo(pid)
            assertThat(asked["type"]).isEqualTo("story")
            assertThat(asked["description"]).contains("branch `keel/helper/abc`").contains("1234567 fix(helper): price helper")
                .contains("- Add a price helper").contains("Added it.")
            post("/api/projects/$pid/helper/sessions/nope/task", mapOf("type" to "story")).andExpect(status().isNotFound)
        } finally {
            engine.helperHandover = before
        }
    }
}
