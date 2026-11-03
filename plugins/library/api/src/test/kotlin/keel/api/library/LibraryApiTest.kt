package keel.api.library

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/** The Library: the page sees all of a project's documents, an agent's door only those marked for agents. */
class LibraryApiTest : ApiTest() {
    @Autowired lateinit var jdbc: JdbcTemplate

    @BeforeEach
    fun table() {
        Files.readString(Path.of("../plugins/library/migrations/V1__library.sql")).replace(Regex("--.*"), "").split(";")
            .map { it.trim() }.filter { it.isNotEmpty() }.forEach { jdbc.execute(it) }
        jdbc.update("DELETE FROM library_doc")
    }

    private fun add(pid: String, title: String, body: String, vararg more: Pair<String, Any?>) =
        post("/api/projects/$pid/library/docs", mapOf("title" to title, "body" to body) + more).andExpect(status().isOk).json()

    private val policy = "# Data rules\n\n## Backups\n\nBackups run each night and stay 30 days.\n\n## Passwords\n\nPasswords are hashed.\nNobody reads them.\n"

    @Test
    fun `a search finds the passage, with its section and version, and says nothing when nothing fits`() {
        val (pid, _) = newProject("lib-search")
        val id = add(pid, "Data rules", policy)["id"].asText()
        val hits = get("/api/projects/$pid/library/search?q=how long do backups stay").andExpect(status().isOk).json()
        assertThat(hits[0]["doc_id"].asText()).isEqualTo(id)
        assertThat(hits[0]["section"].asText()).isEqualTo("Backups")
        assertThat(hits[0]["quote"].asText()).contains("stay 30 days").doesNotContain("Passwords")
        assertThat(hits[0]["version"].asInt()).isEqualTo(1)
        assertThat(get("/api/projects/$pid/library/search?q=parking spaces").json().size()).isEqualTo(0)
        assertThat(get("/api/projects/$pid/library/search?q=the").json().size()).isEqualTo(0)
    }

    @Test
    fun `a document for people only is on the page but never at the agents' door`() {
        val (pid, _) = newProject("lib-people")
        val open = add(pid, "Open rules", "The holiday rule is simple.")["id"].asText()
        val shut = add(pid, "Staff rules", "The holiday rule for staff.", "for_agents" to false)["id"].asText()
        assertThat(get("/api/projects/$pid/library/search?q=holiday").json().map { it["doc_id"].asText() }).containsExactlyInAnyOrder(open, shut)
        assertThat(get("/api/projects/$pid/library/agent/search?q=holiday").json().map { it["doc_id"].asText() }).containsExactly(open)
        get("/api/projects/$pid/library/agent/docs/$open").andExpect(status().isOk)
        get("/api/projects/$pid/library/agent/docs/$shut").andExpect(status().isNotFound)
        get("/api/projects/$pid/library/docs/$shut").andExpect(status().isOk)
    }

    @Test
    fun `a document for all projects shows everywhere, one for a project only there`() {
        val (a, _) = newProject("lib-a")
        val (b, _) = newProject("lib-b")
        val everywhere = add(a, "Shared rules", "The shared rule.")["id"].asText()
        val mine = add(a, "Own rules", "The own rule.", "scope" to "project")["id"].asText()
        assertThat(get("/api/projects/$a/library/docs").json()["docs"].map { it["id"].asText() }).containsExactlyInAnyOrder(everywhere, mine)
        assertThat(get("/api/projects/$b/library/docs").json()["docs"].map { it["id"].asText() }).containsExactly(everywhere)
        get("/api/projects/$b/library/docs/$mine").andExpect(status().isNotFound)
        get("/api/projects/$b/library/agent/docs/$mine").andExpect(status().isNotFound)
        assertThat(get("/api/projects/$b/library/agent/search?q=own rule").json().map { it["doc_id"].asText() }).doesNotContain(mine)
    }

    @Test
    fun `changing the words makes a new version, a switch does not, and the page says what can go to the model company`() {
        val (pid, _) = newProject("lib-change")
        val id = add(pid, "Rules", "Old words.")["id"].asText()
        assertThat(post("/api/projects/$pid/library/docs/$id", mapOf("for_agents" to false)).json()["version"].asInt()).isEqualTo(1)
        val d = post("/api/projects/$pid/library/docs/$id", mapOf("body" to "New words.")).json()
        assertThat(d["version"].asInt()).isEqualTo(2)
        assertThat(d["for_agents"].asBoolean()).isFalse()
        assertThat(d["title"].asText()).isEqualTo("Rules")
        val list = get("/api/projects/$pid/library/docs").json()
        assertThat(list["sends"].asText()).contains("model company").contains("marked for agents")
        assertThat(list["docs"][0].has("body")).isFalse()
    }

    @Test
    fun `a bad document is refused, and delete removes it`() {
        val (pid, _) = newProject("lib-bad")
        post("/api/projects/$pid/library/docs", mapOf("title" to "", "body" to "x")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/library/docs", mapOf("title" to "x", "body" to " ")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/library/docs", mapOf("title" to "x", "body" to "y".repeat(LibraryService.BODY_MAX + 1))).andExpect(status().isBadRequest)
        post("/api/projects/$pid/library/docs", mapOf("title" to "x", "body" to "y", "scope" to "galaxy")).andExpect(status().isBadRequest)
        val id = add(pid, "x", "y")["id"].asText()
        delete("/api/projects/$pid/library/docs/$id").andExpect(status().isOk)
        get("/api/projects/$pid/library/docs/$id").andExpect(status().isNotFound)
    }

    @Test
    fun `passages split at headings and blank lines, and a plural finds a singular`() {
        val ps = LibraryService.passages("intro\n\n# One\n\nfirst\nsecond\n\n## Two\nthird\n")
        assertThat(ps.map { it.section to it.text }).containsExactly(null to "intro", "One" to "first\nsecond", "Two" to "third")
        assertThat(LibraryService.words("Backups")).contains("backup")
    }
}
