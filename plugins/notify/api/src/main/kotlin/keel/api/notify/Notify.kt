package keel.api.notify

import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.annotation.PreDestroy
import keel.api.common.BadRequest
import keel.api.common.KeelProperties
import keel.api.common.KvStore
import keel.api.common.NotFound
import keel.api.common.Time
import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import keel.api.connections.SecretService
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.notifications.NotificationService
import keel.api.settings.SettingsService
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/** One place a message can go. It never holds a secret: the webhook address and the password are in keel's secret store. */
data class Channel(
    val id: String = "",
    /** chat | mail */
    val kind: String = "chat",
    val name: String = "",
    val on: Boolean = true,
    /** waiting (a flow waits for you) and failed (a flow failed) */
    val events: List<String> = listOf(WAITING),
    /** one project's id, or null for every project */
    val project: String? = null,
    /** chat: the webhook's host, for the list */
    val host: String = "",
    val port: Int = 587,
    val security: String = "starttls",
    val user: String = "",
    val from: String = "",
    val to: List<String> = emptyList(),
)

/** What the page sends to add or change a channel. A secret left out stays as it is. */
data class ChannelBody(
    val kind: String? = null, val name: String? = null, val on: Boolean? = null, val events: List<String>? = null,
    /** "" is every project */
    val project: String? = null, val url: String? = null, val host: String? = null, val port: Int? = null,
    val security: String? = null, val user: String? = null, val password: String? = null, val from: String? = null,
    val to: List<String>? = null,
)

/** One message keel tried to send. */
data class Sent(val at: String, val channel: String, val channelName: String, val text: String, val ok: Boolean, val error: String?,
                val key: String? = null)

data class NotifyView(val channels: List<Channel>, val sent: List<Sent>, val sends: String, val example: String)
data class TestResult(val ok: Boolean, val error: String? = null)

const val WAITING = "waiting"
const val FAILED = "failed"

/**
 * The Notify plugin's api part: a chat or mail message when a flow waits at a gate or fails, through the channels the
 * person added. Without a channel nothing is sent and no network is used.
 *
 *     GET    /api/plugins/notify/channels            the channels, the last messages, what is sent
 *     POST   /api/plugins/notify/channels            add one
 *     POST   /api/plugins/notify/channels/{id}       change one (on, events, project, name, its address or password)
 *     DELETE /api/plugins/notify/channels/{id}       remove one and its secrets
 *     POST   /api/plugins/notify/channels/{id}/test  send one test message now
 *
 * A message holds only the project's name, the flow's title and the gate's name: never a gate's text, code or a diff.
 */
@Service
class NotifyService(
    private val kv: KvStore,
    private val secrets: SecretService,
    private val settings: SettingsService,
    private val notifications: NotificationService,
    private val jdbc: JdbcTemplate,
    private val props: KeelProperties,
    mapper: ObjectMapper,
) {
    private val log = LoggerFactory.getLogger(NotifyService::class.java)
    private val chat = ChatSender(mapper)
    private val mail = MailSender()
    private val lock = Any()
    // one sender, a small queue: a slow chat service must never hold up keel's events, and a full queue drops a message
    // (it is written in the list) rather than growing without end
    private val pool = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(QUEUE)) { r -> Thread(r, "keel-notify").apply { isDaemon = true } }

    @PreDestroy
    fun stop() {
        pool.shutdownNow()
    }

    fun channels(): List<Channel> = kv.get<List<Channel>>(CHANNELS) ?: emptyList()
    fun sent(): List<Sent> = kv.get<List<Sent>>(SENT) ?: emptyList()

    fun view() = NotifyView(channels(), sent(), SENDS, text(WAITING, "shop", "Export invoices", "spec approval"))

    fun add(body: ChannelBody): Channel {
        val kind = body.kind ?: "chat"
        if (kind !in setOf("chat", "mail")) throw BadRequest("A channel is chat or mail")
        val id = "c" + UUID.randomUUID().toString().replace("-", "").take(10)
        val c = apply(Channel(id = id, kind = kind), body)
        when (kind) {
            "chat" -> if (body.url.isNullOrBlank()) throw BadRequest("Give the webhook address", "Your chat service makes one: search its settings for \"incoming webhook\".")
            else -> if (c.host.isBlank() || c.from.isBlank() || c.to.isEmpty()) throw BadRequest("Give the mail server, From and To")
        }
        store(c, body)
        synchronized(lock) { kv.put(CHANNELS, channels() + c) }
        return c
    }

    fun change(id: String, body: ChannelBody): Channel {
        val c = apply(find(id), body)
        store(c, body)
        synchronized(lock) { kv.put(CHANNELS, channels().map { if (it.id == id) c else it }) }
        return c
    }

    fun remove(id: String) {
        val c = find(id)
        listOf(secret(c, "url"), secret(c, "password")).filter { secrets.has(it) }.forEach { secrets.delete(it) }
        synchronized(lock) { kv.put(CHANNELS, channels().filter { it.id != id }) }
    }

    /** A person pressed Send a test: it goes now, and the answer says how it went. */
    fun test(id: String): TestResult {
        val c = find(id)
        val text = "keel: this is a test from Notify. It works."
        val error = deliver(c, "keel: test", text)
        remember(Sent(Time.now(), c.id, c.name, text, error == null, error))
        return TestResult(error == null, error)
    }

    private fun find(id: String) = channels().firstOrNull { it.id == id } ?: throw NotFound("No channel $id")

    private fun apply(c: Channel, b: ChannelBody): Channel {
        val events = b.events?.also { list ->
            if (list.isEmpty() || list.any { it !in setOf(WAITING, FAILED) }) throw BadRequest("Choose when it sends: waiting, failed or both")
        } ?: c.events
        val name = (b.name ?: c.name).trim().ifBlank { if (c.kind == "chat") "Chat" else "Mail" }.take(60)
        val host = when (c.kind) {
            "chat" -> b.url?.takeIf { it.isNotBlank() }?.let { WebhookAddress.check(it).host } ?: c.host
            else -> (b.host ?: c.host).trim()
        }
        val out = c.copy(name = name, on = b.on ?: c.on, events = events, project = b.project?.let { it.ifBlank { null } } ?: c.project.takeIf { b.project == null },
            host = host, port = b.port ?: c.port, security = b.security ?: c.security, user = (b.user ?: c.user).trim(),
            from = (b.from ?: c.from).trim(), to = (b.to ?: c.to).map { it.trim() }.filter { it.isNotEmpty() })
        if (c.kind == "mail" && out.host.isNotBlank()) {
            mail.check(MailServer(out.host, out.port, out.security, out.user, null))
            if (out.from.isNotBlank()) MailSender.address(out.from)
            out.to.forEach { MailSender.address(it) }
        }
        return out
    }

    private fun store(c: Channel, b: ChannelBody) {
        b.url?.takeIf { it.isNotBlank() }?.let { secrets.put(secret(c, "url"), it.trim()) }
        b.password?.takeIf { it.isNotEmpty() }?.let { secrets.put(secret(c, "password"), it) }
    }

    private fun secret(c: Channel, what: String) = "plugin.notify.${c.id}.$what"

    /** What the event handler calls: one message per channel that wants it. Never throws, never waits for the network. */
    fun event(event: String, projectId: String, threadId: String, step: String?, gate: String?) {
        if (projectId.isBlank()) return
        val wanted = channels().filter { it.on && event in it.events && (it.project == null || it.project == projectId) }
        if (wanted.isEmpty() || projectQuiet(projectId, event)) return
        val project = notifications.projectName(projectId) ?: projectId
        val flow = jdbc.query("SELECT title FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, threadId).firstOrNull().orEmpty()
        val body = text(event, project, flow, gate ?: step ?: "a gate")
        val subject = if (event == FAILED) "keel: a flow failed in $project" else "keel: a flow waits for you in $project"
        val key = "$threadId:$event:${step.orEmpty()}:${gate.orEmpty()}"
        for (c in wanted) {
            if (again(c, key) || overLimit(c)) continue
            try {
                pool.execute {
                    val error = runCatching { deliver(c, subject, body) }.getOrElse { it.javaClass.simpleName }
                    remember(Sent(Time.now(), c.id, c.name, body, error == null, error, key))
                }
            } catch (e: RejectedExecutionException) {
                remember(Sent(Time.now(), c.id, c.name, body, false, "Too many messages at once; this one was dropped.", key))
            }
        }
    }

    /** The project's own setting (Settings › Notifications): none sends nothing, needs_you only what waits for a person. */
    private fun projectQuiet(pid: String, event: String): Boolean =
        when (runCatching { settings.effective(pid).notify }.getOrDefault("all")) {
            "none" -> true
            "needs_you" -> event != WAITING
            else -> false
        }

    /** The engine sends an event again when its call was retried: the same question goes out once. */
    private fun again(c: Channel, key: String): Boolean {
        val since = Instant.now().minusSeconds(AGAIN_SECONDS)
        return sent().any { it.channel == c.id && it.key == key && runCatching { Instant.parse(it.at) }.getOrNull()?.isAfter(since) == true }
    }

    /** A flow gone wrong must not fill a person's chat: past this many in an hour, the rest wait in keel's Inbox. */
    private fun overLimit(c: Channel): Boolean {
        val since = Instant.now().minusSeconds(3600)
        return sent().count { it.channel == c.id && runCatching { Instant.parse(it.at) }.getOrNull()?.isAfter(since) == true } >= PER_HOUR
    }

    /** Sends one message; the failure's words (they hold no secret), or null when it went. */
    private fun deliver(c: Channel, subject: String, body: String): String? = try {
        when (c.kind) {
            "chat" -> chat.send(secrets.get(secret(c, "url")) ?: throw SendFailed("The webhook address is gone: add the channel again."), body)
            else -> mail.send(MailServer(c.host, c.port, c.security, c.user, secrets.get(secret(c, "password"))), c.from, c.to, subject, body)
        }
        null
    } catch (e: SendFailed) {
        e.message
    } catch (e: BadRequest) {
        e.message
    } catch (e: Exception) {
        log.warn("notify: could not send to a channel: {}", e.javaClass.simpleName)
        "It could not be sent (${e.javaClass.simpleName})."
    }

    private fun remember(s: Sent) = synchronized(lock) { kv.put(SENT, (listOf(s) + sent()).take(KEEP)) }

    /** The whole message: the project, the flow, the gate and where to answer. Nothing else leaves keel. */
    fun text(event: String, project: String, flow: String, gate: String): String {
        val where = props.publicUrl.trim().trimEnd('/').takeIf { it.isNotEmpty() }?.let { "$it/#/inbox" } ?: "keel's Inbox"
        val line = MailSender.header(if (flow.isBlank()) gate else "$flow: $gate")
        return if (event == FAILED) "keel: a flow failed in ${MailSender.header(project)}. $line. See it in keel."
        else "keel: a flow waits for you in ${MailSender.header(project)}. $line. Answer it in $where."
    }

    companion object {
        const val CHANNELS = "plugin.notify.channels"
        const val SENT = "plugin.notify.sent"
        const val KEEP = 50
        const val QUEUE = 50
        const val AGAIN_SECONDS = 120L
        const val PER_HOUR = 30
        const val SENDS = "Only the project's name, the flow's title, the gate's name and where to answer. Never a gate's text, code, a diff or a file. " +
            "The message goes to the chat or mail service you name here, under their rules."
    }
}

/** A flow began to wait for a person. A gate a run mode or the Supervisor answers by itself never comes here. */
@Component
class NotifyWaiting(private val notify: NotifyService) : EngineEventHandler {
    override val prefix = "gate.waiting"
    override fun handle(event: EngineEvent) {
        val name = event.data["step_name"]?.toString() ?: event.data["title"]?.toString()
        notify.event(WAITING, event.projectId, event.threadId, event.step, name)
    }
}

/** A flow failed. */
@Component
class NotifyFailed(private val notify: NotifyService) : EngineEventHandler {
    override val prefix = "thread.failed"
    override fun handle(event: EngineEvent) = notify.event(FAILED, event.projectId, event.threadId, event.step, null)
}

@Component
class NotifyChatKind : ConnectionKind {
    override val kind = "notify-chat"
    override val title = "Notify: chat"
    override val scope = "keel"
    override val order = 70
    override val fields = listOf(ConnectionField("name", "Name"), ConnectionField("url", "Webhook address", "secret", required = true))
}

@Component
class NotifyMailKind : ConnectionKind {
    override val kind = "notify-mail"
    override val title = "Notify: mail"
    override val scope = "keel"
    override val order = 71
    override val fields = listOf(
        ConnectionField("host", "Mail server", required = true), ConnectionField("port", "Port", "number", required = true),
        ConnectionField("security", "Security", "choice", required = true, choices = listOf("starttls", "tls", "none")),
        ConnectionField("user", "User"), ConnectionField("password", "Password", "secret"),
        ConnectionField("from", "From", required = true), ConnectionField("to", "To", "list", required = true),
    )
}

@RestController
@RequestMapping("/api/plugins/notify/channels")
class NotifyController(private val notify: NotifyService) {
    @GetMapping
    fun view(): NotifyView = notify.view()

    @PostMapping
    fun add(@RequestBody body: ChannelBody): Channel = notify.add(body)

    @PostMapping("/{id}")
    fun change(@PathVariable id: String, @RequestBody body: ChannelBody): Channel = notify.change(id, body)

    @DeleteMapping("/{id}")
    fun remove(@PathVariable id: String): Map<String, Boolean> {
        notify.remove(id)
        return mapOf("removed" to true)
    }

    @PostMapping("/{id}/test")
    fun test(@PathVariable id: String): TestResult = notify.test(id)
}
