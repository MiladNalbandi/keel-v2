package keel.api.jira

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.support.StubJira
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import java.net.ServerSocket
import java.time.Duration

/** v0.5.0: keel's Jira client against a local stub of Jira Cloud and of Jira Server / Data Center. */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class JiraClientTest {
    private val mapper = jacksonObjectMapper()
    private val cloud = StubJira.start("cloud")
    private val server = StubJira.start("server")

    private fun client(s: StubJira, token: String = s.token) =
        JiraClient(s.kind, s.url + "/", if (s.cloud) s.email else null, token, mapper, Duration.ofSeconds(5))

    @BeforeEach
    fun reset() {
        listOf(cloud, server).forEach {
            it.reset()
            it.add("ABC-1", "Rank players", "To Do", "Story", "Players want ranks.\n\n- top ten\n- weekly")
            it.add("ABC-2", "Fix the tally", "In Progress", "Bug")
            it.add("ABC-3", "Old thing", "Done", "Task")
        }
    }

    @Test
    fun `test reads the user, with Basic auth on Cloud and Bearer on Server`() {
        val me = client(cloud).myself()
        assertThat(me.name).isEqualTo("Dev One")
        assertThat(me.accountId).isEqualTo("acc-dev-1")
        assertThat(cloud.calls.last().auth).startsWith("Basic ")
        val sm = client(server).myself()
        assertThat(sm.username).isEqualTo("dev1")
        assertThat(sm.accountId).isNull()
        assertThat(server.calls.last().auth).isEqualTo("Bearer ${server.token}")
        assertThat(client(cloud).base).isEqualTo(cloud.url)              // the trailing slash is gone
    }

    @Test
    fun `search uses the enhanced search on Cloud (pages, ADF) and the classic one on Server`() {
        cloud.add("ABC-4", "Fourth", "To Do")
        val c = JiraClient("cloud", cloud.url, cloud.email, cloud.token, mapper).search("assignee = currentUser() AND statusCategory != Done", 200)
        assertThat(c.map { it.key }).containsExactly("ABC-1", "ABC-2", "ABC-4")
        assertThat(cloud.calls.filter { it.path == "/rest/api/3/search/jql" }).hasSize(1)
        assertThat(cloud.calls.none { it.path == "/rest/api/2/search" }).isTrue()
        val one = c.first()
        assertThat(one.description).isEqualTo("Players want ranks.\n- top ten\n- weekly")   // ADF → text
        assertThat(one.status).isEqualTo("To Do")
        assertThat(one.category).isEqualTo("new")
        assertThat(one.type).isEqualTo("Story")
        assertThat(one.assignee).isEqualTo("Dev One")
        assertThat(one.priority).isEqualTo("High")

        // pages of 2 with nextPageToken
        val paged = JiraClient("cloud", cloud.url, cloud.email, cloud.token, mapper).search("x", 3)
        assertThat(paged.map { it.key }).containsExactly("ABC-1", "ABC-2", "ABC-3")

        val s = client(server).search("statusCategory != Done")
        assertThat(s.map { it.key }).containsExactly("ABC-1", "ABC-2")
        assertThat(s.first().description).isEqualTo("Players want ranks.\n\n- top ten\n- weekly")   // plain text on Server
        assertThat(server.calls.single { it.path == "/rest/api/2/search" }.query).contains("startAt=0").contains("fields=summary")
        assertThat(client(server).search("key in (ABC-3)").single().status).isEqualTo("Done")
    }

    @Test
    fun `a Cloud site without the enhanced search falls back to the classic one`() {
        cloud.enhancedSearch = false
        assertThat(client(cloud).search("statusCategory != Done").map { it.key }).containsExactly("ABC-1", "ABC-2")
        assertThat(cloud.calls.map { it.path }).containsSubsequence("/rest/api/3/search/jql", "/rest/api/2/search")
    }

    @Test
    fun `board issues come from the agile API on both`() {
        for (s in listOf(cloud, server)) {
            assertThat(client(s).boardIssues("7", "statusCategory != Done").map { it.key }).containsExactly("ABC-1", "ABC-2")
            assertThat(s.calls.last().path).isEqualTo("/rest/agile/1.0/board/7/issue")
            assertThat(s.calls.last().query).contains("jql=statusCategory%20%21%3D%20Done")
            assertThatThrownBy { client(s).boardIssues("99", null) }.isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.kind).isEqualTo("not_found")
                assertThat(it.message).contains("board 99").contains("404")
            }
        }
    }

    @Test
    fun `statuses, transitions and fields are discovered`() {
        for (s in listOf(cloud, server)) {
            val c = client(s)
            assertThat(c.projectStatuses("ABC").map { it.name }).containsExactly("To Do", "In Progress", "In Review", "Testing in PP", "Ready for Production", "Done")
            assertThat(c.allStatuses().first { it.name == "Done" }.category).isEqualTo("done")
            val t = c.transitions("ABC-1")
            assertThat(t.map { it.to }).doesNotContain("To Do").contains("In Progress", "Done")
            assertThat(t.first { it.to == "In Progress" }.name).isEqualTo("Go In Progress")
            val reviewers = c.fields().first { it.id == "customfield_10010" }
            assertThat(reviewers.type).isEqualTo("array")
            assertThat(reviewers.items).isEqualTo("user")
        }
    }

    @Test
    fun `a ticket moves by status or transition name, and not when it is already there`() {
        for (s in listOf(cloud, server)) {
            val c = client(s)
            val m = c.transitionTo("ABC-1", "in progress")
            assertThat(m.moved).isTrue()
            assertThat(m.from).isEqualTo("To Do")
            assertThat(m.to).isEqualTo("In Progress")
            assertThat(s.issues.getValue("ABC-1").status).isEqualTo("In Progress")
            assertThat(s.calls("POST", "/rest/api/2/issue/ABC-1/transitions").single().body!!["transition"]["id"].asText()).isEqualTo("t-In-Progress")
            assertThat(c.transitionTo("ABC-1", "In Progress").moved).isFalse()
            assertThat(c.transitionTo("ABC-1", "Go In Review").to).isEqualTo("In Review")      // a transition's own name
            s.noWayTo += "Done"
            assertThatThrownBy { c.transitionTo("ABC-1", "Done") }.isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.message).contains("no transition from \"In Review\" to \"Done\"")
                assertThat(it.hint).contains("It can go to:").contains("To Do")
            }
        }
    }

    @Test
    fun `comments are plain text through REST v2 on both`() {
        for (s in listOf(cloud, server)) {
            client(s).comment("ABC-2", "keel started the fix flow.")
            val call = s.calls("POST", "/rest/api/2/issue/ABC-2/comment").single()
            assertThat(call.body!!["body"].isTextual).isTrue()
            assertThat(s.issues.getValue("ABC-2").comments).containsExactly("keel started the fix flow.")
        }
    }

    @Test
    fun `the reviewer field gets account ids on Cloud and user names on Server`() {
        client(cloud).setUsers("ABC-1", "customfield_10010", listOf("rev@example.com", "acc-77"))
        assertThat(cloud.issues.getValue("ABC-1").fields["customfield_10010"]).isEqualTo(listOf(mapOf("accountId" to "acc-rev-1"), mapOf("accountId" to "acc-77")))
        client(server).setUsers("ABC-1", "customfield_10010", listOf("rev1"))
        assertThat(server.issues.getValue("ABC-1").fields["customfield_10010"]).isEqualTo(listOf(mapOf("name" to "rev1")))
        client(server).setUsers("ABC-1", "customfield_10020", listOf("lead1", "other"))
        assertThat(server.issues.getValue("ABC-1").fields["customfield_10020"]).isEqualTo(mapOf("name" to "lead1"))   // a single-user picker
        assertThatThrownBy { client(cloud).setUsers("ABC-1", "customfield_10010", listOf("nobody@example.com")) }
            .isInstanceOfSatisfying(JiraException::class.java) { assertThat(it.message).contains("no user with the email nobody@example.com") }
        assertThatThrownBy { client(cloud).setUsers("ABC-1", "customfield_404", listOf("x")) }
            .isInstanceOfSatisfying(JiraException::class.java) { assertThat(it.message).contains("no field customfield_404") }
    }

    @Test
    fun `errors say what is wrong and never show the token`() {
        for (s in listOf(cloud, server)) {
            val wrong = "wrong-token-SECRET-999"
            assertThatThrownBy { client(s, wrong).myself() }.isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.kind).isEqualTo("auth")
                assertThat(it.status).isEqualTo(401)
                assertThat(it.message).isEqualTo("Jira refused the login (401).")
                assertThat(it.hint).contains(if (s.cloud) "API token" else "personal access token")
                assertThat("${it.message} ${it.hint}").doesNotContain(wrong)
            }
            s.failures["/rest/api/2/issue/ABC-1/transitions"] = 403 to "You do not have permission to transition this issue. You sent {auth}"
            assertThatThrownBy { client(s).transitions("ABC-1") }.isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.kind).isEqualTo("forbidden")
                assertThat(it.message).contains("(403)").contains("You do not have permission").contains("***")
                assertThat(it.message).doesNotContain(s.token)
                assertThat(it.message).doesNotContain(s.calls.last().auth!!.substringAfter(' '))
            }
            assertThatThrownBy { client(s).issue("ABC-404") }.isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.kind).isEqualTo("not_found")
                assertThat(it.message).contains("ticket ABC-404 (404)").contains("Issue does not exist")
            }
            s.failures["/rest/api/2/issue/ABC-2/comment"] = 400 to "Field 'comment' is invalid"
            assertThatThrownBy { client(s).comment("ABC-2", "x") }.isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.kind).isEqualTo("bad_request")
                assertThat(it.message).contains("Field 'comment' is invalid")
            }
        }
        val port = ServerSocket(0).use { it.localPort }           // nothing listens there
        assertThatThrownBy { JiraClient("server", "http://127.0.0.1:$port", null, "tok-network-1234", mapper, Duration.ofSeconds(2)).myself() }
            .isInstanceOfSatisfying(JiraException::class.java) {
                assertThat(it.kind).isEqualTo("network")
                assertThat(it.message).isEqualTo("Could not reach Jira at http://127.0.0.1:$port: nothing answered (connection refused).")
                assertThat(it.message).doesNotContain("tok-network-1234")
            }
    }

    @Test
    fun `ADF becomes readable text`() {
        val doc = mapper.readTree("""{"type":"doc","content":[
            {"type":"heading","content":[{"type":"text","text":"Goal"}]},
            {"type":"paragraph","content":[{"type":"text","text":"Rank "},{"type":"mention","attrs":{"text":"@ana"}},{"type":"hardBreak"},{"type":"text","text":"weekly"}]},
            {"type":"orderedList","content":[{"type":"listItem","content":[{"type":"paragraph","content":[{"type":"text","text":"one"}]}]}]}]}""")
        assertThat(JiraClient.adfText(doc)).isEqualTo("Goal\nRank @ana\nweekly\n- one")
    }
}
