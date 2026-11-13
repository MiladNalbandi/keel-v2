package dev.keel.jetbrains.api

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The calls the IDE screens add to the phone's: review threads, keel's check, KeelBot's turn, Start a flow. */
class IdeApiTest {
    private lateinit var server: MockWebServer

    @Before
    fun up() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun down() {
        runCatching { server.shutdown() }
    }

    private fun api(token: String? = "web-key-demo") = KeelApi(server.url("/").toString(), token = { token })
    private fun json(code: Int, body: String) = MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    @Test
    fun `the web key of keel2 goes as a Bearer and the IDE says who it is`() = runBlocking {
        server.enqueue(json(200, """[{"id":"p1","name":"shop","root":"/projects/shop","branch":"main","waiting":1}]"""))
        val list = api().projects()
        assertEquals("shop", list.single().name)
        val req = server.takeRequest()
        assertEquals("Bearer web-key-demo", req.getHeader("Authorization"))
        assertEquals("jetbrains", req.getHeader("X-Keel-Via"))
    }

    @Test
    fun `reads a PR's review threads and pending comments`() = runBlocking {
        server.enqueue(
            json(
                200,
                """{"key":"pr:7","kind":"pr","number":7,"title":"Save the page size","author":"ana","base":"main","branch":"feat/paging",
                "checks":[{"name":"build","state":"success"},{"name":"test","state":"success"}],
                "files":[{"path":"src/Prefs.kt","status":"modified","added":5,"removed":1}],
                "threads":[
                  {"id":"T1","path":"src/Prefs.kt","line":2,"side":"RIGHT","resolved":false,"comments":[{"id":"c1","author":"bo","body":"This is a plain map. Two requests can save at the same time.","at":"2026-10-25T08:00:00Z"}]},
                  {"id":"T2","path":"src/Prefs.kt","line":4,"resolved":true,"comments":[{"id":"c2","author":"you","body":"save it in the repository too"},{"id":"c3","author":"ana","body":"done"}]}
                ],
                "drafts":[{"id":"d1","path":"src/Prefs.kt","line":3,"body":"nit"}],"me":"you","can_post":true,"unknown_field":{"a":1}}""",
            ),
        )
        val v = api().review("p1", "pr:7")
        assertEquals(2, v.threads.size)
        assertEquals("bo", v.threads[0].comments.single().author)
        assertEquals(ThreadState2.WAITING, threadStateOf(v.threads[0], v.me))
        assertEquals(ThreadState2.RESOLVED, threadStateOf(v.threads[1], v.me))
        assertEquals("CI 2/2 ✓", ciSummary(v.checks))
        assertEquals(1, v.drafts.size)
        assertEquals("/api/projects/p1/review/view?key=pr%3A7", server.takeRequest().path)
    }

    @Test
    fun `reply and resolve answer the new view`() = runBlocking {
        server.enqueue(json(200, """{"key":"pr:7","threads":[{"id":"T1","resolved":false,"comments":[{"id":"c1","author":"bo","body":"?"},{"id":"c9","author":"you","body":"Yes, fixed."}]}],"me":"you"}"""))
        server.enqueue(json(200, """{"key":"pr:7","threads":[{"id":"T1","resolved":true,"comments":[]}]}"""))
        val v = api().reply("p1", "pr:7", "T1", "Yes, fixed.")
        assertEquals(ThreadState2.OPEN, threadStateOf(v.threads.single(), v.me))
        assertTrue(api().resolve("p1", "pr:7", "T1", true).threads.single().resolved)
        val r1 = server.takeRequest()
        assertEquals("/api/projects/p1/review/threads/T1/reply", r1.path)
        assertEquals("""{"key":"pr:7","body":"Yes, fixed."}""", r1.body.readUtf8())
        assertEquals("""{"key":"pr:7","resolved":true}""", server.takeRequest().body.readUtf8())
    }

    @Test
    fun `keel's check runs in the background and is read with ai`() = runBlocking {
        server.enqueue(json(200, """{"judges":{"T1":{"id":"j1","kind":"judge","status":"running"}}}"""))
        server.enqueue(
            json(
                200,
                """{"judges":{"T1":{"id":"j1","status":"done","result":{"items":[{"claim":"Two saves can lose a write","verdict":"reviewer_right",
                "why":"savePageSize writes to a plain mutableMapOf","fix":"ConcurrentHashMap<String, Int>()"}],"reply":"Good catch, fixed.","tokens":4100}}}}""",
            ),
        )
        val started = api().judge("p1", "pr:7", "T1", harder = true)
        assertEquals("running", started.judges["T1"]?.status)
        val done = api().ai("p1", "pr:7").judges.getValue("T1")
        assertEquals("REVIEWER IS RIGHT", verdictLabel(done.result!!.items.single().verdict))
        assertEquals("Good catch, fixed.", done.result?.reply)
        assertEquals("""{"key":"pr:7","harder":true}""", server.takeRequest().body.readUtf8())
        assertEquals("/api/projects/p1/review/ai?key=pr%3A7", server.takeRequest().path)
    }

    @Test
    fun `KeelBot's turn sends the text to the chat`() = runBlocking {
        server.enqueue(json(200, """{"id":"s1","mode":"edit","status":"idle"}"""))
        server.enqueue(json(200, """{"session":"s1","call_id":"c-42","n":3}"""))
        server.enqueue(json(200, """[{"path":"orders.py","status":"modified","added":4,"removed":1}]"""))
        val s = api().newChat("p1", "edit")
        val t = api().send("p1", s.id, "Make list_orders return newest first, 20 per page.")
        assertEquals("c-42", t.callId)
        val ch = api().changes("p1", "s1").single()
        assertEquals(4, ch.added)
        assertEquals("""{"mode":"edit"}""", server.takeRequest().body.readUtf8())
        val turn = server.takeRequest()
        assertEquals("/api/projects/p1/helper/sessions/s1/turn", turn.path)
        assertEquals("""{"text":"Make list_orders return newest first, 20 per page."}""", turn.body.readUtf8())
        assertEquals("/api/projects/p1/helper/sessions/s1/changes", server.takeRequest().path)
    }

    @Test
    fun `start a flow sends where, run mode, cap and options`() = runBlocking {
        server.enqueue(json(200, """{"thread_id":"t9","title":"Add refunds to orders","status":"running"}"""))
        val t = api().startFlow(
            "p1",
            StartFlowBody("feature", "Add refunds to orders", "A customer can get money back.", where = "worktree", runMode = "important", capTokens = 300_000, options = mapOf("effort" to "high")),
        )
        assertEquals("t9", t.threadId)
        val req = server.takeRequest()
        assertEquals("/api/projects/p1/flows", req.path)
        assertEquals(
            """{"workflow_id":"feature","title":"Add refunds to orders","request":"A customer can get money back.","where":"worktree","run_mode":"important","cap_tokens":300000,"options":{"effort":"high"}}""",
            req.body.readUtf8(),
        )
    }

    @Test
    fun `a gate answer that comes too late is a 409, not a lost sign-in`() = runBlocking {
        server.enqueue(json(409, """{"error":"This flow now asks something else","hint":"Read it again."}"""))
        val form = GateForm.of("t1", Waiting(title = "Approve the spec?", options = listOf("approve", "reject"), id = "q-1"))
        val a = form.action() as GateAction.Answer
        val e = runCatching { api().answer(a.threadId, a.body) }.exceptionOrNull() as KeelException
        assertEquals(409, e.status)
        assertFalse(e.deviceRemoved)
        assertEquals("""{"decision":"approve","id":"q-1"}""", server.takeRequest().body.readUtf8())
    }
}
