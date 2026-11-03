package keel.api.notify

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import com.sun.net.httpserver.HttpServer
import keel.api.common.KvStore
import keel.api.connections.SecretService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

/** Real sending goes only through a channel the person added; a message holds only short lines, and no secret ever shows. */
class NotifyApiTest : ApiTest() {
    @Autowired lateinit var kv: KvStore
    @Autowired lateinit var secrets: SecretService

    private val posts = CopyOnWriteArrayList<String>()
    private var status = 200
    private lateinit var hook: HttpServer

    @BeforeEach
    fun start() {
        kv.delete(NotifyService.CHANNELS)
        kv.delete(NotifyService.SENT)
        hook = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        hook.createContext("/hook") { x ->
            posts += x.requestBody.readAllBytes().toString(Charsets.UTF_8)
            x.sendResponseHeaders(status, -1)
            x.close()
        }
        hook.start()
    }

    @AfterEach
    fun stop() = hook.stop(0)

    private val hookUrl get() = "http://127.0.0.1:${hook.address.port}/hook/SECRET-PART"

    private fun chat(vararg more: Pair<String, Any?>) =
        post("/api/plugins/notify/channels", mapOf("kind" to "chat", "name" to "team chat", "url" to hookUrl) + more).andExpect(status().isOk).json()

    private fun waitingEvent(pid: String, tid: String, step: String = "spec_gate") = post("/internal/events", listOf(
        mapOf("type" to "thread.started", "thread_id" to tid, "project_id" to pid, "at" to java.time.Instant.now().toString(), "data" to mapOf("title" to "Export invoices")),
        mapOf("type" to "gate.waiting", "thread_id" to tid, "project_id" to pid, "step" to step, "at" to java.time.Instant.now().toString(),
            "data" to mapOf("step_name" to "spec approval", "title" to "spec approval", "detail" to "SECRET CODE IN THE GATE TEXT", "kind" to "gate")),
    ), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    private fun eventually(check: () -> Boolean) {
        val until = System.currentTimeMillis() + 5000
        while (!check() && System.currentTimeMillis() < until) Thread.sleep(20)
        assertThat(check()).isTrue()
    }

    @Test
    fun `no channel, no message`() {
        val (pid, _) = newProject("nf-none")
        waitingEvent(pid, "t_none")
        Thread.sleep(200)
        assertThat(posts).isEmpty()
        assertThat(get("/api/plugins/notify/channels").json()["channels"].size()).isEqualTo(0)
    }

    @Test
    fun `a waiting gate sends one short message, never the gate's text`() {
        val (pid, _) = newProject("nf-wait")
        chat()
        waitingEvent(pid, "t_wait")
        eventually { posts.size == 1 }
        val text = jacksonObjectMapper().readTree(posts[0])["text"].asText()
        assertThat(text).contains("waits for you").contains("nf-wait").contains("Export invoices").contains("spec approval")
        assertThat(posts[0]).doesNotContain("SECRET CODE")
        eventually { get("/api/plugins/notify/channels").json()["sent"].size() == 1 }
        val sent = get("/api/plugins/notify/channels").json()["sent"][0]
        assertThat(sent["ok"].asBoolean()).isTrue()
    }

    @Test
    fun `the same question sent twice by the engine goes out once`() {
        val (pid, _) = newProject("nf-twice")
        chat()
        waitingEvent(pid, "t_twice")
        eventually { get("/api/plugins/notify/channels").json()["sent"].size() == 1 }
        waitingEvent(pid, "t_twice")
        Thread.sleep(300)
        assertThat(posts).hasSize(1)
    }

    @Test
    fun `a channel that is off, for another project or only for failures stays quiet`() {
        val (pid, _) = newProject("nf-quiet")
        val (other, _) = newProject("nf-other")
        chat("on" to false)
        chat("project" to other)
        chat("events" to listOf("failed"))
        waitingEvent(pid, "t_quiet")
        Thread.sleep(300)
        assertThat(posts).isEmpty()
    }

    @Test
    fun `a project whose notifications are none sends nothing`() {
        val (pid, _) = newProject("nf-none-proj")
        chat()
        put("/api/projects/$pid/settings", mapOf("notify" to "none"))
        waitingEvent(pid, "t_none_proj")
        Thread.sleep(300)
        assertThat(posts).isEmpty()
    }

    @Test
    fun `a failed flow goes out only to a channel that asked for it`() {
        val (pid, _) = newProject("nf-fail")
        chat("events" to listOf("failed"))
        post("/internal/events", listOf(mapOf("type" to "thread.failed", "thread_id" to "t_fail", "project_id" to pid, "at" to java.time.Instant.now().toString(),
            "data" to mapOf("error" to "SECRET STACK TRACE"))), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)
        eventually { posts.size == 1 }
        assertThat(posts[0]).contains("failed").doesNotContain("SECRET STACK TRACE")
    }

    @Test
    fun `the webhook address never comes back, and removing a channel removes it`() {
        val c = chat()
        val id = c["id"].asText()
        val view = get("/api/plugins/notify/channels").andReturn().response.contentAsString
        assertThat(c.toString() + view).doesNotContain("SECRET-PART")
        assertThat(view).contains("127.0.0.1")
        assertThat(secrets.has("plugin.notify.$id.url")).isTrue()
        delete("/api/plugins/notify/channels/$id").andExpect(status().isOk)
        assertThat(secrets.has("plugin.notify.$id.url")).isFalse()
        assertThat(get("/api/plugins/notify/channels").json()["channels"].size()).isEqualTo(0)
        post("/api/plugins/notify/channels/$id/test").andExpect(status().isNotFound)
    }

    @Test
    fun `send a test says how it went, without the address`() {
        val id = chat()["id"].asText()
        assertThat(post("/api/plugins/notify/channels/$id/test").json()["ok"].asBoolean()).isTrue()
        assertThat(posts).hasSize(1)
        status = 403
        val res = post("/api/plugins/notify/channels/$id/test").json()
        assertThat(res["ok"].asBoolean()).isFalse()
        assertThat(res["error"].asText()).contains("403").doesNotContain("SECRET-PART")
    }

    @Test
    fun `a failing chat service is written down and takes nothing else with it`() {
        val (pid, _) = newProject("nf-broken")
        chat()
        status = 500
        waitingEvent(pid, "t_broken")
        eventually { get("/api/plugins/notify/channels").json()["sent"].size() == 1 }
        val sent = get("/api/plugins/notify/channels").json()["sent"][0]
        assertThat(sent["ok"].asBoolean()).isFalse()
        assertThat(sent["error"].asText()).contains("500")
        assertThat(get("/api/projects/$pid/runs").json()[0]["thread_id"].asText()).isEqualTo("t_broken")
    }

    @Test
    fun `a webhook must use https over the internet and may not point at a machine's own address`() {
        post("/api/plugins/notify/channels", mapOf("kind" to "chat", "url" to "http://8.8.8.8/hook")).andExpect(status().isBadRequest)
        post("/api/plugins/notify/channels", mapOf("kind" to "chat", "url" to "ftp://example.com/hook")).andExpect(status().isBadRequest)
        post("/api/plugins/notify/channels", mapOf("kind" to "chat", "url" to "http://169.254.169.254/latest")).andExpect(status().isBadRequest)
        post("/api/plugins/notify/channels", mapOf("kind" to "chat")).andExpect(status().isBadRequest)
        assertThat(get("/api/plugins/notify/channels").json()["channels"].size()).isEqualTo(0)
    }

    @Test
    fun `mail needs encryption on a network, and the From and To must be addresses`() {
        val mail = mapOf("kind" to "mail", "host" to "mail.example.com", "port" to 25, "security" to "none", "from" to "keel@example.com", "to" to listOf("me@example.com"))
        post("/api/plugins/notify/channels", mail).andExpect(status().isBadRequest)
        post("/api/plugins/notify/channels", mail + ("security" to "starttls") + ("from" to "not an address")).andExpect(status().isBadRequest)
        post("/api/plugins/notify/channels", mail + ("security" to "starttls") + ("to" to listOf("a@example.com\r\nBcc: x@example.com"))).andExpect(status().isBadRequest)
        post("/api/plugins/notify/channels", mail + ("security" to "starttls")).andExpect(status().isOk)
    }

    @Test
    fun `mail goes through a small SMTP talk, with the password and without a header trick`() {
        val server = ServerSocket(0)
        val talk = CopyOnWriteArrayList<String>()
        val done = thread(isDaemon = true) {
            server.accept().use { s ->
                val input = s.getInputStream().bufferedReader()
                val out = s.getOutputStream().writer()
                fun say(line: String) { out.write(line + "\r\n"); out.flush() }
                say("220 test")
                while (true) {
                    val line = input.readLine() ?: break
                    talk += line
                    when {
                        line.startsWith("EHLO") -> { say("250-test"); say("250 AUTH PLAIN") }
                        line.startsWith("AUTH") -> say("235 ok")
                        line == "DATA" -> {
                            say("354 go")
                            while (true) { val d = input.readLine() ?: break; talk += d; if (d == ".") break }
                            say("250 kept")
                        }
                        line == "QUIT" -> { say("221 bye"); break }
                        else -> say("250 ok")
                    }
                }
            }
        }
        val id = post("/api/plugins/notify/channels", mapOf("kind" to "mail", "host" to "127.0.0.1", "port" to server.localPort, "security" to "none",
            "user" to "keel", "password" to "pw-123", "from" to "keel@example.com", "to" to listOf("me@example.com"))).andExpect(status().isOk).json()["id"].asText()
        assertThat(post("/api/plugins/notify/channels/$id/test").json()["ok"].asBoolean()).isTrue()
        done.join(5000)
        server.close()
        assertThat(talk.toList()).anyMatch { it.startsWith("AUTH PLAIN") }
        assertThat(talk.toList()).contains("MAIL FROM:<keel@example.com>", "RCPT TO:<me@example.com>", "Subject: keel: test", "From: keel@example.com")
        assertThat(talk.joinToString("\n")).doesNotContain("pw-123")
    }

    @Test
    fun `a body line that starts with a dot is stuffed and a header stays one line`() {
        assertThat(MailSender.stuffed("one\n.two\n")).isEqualTo("one\r\n..two\r\n")
        assertThat(MailSender.header("a\r\nBcc: x@y.z")).isEqualTo("a Bcc: x@y.z")
    }
}
