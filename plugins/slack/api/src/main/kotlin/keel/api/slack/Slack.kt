package keel.api.slack

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.annotation.PreDestroy
import keel.api.common.ApiException
import keel.api.common.BadRequest
import keel.api.common.KvStore
import keel.api.common.Time
import keel.api.connections.ConnectionField
import keel.api.connections.ConnectionKind
import keel.api.connections.SecretService
import keel.api.engine.EngineClient
import keel.api.events.AgentCalls
import keel.api.events.EngineEvent
import keel.api.events.EngineEventHandler
import keel.api.events.EventService
import keel.api.flow.DEFAULT_BRANCH
import keel.api.flow.FlowService
import keel.api.inbox.InboxAct
import keel.api.inbox.InboxService
import keel.api.projects.ProjectService
import keel.api.settings.LocalOnly
import keel.api.settings.SettingsService
import keel.api.tasks.NewTask
import keel.api.tasks.TaskPatch
import keel.api.tasks.TaskService
import org.slf4j.LoggerFactory
import org.springframework.boot.context.event.ApplicationReadyEvent
import org.springframework.context.event.EventListener
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.web.bind.annotation.DeleteMapping
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

private data class TaskRef(val id: String, val title: String, val source: String?, val key: String?, val externalStatus: String?, val project: String)

/** One person who may act from Slack: their Slack member id, and the name keel records. */
data class SlackPerson(val id: String = "", val name: String = "")

/** What keel keeps about the link. No token: those are in the secret store. */
data class SlackConfig(
    val on: Boolean = true,
    val apiUrl: String = SLACK_API,
    /** the channel a gate's question goes to (its id, C…) */
    val channel: String = "",
    /** who may answer and make tasks; nobody else */
    val allowed: List<SlackPerson> = emptyList(),
    /** also send a gate's whole text (it can hold code). Off: its short lines only. */
    val sendDetail: Boolean = false,
    /** where `task ...` goes without `in <project>:` */
    val project: String? = null,
    /** the workspace the tokens belong to, from Slack itself: an event of another one is refused */
    val team: String? = null,
    val teamName: String? = null,
    val botUser: String? = null,
    /** Let people ask keel in Slack: a message that is no command goes to the model, with read-only tools. Off until the person turns it on. */
    val allowChat: Boolean = false,
    /** a person's cap per day for that chat; each turn is checked before it runs */
    val chatDayUsd: Double = 1.0,
    val chatDayTokens: Long = 200_000,
    /** Let people start flows from Slack, with one confirm button. Off until the person turns it on. */
    val allowStart: Boolean = false,
    /** Allow every gate from Slack: `person: true` gates and the never list, for people on [strictAllowed]. Budget and secret gates stay in keel whatever this says. */
    val allowAllGates: Boolean = false,
    val strictAllowed: List<SlackPerson> = emptyList(),
)

data class SlackBody(
    val botToken: String? = null, val appToken: String? = null, val on: Boolean? = null, val apiUrl: String? = null, val channel: String? = null,
    val allowed: List<SlackPerson>? = null, val sendDetail: Boolean? = null, val project: String? = null,
    val allowChat: Boolean? = null, val chatDayUsd: Double? = null, val chatDayTokens: Long? = null, val allowStart: Boolean? = null,
    val allowAllGates: Boolean? = null, val strictAllowed: List<SlackPerson>? = null,
)

/** One thing that happened from Slack, for the settings page and the person's check. */
data class SlackLogRow(val at: String, val who: String, val what: String, val ok: Boolean)

data class SlackView(
    val linked: Boolean, val state: String, val problem: String?, val config: SlackConfig, val botHint: String?, val appHint: String?, val log: List<SlackLogRow>,
    val sendsSlack: String, val sendsModel: String,
)

/** The message keel posted for a question, so an answer can close it. */
data class PostedGate(val thread: String = "", val step: String = "", val channel: String = "", val ts: String = "", val text: String = "")

/** A chat in one Slack thread: what was said, kept short, so a reply continues it. */
data class SlackChatTurn(val role: String = "", val text: String = "")
data class SlackChat(val key: String = "", val project: String = "", val turns: List<SlackChatTurn> = emptyList(), val at: String = "")

/** One person's use of the chat today. */
data class SlackSpend(val day: String = "", val people: Map<String, SlackSpent> = emptyMap())
data class SlackSpent(val usd: Double = 0.0, val tokens: Long = 0)

/** `start ...` waiting for its one confirm. Kept here, so a button can carry nothing but this id, and only who asked can press it. */
private data class PendingStart(val user: String, val who: String, val project: String, val projectName: String, val workflow: String, val what: String, val until: Long)

/**
 * The Slack plugin's api part. keel connects out (Socket Mode), so a keel on a laptop needs no public address.
 *
 *     a gate waits ──▶ a message with its short lines and buttons, in the person's channel
 *     the Supervisor asks ──▶ its words under that message
 *     a button, or `answer <ref> <choice> <reason>` ──▶ InboxService.act: the same check as an answer in keel's Inbox
 *     `status`, `waiting` ──▶ a real answer from keel's own tables, with no model
 *     `task <title> [@name] | <text>` ──▶ TaskService.create, and a direct message to @name
 *     `assign <task> @name` ──▶ TaskService.update: a name on a local task, a label and no account
 *     a gate with a form ──▶ a modal with the Inbox's options ([SlackForms])
 *     `start <flow> ...` (off until turned on) ──▶ one confirm button ──▶ FlowService.start, the dashboard's own start
 *     anything else (off until turned on) ──▶ the model, with read-only tools, in the engine part (keel_plugin_slack)
 *
 * Who may act: only a Slack member the person listed, in the workspace of the tokens. A bot's message is never read. A gate only a person answers
 * (`person: true`) and everything on the Supervisor's never list ([NEVER]) stays in keel: Slack says so and sends no buttons. With **Allow every gate
 * from Slack** on, people on a second, shorter list may answer those, with a reason and one confirm; budget and secret gates ([ALWAYS]) stay in keel whatever
 * the settings say. Slack's text never goes to an agent as an instruction: commands are matched here, with no model, and the chat's agent cannot write.
 */
@Service
class SlackService(
    private val kv: KvStore,
    private val secrets: SecretService,
    private val mapper: ObjectMapper,
    private val jdbc: JdbcTemplate,
    private val engine: EngineClient,
    private val inbox: InboxService,
    private val flows: FlowService,
    private val projects: ProjectService,
    private val tasks: TaskService,
    private val events: EventService,
    private val settings: SettingsService,
) {
    private val log = LoggerFactory.getLogger(SlackService::class.java)
    private val lock = Any()
    // one worker: events are handled in order, and a slow Slack never holds up keel's events or the socket's acknowledgements
    private val pool = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(QUEUE)) { r -> Thread(r, "keel-slack").apply { isDaemon = true } }
    private val seen = object : LinkedHashMap<String, Boolean>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Boolean>) = size > SEEN_MAX
    }
    // the chat runs here: a model's turn takes long, and the one worker above keeps Slack and keel's events moving
    private val chatPool = ThreadPoolExecutor(CHAT_WORKERS, CHAT_WORKERS, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(CHAT_QUEUE)) { r -> Thread(r, "keel-slack-chat").apply { isDaemon = true } }
    private val chatting = ConcurrentHashMap.newKeySet<String>()
    private val starts = ConcurrentHashMap<String, PendingStart>()
    @Volatile private var socket: SlackSocket? = null
    @Volatile private var state = "off"
    @Volatile private var problem: String? = null

    fun config(): SlackConfig = kv.get<SlackConfig>(CONFIG) ?: SlackConfig()
    private fun bot() = secrets.get(BOT)
    private fun app() = secrets.get(APP)
    private fun api(c: SlackConfig = config()) = SlackApi(c.apiUrl, mapper)

    fun view() = SlackView(secrets.has(BOT) && secrets.has(APP), state, problem, config(), secrets.hintOf(BOT), secrets.hintOf(APP),
        kv.get<List<SlackLogRow>>(LOG) ?: emptyList(), SENDS_SLACK, SENDS_MODEL)

    /** Saves what the page sent. New tokens are checked with Slack first; a refused one is not kept. */
    fun save(b: SlackBody): SlackView {
        val before = config()
        var c = before
        c = c.copy(on = b.on ?: c.on, apiUrl = b.apiUrl?.trim()?.ifEmpty { null } ?: c.apiUrl, channel = b.channel?.trim() ?: c.channel, sendDetail = b.sendDetail ?: c.sendDetail,
            project = if (b.project != null) b.project.trim().ifEmpty { null } else c.project, allowed = b.allowed?.let(::checkedPeople) ?: c.allowed,
            allowChat = b.allowChat ?: c.allowChat, chatDayUsd = b.chatDayUsd ?: c.chatDayUsd, chatDayTokens = b.chatDayTokens ?: c.chatDayTokens,
            allowStart = b.allowStart ?: c.allowStart, allowAllGates = b.allowAllGates ?: c.allowAllGates,
            strictAllowed = b.strictAllowed?.let(::checkedPeople) ?: c.strictAllowed)
        if (c.chatDayUsd <= 0 || c.chatDayTokens <= 0) throw BadRequest("The chat's daily caps must be above zero", "To stop the chat, turn it off.")
        // the second list is shorter than the first: nobody answers a kept gate who may not answer a plain one
        val outside = c.strictAllowed.filter { s -> c.allowed.none { it.id == s.id } }
        if (b.strictAllowed != null && outside.isNotEmpty()) throw BadRequest("${outside.joinToString { it.name }} is not on the list of who may answer", "The second list is part of the first.")
        c = c.copy(strictAllowed = c.strictAllowed.filter { s -> c.allowed.any { it.id == s.id } })
        checkedAddress(c.apiUrl, "https", "http")
        val bot = b.botToken?.trim()?.ifEmpty { null }
        val app = b.appToken?.trim()?.ifEmpty { null }
        if (bot != null && !bot.startsWith("xoxb-")) throw BadRequest("A bot token starts with xoxb-", "Slack › your app › OAuth & Permissions.")
        if (app != null && !app.startsWith("xapp-")) throw BadRequest("An app-level token starts with xapp-", "Slack › your app › Basic Information › App-Level Tokens.")
        try {
            if (bot != null) {
                val who = api(c).call("auth.test", bot)
                c = c.copy(team = who.path("team_id").asText(null), teamName = who.path("team").asText(null), botUser = who.path("user_id").asText(null))
            }
            if (app != null) api(c).call("apps.connections.open", app)
        } catch (e: SlackError) {
            throw BadRequest(e.message ?: "Slack refused it.", "Nothing was kept.")
        }
        bot?.let { secrets.put(BOT, it) }
        app?.let { secrets.put(APP, it) }
        kv.put(CONFIG, c)
        // a choice on the page does not drop the connection; only what the connection itself depends on does
        if (bot != null || app != null || c.on != before.on || c.apiUrl != before.apiUrl || socket == null) restart()
        return view()
    }

    private fun checkedPeople(list: List<SlackPerson>): List<SlackPerson> = list.map { p ->
        val id = p.id.trim()
        if (!Regex("^[UW][A-Z0-9]{2,}$").matches(id)) throw BadRequest("\"${id.take(30)}\" is not a Slack member id", "It looks like U012ABCDEF: a person's Slack profile › More › Copy member ID.")
        SlackPerson(id, p.name.trim().replace(Regex("[\\r\\n]"), " ").take(60).ifEmpty { id })
    }.distinctBy { it.id }

    fun forget(): SlackView {
        socket?.stop()
        socket = null
        listOf(BOT, APP).filter { secrets.has(it) }.forEach { secrets.delete(it) }
        kv.delete(CONFIG)
        kv.delete(CHATS)
        kv.delete(SPEND)
        starts.clear()
        state = "off"
        problem = null
        return view()
    }

    /** (Re)connects when it is on and has both tokens. */
    @Synchronized
    fun restart() {
        socket?.stop()
        socket = null
        val c = config()
        if (!c.on || bot() == null || app() == null) {
            state = "off"
            return
        }
        state = "connecting"
        problem = null
        socket = SlackSocket(
            open = { api().call("apps.connections.open", app() ?: throw SlackError("The app-level token is gone.")).path("url").asText() },
            mapper = mapper,
            onEnvelope = { env -> later { envelope(env) } },
            onState = { s, why -> state = s; problem = why },
        ).also { it.start() }
    }

    @PreDestroy
    fun stop() {
        socket?.stop()
        pool.shutdownNow()
        chatPool.shutdownNow()
    }

    private fun later(job: () -> Unit) {
        try {
            pool.execute { runCatching(job).onFailure { log.warn("slack: {}", it.javaClass.simpleName) } }
        } catch (e: RejectedExecutionException) {
            log.warn("slack: too much at once, one event was dropped")
        }
    }

    private fun once(id: String): Boolean = synchronized(seen) { seen.put(id, true) == null }

    // ---- from Slack

    /** One envelope. Slack sends again what it thinks it lost: each is handled once. */
    fun envelope(env: JsonNode) {
        val payload = env.path("payload")
        when (env.path("type").asText()) {
            "events_api" -> if (once(payload.path("event_id").asText(env.path("envelope_id").asText()))) event(payload)
            "interactive" -> if (once(env.path("envelope_id").asText())) when (payload.path("type").asText()) {
                "block_actions" -> button(payload)
                "view_submission" -> submitted(payload)
            }
        }
    }

    private fun event(payload: JsonNode) {
        val e = payload.path("event")
        val c = config()
        if (e.path("type").asText() !in setOf("message", "app_mention")) return
        val inChannel = e.path("type").asText() == "message" && e.path("channel_type").asText() != "im"
        // in a channel: only when mentioned, or a reply in a thread where keel is chatting. Slack sends other channel messages too, once the app may read them
        if (inChannel && !continuesChat(c, e)) return
        // a bot, an edit, a join line: never read, and never answered, or two bots talk for ever
        if (e.has("bot_id") || e.has("subtype") || e.path("user").asText().isEmpty() || e.path("user").asText() == c.botUser) {
            remember(e.path("user").asText(e.path("bot_id").asText("a bot")), "was not read: it came from a bot", false)
            return
        }
        val user = e.path("user").asText()
        val channel = e.path("channel").asText()
        val thread = e.path("thread_ts").asText(null)
        val person = who(c, user, payload.path("team_id").asText(null))
        if (person == null) {
            remember(user, "was refused: not on the list", false)
            say(channel, thread, "Sorry, you are not on this keel's list of people who may use it from Slack. The person who runs keel can add you.")
            return
        }
        // only the app's own mention goes: `task Fix it @sara` needs hers
        val text = withoutBot(c, e.path("text").asText()).trim()
        val reply = thread ?: e.path("ts").asText(null)
        val word = text.substringBefore(' ').lowercase()
        if (word == "start") return startAsked(c, person, text.substringAfter(' ', "").trim(), channel, reply)
        if (c.allowChat && word !in COMMANDS && text.isNotBlank()) return chat(c, person, text, channel, reply)
        say(channel, reply, command(c, person, text))
    }

    private fun withoutBot(c: SlackConfig, text: String) = c.botUser?.let { text.replace(Regex("<@$it(\\|[^>]*)?>"), "") } ?: text.replaceFirst(Regex("^\\s*<@[A-Z0-9]+(\\|[^>]*)?>"), "")

    /** A plain reply in a channel's thread goes on only where keel is already chatting. A mention comes as its own event. */
    private fun continuesChat(c: SlackConfig, e: JsonNode): Boolean {
        val thread = e.path("thread_ts").asText(null) ?: return false
        val mentioned = c.botUser?.let { e.path("text").asText().contains("<@$it") } ?: false
        return c.allowChat && !mentioned && chats().any { it.key == chatKey(e.path("channel").asText(), thread) }
    }

    /** The person who may act, or null: on the list and in the workspace of the tokens. */
    private fun who(c: SlackConfig, user: String, team: String?): SlackPerson? {
        if (c.team != null && team != null && team != c.team) return null
        return c.allowed.firstOrNull { it.id == user }
    }

    private fun button(payload: JsonNode) {
        val c = config()
        val user = payload.path("user").path("id").asText()
        val team = payload.path("team").path("id").asText(payload.path("user").path("team_id").asText(null))
        val channel = payload.path("channel").path("id").asText()
        val ts = payload.path("message").path("ts").asText(null)
        val person = who(c, user, team)
        if (person == null) {
            remember(user, "was refused: not on the list", false)
            whisper(channel, user, "Sorry, you are not on this keel's list of people who may answer from Slack.")
            return
        }
        val action = payload.path("actions").path(0)
        val id = action.path("action_id").asText()
        if (id == START) return confirmStart(person, action.path("value").asText(), channel, ts, user)
        if (id == START_CANCEL) return cancelStart(person, action.path("value").asText(), channel, ts, user)
        if (id !in setOf(ACTION, FORM_ACTION, KEPT_ACTION)) return
        val v = runCatching { mapper.readTree(action.path("value").asText()) }.getOrNull() ?: return
        val tid = v.path("t").asText()
        val qid = v.path("q").asText(null)
        when (id) {
            ACTION -> say(channel, ts, answer(c, person, tid, qid, v.path("c").asText(null), null))
            FORM_ACTION -> openForm(c, person, tid, qid, payload.path("trigger_id").asText(), channel, ts)
            KEPT_ACTION -> openKept(c, person, tid, qid, v.path("c").asText(null), payload.path("trigger_id").asText(), channel, ts, user)
        }
    }

    /** The form or the kept gate's confirm came back. Who sent it is checked again, and the question must still be the one it was opened for. */
    private fun submitted(payload: JsonNode) {
        val c = config()
        val view = payload.path("view")
        val user = payload.path("user").path("id").asText()
        val team = payload.path("team").path("id").asText(payload.path("user").path("team_id").asText(null))
        val m = runCatching { mapper.readTree(view.path("private_metadata").asText()) }.getOrNull() ?: return
        val channel = m.path("ch").asText()
        val ts = m.path("ts").asText(null)
        val person = who(c, user, team)
        if (person == null) {
            remember(user, "was refused: not on the list", false)
            return
        }
        val tid = m.path("t").asText()
        val qid = m.path("q").asText(null)
        val out = when (view.path("callback_id").asText()) {
            SlackForms.FORM -> answerForm(c, person, tid, qid, view)
            SlackForms.KEPT -> answerKept(c, person, tid, qid, m.path("c").asText(null), SlackForms.readWhy(view))
            else -> return
        }
        say(channel, ts, out)
    }

    private fun meta(vararg pairs: Pair<String, String?>) = mapper.writeValueAsString(mapOf(*pairs))

    private fun openView(c: SlackConfig, trigger: String, view: Map<String, Any?>, channel: String, ts: String?) {
        val token = bot() ?: return
        runCatching { api(c).call("views.open", token, mapOf("trigger_id" to trigger, "view" to view)) }.onFailure {
            fail(it)
            say(channel, ts, "Slack could not open the form. Answer it in keel.")
        }
    }

    private fun waitingNow(tid: String) = runCatching { engine.thread(tid) }.getOrNull()?.path("waiting")?.takeUnless { it.isMissingNode || it.isNull }

    private fun openForm(c: SlackConfig, person: SlackPerson, tid: String, qid: String?, trigger: String, channel: String, ts: String?) {
        val w = waitingNow(tid)
        val why = when {
            w == null -> "This flow does not wait any more. Nothing changed."
            qid != null && w.path("id").asText(null) != qid -> "This flow now asks something else. `waiting` gives the new code."
            alwaysInKeel(w) != null -> alwaysInKeel(w)
            keptInKeel(w) != null -> keptInKeel(w)
            !SlackForms.fits(w) -> FORM_IN_KEEL
            else -> null
        }
        if (why != null) {
            remember("${person.name} (${person.id})", "could not open a form", false, tid)
            return say(channel, ts, why)
        }
        openView(c, trigger, SlackForms.formView(w!!, meta("t" to tid, "q" to qid, "ch" to channel, "ts" to ts)), channel, ts)
    }

    private fun openKept(c: SlackConfig, person: SlackPerson, tid: String, qid: String?, word: String?, trigger: String, channel: String, ts: String?, user: String) {
        val w = waitingNow(tid)
        if (w == null || (qid != null && w.path("id").asText(null) != qid)) return say(channel, ts, "This flow now asks something else. Nothing changed.")
        val refused = keptRefusal(c, person, w)
        if (refused != null) {
            remember("${person.name} (${person.id})", "was refused at a gate only keel takes", false, tid)
            return whisper(channel, user, refused)
        }
        openView(c, trigger, SlackForms.keptView(w.path("title").asText(), word, meta("t" to tid, "q" to qid, "c" to word, "ch" to channel, "ts" to ts)), channel, ts)
    }

    /** A person's words, matched here. No model ever reads them to decide what to do. */
    fun command(c: SlackConfig, person: SlackPerson, text: String): String {
        val word = text.substringBefore(' ').lowercase()
        val rest = text.substringAfter(' ', "").trim()
        return when (word) {
            "status" -> status(rest)
            "waiting" -> waiting()
            "task" -> task(c, person, rest)
            "assign" -> assign(c, person, rest)
            "answer" -> {
                val parts = rest.split(Regex("\\s+"), limit = 3)
                if (parts.size < 2) ANSWER_HOW else answerRef(c, person, parts[0], parts[1].lowercase(), parts.getOrNull(2))
            }
            else -> help(c)
        }
    }

    /** A code holds the flow and its question, so a typed answer can never reach a newer question than the one it was for. */
    private fun answerRef(c: SlackConfig, person: SlackPerson, code: String, word: String, why: String?): String {
        val m = Regex("^([A-Za-z0-9_-]{$REF})-([A-Za-z0-9]{1,$QREF})$").matchEntire(code) ?: return "That is not a code. `waiting` lists the codes."
        val tid = jdbc.query("SELECT id FROM threads WHERE substr(id, 1, $REF) = ? AND status = 'waiting' LIMIT 2", { rs, _ -> rs.getString(1) }, m.groupValues[1])
        if (tid.isEmpty()) return "No flow waits with the code $code."
        if (tid.size > 1) return "More than one flow has the code $code. Answer it in keel."
        val qid = runCatching { engine.thread(tid[0]) }.getOrNull()?.path("waiting")?.path("id")?.asText(null)
        if (qid == null || !qid.startsWith(m.groupValues[2])) return "This flow now asks something else. `waiting` gives the new code."
        return answer(c, person, tid[0], qid, word, why)
    }

    private fun code(tid: String, qid: String?) = tid.take(REF) + "-" + qid.orEmpty().take(QREF)

    /**
     * An answer from Slack: keel's rules first, then the same call as keel's Inbox ([InboxService.act]). mayDecide false: as for an MCP client with the
     * write level, a gate with `person: true` is refused there too. The engine checks the answer as for any other (a reason where one is needed).
     * A gate keel keeps is answered only through [answerKept]; a gate with a form only through [answerForm], or sent back here with a reason.
     */
    fun answer(c: SlackConfig, person: SlackPerson, tid: String, question: String?, word: String?, why: String?): String {
        val who = "${person.name} (${person.id})"
        val state = runCatching { engine.thread(tid) }.getOrNull()
        val w = state?.path("waiting")
        if (w == null || w.isMissingNode || w.isNull) {
            remember(who, "answered too late", false, tid)
            return "This flow does not wait any more. Nothing changed."
        }
        val only = alwaysInKeel(w) ?: keptInKeel(w)?.let { kept -> if (mayAnswerKept(c, person)) "$kept Or use the button on its message: it asks for your reason." else kept }
        if (only != null) {
            remember(who, "was refused at a gate only keel takes", false, tid)
            return only
        }
        val back = word == "reject" || word == "back"
        if (SlackForms.hasForm(w) && !back) {
            remember(who, "tried to answer a form in words", false, tid)
            return if (SlackForms.fits(w)) "This one has a form. Press **Answer the form** on its message." else FORM_IN_KEEL
        }
        val (decision, payload) = wordToAnswer(w, word) ?: return "This gate takes: ${(names(w).ifEmpty { listOf("approve") } + "reject").joinToString(", ")}."
        return act(person, tid, InboxAct(decision, why?.trim()?.ifEmpty { null }?.take(WHY_MAX), payload, question), mayDecide = false, "answered ${word ?: "approve"}")
    }

    private fun names(w: JsonNode) = w.path("choice_info").map { it.path("name").asText() }.ifEmpty { w.path("choices").map { it.asText() } }

    private fun wordToAnswer(w: JsonNode, word: String?): Pair<String, Map<String, Any?>?>? {
        val names = names(w)
        return when {
            word == "reject" || word == "back" -> "reject" to null
            word != null && word in names -> "approve" to mapOf("choice" to word)
            names.isEmpty() && (word == null || word == "approve") -> "approve" to null
            else -> null
        }
    }

    /** The one way an answer from Slack reaches a flow: the same call as keel's Inbox, with who it was. */
    private fun act(person: SlackPerson, tid: String, a: InboxAct, mayDecide: Boolean, said: String): String {
        val who = "${person.name} (${person.id})"
        return try {
            inbox.act(tid, a, mayDecide = mayDecide, by = "slack:$who")
            remember(who, said, true, tid)
            "Thanks ${person.name}: $said."
        } catch (e: ApiException) {
            remember(who, "was refused: ${e.message}", false, tid)
            "keel did not take it: ${e.message}" + (e.hint?.let { " $it" } ?: "")
        }
    }

    /** A form from Slack: its answers as the Inbox sends them, through the Inbox's check. */
    private fun answerForm(c: SlackConfig, person: SlackPerson, tid: String, qid: String?, view: JsonNode): String {
        val w = waitingNow(tid) ?: return "This flow does not wait any more. Nothing changed."
        if (qid != null && w.path("id").asText(null) != qid) return "This flow now asks something else. Nothing changed."
        val who = "${person.name} (${person.id})"
        val only = alwaysInKeel(w) ?: keptInKeel(w) ?: if (SlackForms.fits(w)) null else FORM_IN_KEEL
        if (only != null) {
            remember(who, "was refused at a gate only keel takes", false, tid)
            return only
        }
        val form = SlackForms.readForm(w, view)
        form.missing?.let {
            remember(who, "sent a form without a reason it needs", false, tid)
            return "$it Nothing changed: press **Answer the form** again."
        }
        val payload = mutableMapOf<String, Any?>("answers" to form.answers)
        if (form.reasons.isNotEmpty()) payload["reasons"] = form.reasons
        form.choice?.let { payload["choice"] = it }
        return act(person, tid, InboxAct("approve", form.why?.take(WHY_MAX), payload, qid), mayDecide = false, "answered the form")
    }

    private fun mayAnswerKept(c: SlackConfig, person: SlackPerson) = c.allowAllGates && c.strictAllowed.any { it.id == person.id }

    /** Why this person may not answer this gate from Slack through its confirm, or null. Budget and secret gates: never. */
    private fun keptRefusal(c: SlackConfig, person: SlackPerson, w: JsonNode): String? = alwaysInKeel(w)
        ?: if (keptInKeel(w) == null) "This gate needs no confirm." else if (SlackForms.hasForm(w)) keptInKeel(w)
        else if (!c.allowAllGates) keptInKeel(w)
        else if (!mayAnswerKept(c, person)) "${person.name}, you are not on the short list of who may answer these from Slack. Answer it in keel."
        else null

    /** **Allow every gate from Slack**: a gate keel keeps, for a person on the second list, with a reason. It counts as the person's own answer: mayDecide. */
    private fun answerKept(c: SlackConfig, person: SlackPerson, tid: String, qid: String?, word: String?, why: String?): String {
        val w = waitingNow(tid) ?: return "This flow does not wait any more. Nothing changed."
        if (qid != null && w.path("id").asText(null) != qid) return "This flow now asks something else. Nothing changed."
        val who = "${person.name} (${person.id})"
        keptRefusal(c, person, w)?.let {
            remember(who, "was refused at a gate only keel takes", false, tid)
            return it
        }
        if (why.isNullOrBlank()) return "It needs a reason. Nothing changed."
        val (decision, payload) = wordToAnswer(w, word) ?: return "This gate takes: ${(names(w).ifEmpty { listOf("approve") } + "reject").joinToString(", ")}."
        return act(person, tid, InboxAct(decision, why.take(WHY_MAX), payload, qid), mayDecide = true, "answered ${word ?: "approve"} at a gate keel normally keeps")
    }

    /** Why this question stays in keel whatever the settings say, or null: money and secrets. */
    private fun alwaysInKeel(w: JsonNode): String? = when {
        w.path("gate_kind").asText("") in ALWAYS -> "keel's rules keep this kind of question in keel (${w.path("gate_kind").asText()}). Answer it there."
        w.path("kind").asText() in setOf("budget", "usage") -> "keel's rules keep money questions in keel. Answer it there."
        CAP_TITLE.containsMatchIn(w.path("title").asText("")) -> "keel's rules keep money questions in keel. Answer it there."
        else -> null
    }

    /** Why this question stays in keel unless **Allow every gate from Slack** says otherwise, or null. */
    private fun keptInKeel(w: JsonNode): String? = when {
        w.path("person").asBoolean(false) -> "Only a person signed in to keel answers this one. Answer it in keel."
        w.path("gate_kind").asText("") in NEVER -> "keel's rules keep this kind of question in keel (${w.path("gate_kind").asText()}). Answer it there."
        else -> null
    }

    private fun status(words: String): String {
        val pids = projects.rows()
        if (words.isBlank()) {
            val lines = pids.map { p ->
                val runs = flows.runs(p.id, null, 30)
                val on = runs.filter { it.status == "running" || it.status == "waiting" }
                "*${safe(p.name)}*: " + if (on.isEmpty()) "nothing runs" else on.joinToString("; ") { "${safe(it.title)} (${it.status}${it.waiting?.let { w -> ": ${safe(w)}" } ?: ""})" }
            }
            return lines.joinToString("\n").ifEmpty { "keel has no project." }
        }
        val find = words.lowercase()
        val out = mutableListOf<String>()
        for (p in pids) {
            flows.runs(p.id, null, 50).filter { find in it.title.lowercase() || it.threadId.startsWith(find) }.take(3).forEach { r ->
                out += "Flow *${safe(r.title)}* in ${safe(p.name)}: ${r.status}" + (r.current?.let { " at ${safe(it)}" } ?: "") + (r.waiting?.let { ", waits for: ${safe(it)}" } ?: "") +
                    (if (r.acsTotal > 0) ", ${r.acsDone} of ${r.acsTotal} criteria" else "") + ", ${r.tokens} tokens" + (r.error?.let { ". Error: ${safe(it.take(200))}" } ?: "")
            }
            jdbc.query("SELECT id, title, status, external_key FROM tasks WHERE project_id = ? AND (LOWER(title) LIKE ? OR LOWER(COALESCE(external_key, '')) = ?) ORDER BY updated_at DESC LIMIT 3",
                { rs, _ -> "Task *${safe(rs.getString(2))}* in ${safe(p.name)}: ${rs.getString(3)}" + (rs.getString(4)?.let { " (${safe(it)})" } ?: "") }, p.id, "%${find.replace("%", "")}%", find).forEach { out += it }
        }
        return out.take(STATUS_MAX).joinToString("\n").ifEmpty { "keel has no flow or task with \"${safe(words)}\"." }
    }

    private fun waiting(): String {
        val rows = inbox.list().items.filter { it.threadId.isNotEmpty() }
        val more = if (rows.size > STATUS_MAX) "\n…and ${rows.size - STATUS_MAX} more in keel's Inbox." else ""
        return rows.take(STATUS_MAX).joinToString("\n", postfix = more) { "${safe(it.projectName)}: *${safe(it.flow)}*: ${safe(it.title)}, code `${code(it.threadId, it.id)}`" }.ifEmpty { "Nothing waits for you." }
    }

    /** Who `@sara` or a real Slack mention is: a person on the list, or the words that say why not. */
    private fun named(c: SlackConfig, ref: String): Pair<SlackPerson?, String?> {
        val id = Regex("^<@([A-Z0-9]+)(?:\\|[^>]*)?>$").find(ref)?.groupValues?.get(1)
        if (id != null) return c.allowed.firstOrNull { it.id == id }?.let { it to null } ?: (null to "That person is not on this keel's list. Nothing changed.")
        val name = ref.removePrefix("@").lowercase()
        val found = c.allowed.filter { it.name.lowercase() == name || it.name.lowercase().substringBefore(' ') == name }
        return when (found.size) {
            1 -> found[0] to null
            0 -> null to "${safe(ref)} is not on this keel's list. Nothing changed."
            else -> null to "More than one person on the list is called ${safe(ref)}. Use their full name or a Slack mention. Nothing changed."
        }
    }

    /** A direct message: Slack takes the member id as the channel. Your own work needs no message; one that does not arrive changes nothing. */
    private fun tell(c: SlackConfig, from: SlackPerson, to: SlackPerson, text: String): Boolean {
        if (to.id == from.id) return true
        val token = bot() ?: return false
        return runCatching { api(c).call("chat.postMessage", token, mapOf("channel" to to.id, "text" to text, "parse" to "none", "link_names" to false)) }.onFailure { fail(it) }.isSuccess
    }

    /**
     * `task <title>`, `task <title> @name`, `task <title> | <text>`, `task in <project>: <title>`. It goes on the Tasks board; no flow starts from this.
     * The name is on the list or the task is not made: a typing mistake must not make a task nobody hears about.
     */
    private fun task(c: SlackConfig, person: SlackPerson, rest: String): String {
        val who = "${person.name} (${person.id})"
        val inProject = Regex("^in\\s+([^:]+):\\s*(.*)$", RegexOption.DOT_MATCHES_ALL).find(rest)
        val words = inProject?.groupValues?.get(2) ?: rest
        var title = words.substringBefore('|').trim()
        var assignee: SlackPerson? = null
        ASSIGNEE.find(title)?.let { m ->
            val (to, problem) = named(c, m.groupValues[1])
            if (to == null) return problem!!
            assignee = to
            title = title.substring(0, m.range.first).trim()
        }
        if (title.isEmpty()) return TASK_HOW
        val all = projects.rows()
        val named = inProject?.groupValues?.get(1)?.trim()
        val project = when {
            named != null -> all.firstOrNull { it.id.equals(named, true) || it.name.equals(named, true) } ?: return "keel has no project $named. It has: ${all.joinToString(", ") { safe(it.name) }}."
            c.project != null -> all.firstOrNull { it.id == c.project }
            all.size == 1 -> all[0]
            else -> null
        } ?: return "Which project? Say `task in <project>: <title>`. keel has: ${all.joinToString(", ") { safe(it.name) }}."
        val text = words.substringAfter('|', "").trim()
        return try {
            val t = tasks.create(project.id, NewTask(title = title.take(TITLE_MAX), description = "From Slack, written by $who.\n\n$text".take(TEXT_MAX).trim(), assignee = assignee?.name))
            remember(who, "made the task ${t.title}" + (assignee?.let { " for ${it.name}" } ?: ""), true, project = project.id)
            val told = assignee?.let { tell(c, person, it, "${safe(person.name)} gave you a task in ${safe(project.name)}: *${safe(t.title)}* (`${t.id.take(TASK_REF)}`). It is on keel's Tasks board.") } ?: true
            "Task *${safe(t.title)}* (`${t.id.take(TASK_REF)}`) is on the board of ${safe(project.name)}" + (assignee?.let { " for ${safe(it.name)}" } ?: "") +
                ". No flow started: you start it in keel." + if (told) "" else " The direct message to them did not go."
        } catch (e: ApiException) {
            remember(who, "was refused a task: ${e.message}", false, project = project.id)
            "keel did not take it: ${e.message}"
        }
    }

    /**
     * `assign <task> @name`: a name on a local task, a label and no account: keel has no such accounts. The task is its key, the start of its id, or its title.
     * A task Jira updates keeps Jira's assignee: keel's sync would overwrite the name, so it says so instead. A key typed by hand, with no status from Jira, is keel's own.
     */
    private fun assign(c: SlackConfig, person: SlackPerson, rest: String): String {
        val m = ASSIGNEE.find(rest) ?: return ASSIGN_HOW
        val ref = rest.substring(0, m.range.first).trim()
        if (ref.isEmpty()) return ASSIGN_HOW
        val (to, problem) = named(c, m.groupValues[1])
        if (to == null) return problem!!
        val who = "${person.name} (${person.id})"
        val low = ref.lowercase()
        fun find(where: String, vararg args: Any) = jdbc.query(
            "SELECT t.id, t.title, t.source, t.external_key, t.external_status, p.name FROM tasks t JOIN projects p ON p.id = t.project_id WHERE $where ORDER BY t.updated_at DESC LIMIT 3",
            { rs, _ -> TaskRef(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5), rs.getString(6)) }, *args)
        val found = find("LOWER(COALESCE(t.external_key, '')) = ? OR t.id = ? OR LOWER(t.title) = ?", low, ref, low)
            .ifEmpty { if (ref.length >= TASK_REF_MIN) find("substr(t.id, 1, ?) = ?", ref.length, ref) else emptyList() }
            .ifEmpty { find("LOWER(t.title) LIKE ?", "%${low.replace("%", "")}%") }
        if (found.isEmpty()) return "keel has no task \"${safe(ref)}\". `status <words>` finds one."
        if (found.size > 1) return "More than one task fits \"${safe(ref)}\": " + found.joinToString("; ") { "`${it.id.take(TASK_REF)}` ${safe(it.title)}" } + ". Say `assign <id> @name`."
        val t = found[0]
        if (t.source == "jira" && !t.externalStatus.isNullOrBlank()) {
            remember(who, "was refused: ${t.key} is Jira's to assign", false)
            return "${safe(t.key ?: t.title)} comes from Jira, and Jira keeps its own assignee. Assign it in Jira."
        }
        return try {
            tasks.update(t.id, TaskPatch(assignee = to.name))
            remember(who, "assigned ${t.title} to ${to.name}", true)
            val told = tell(c, person, to, "${safe(person.name)} gave you a task in ${safe(t.project)}: *${safe(t.title)}* (`${t.id.take(TASK_REF)}`). It is on keel's Tasks board.")
            "*${safe(t.title)}* now says ${safe(to.name)} on the board." + if (told) "" else " The direct message to them did not go."
        } catch (e: ApiException) {
            remember(who, "was refused: ${e.message}", false)
            "keel did not take it: ${e.message}"
        }
    }

    // ---- starting a flow

    /** `start <flow> [in <project>:] <what to do>`: nothing starts yet. One button, for who asked, within [START_MINUTES]; the button carries only an id kept here. */
    private fun startAsked(c: SlackConfig, person: SlackPerson, rest: String, channel: String, thread: String?) {
        if (!c.allowStart) return say(channel, thread, "Starting flows from Slack is off. The person who runs keel can turn it on in Settings › Slack.")
        val m = Regex("^(\\S+?):?\\s+(?:in\\s+([^:]+):\\s*)?(.+)$", RegexOption.DOT_MATCHES_ALL).find(rest) ?: return say(channel, thread, START_HOW)
        val (flow, named, what) = m.destructured
        val all = projects.rows()
        val project = when {
            named.isNotBlank() -> all.firstOrNull { it.id.equals(named.trim(), true) || it.name.equals(named.trim(), true) } ?: return say(channel, thread, "keel has no project ${safe(named)}. It has: ${all.joinToString(", ") { safe(it.name) }}.")
            c.project != null -> all.firstOrNull { it.id == c.project }
            all.size == 1 -> all[0]
            else -> null
        } ?: return say(channel, thread, "Which project? Say `start ${safe(flow)} in <project>: <what to do>`. keel has: ${all.joinToString(", ") { safe(it.name) }}.")
        val token = bot() ?: return
        val now = System.currentTimeMillis()
        starts.entries.removeIf { it.value.until < now }
        val id = UUID.randomUUID().toString()
        val who = "${person.name} (${person.id})"
        starts[id] = PendingStart(person.id, who, project.id, project.name, flow, what.trim().take(START_TEXT_MAX), now + START_MINUTES * 60_000)
        remember(who, "asked to start $flow", true, project = project.id)
        val text = "Start *${safe(flow)}* in *${safe(project.name)}*?\n> ${safe(what.trim().take(START_TEXT_MAX))}\nIts tokens count on keel's budget. Only ${safe(person.name)} can press this, within $START_MINUTES minutes."
        runCatching { api(c).call("chat.postMessage", token, mapOf("channel" to channel, "text" to text, "thread_ts" to thread, "parse" to "none", "link_names" to false,
            "blocks" to SlackForms.startBlocks(text, id, START, START_CANCEL)).filterValues { it != null }) }.onFailure { fail(it) }
    }

    private fun confirmStart(person: SlackPerson, id: String, channel: String, ts: String?, user: String) {
        val p = starts[id] ?: return say(channel, ts, "This one is no longer there. Nothing started: say `start ...` again.")
        if (p.user != person.id) return whisper(channel, user, "Only the person who asked can press this.")
        starts.remove(id)
        if (p.until < System.currentTimeMillis()) return say(channel, ts, "This one is too old. Nothing started: say `start ...` again.")
        if (!config().allowStart) return say(channel, ts, "Starting flows from Slack is off now. Nothing started.")
        val out = try {
            // the dashboard's own start (FlowService.start), so its checks, the budget and the caps all count; no fake model, no dirty folder
            val started = flows.start(p.project, p.workflow, p.what.lineSequence().first().take(TITLE_MAX), null, null, allowFake = false, allowDirty = false, request = p.what,
                options = mapOf("started_via" to "slack", "started_by" to "slack:${p.who}"), branch = DEFAULT_BRANCH)
            remember(p.who, "started the flow ${p.workflow}", true, started.path("thread_id").asText(null), p.project)
            "Started *${safe(p.workflow)}* in ${safe(p.projectName)}. Its gates come to the channel."
        } catch (e: ApiException) {
            remember(p.who, "could not start ${p.workflow}: ${e.message}", false, project = p.project)
            "keel did not start it: ${safe(e.message.orEmpty())}" + (e.hint?.let { " ${safe(it)}" } ?: "")
        }
        say(channel, ts, out)
    }

    private fun cancelStart(person: SlackPerson, id: String, channel: String, ts: String?, user: String) {
        val p = starts[id] ?: return
        if (p.user != person.id) return whisper(channel, user, "Only the person who asked can press this.")
        starts.remove(id)
        remember(p.who, "cancelled the start of ${p.workflow}", true, project = p.project)
        say(channel, ts, "Cancelled. Nothing started.")
    }

    // ---- the chat

    private fun chatKey(channel: String, thread: String?) = "$channel:${thread.orEmpty()}"

    private fun chats() = kv.get<List<SlackChat>>(CHATS) ?: emptyList()

    private fun spendToday(): SlackSpend {
        val day = Time.now().take(10)
        val s = kv.get<SlackSpend>(SPEND)
        return if (s?.day == day) s else SlackSpend(day)
    }

    /**
     * **Let people ask keel in Slack.** The model runs in this plugin's engine part, with read-only tools (the engine enforces that, keel_plugin_slack). Slack's words go in as
     * data. One turn at a time per person, and a daily cap per person, checked before each turn. Its tokens are an agent call, so the Budget page counts them.
     */
    private fun chat(c: SlackConfig, person: SlackPerson, text: String, channel: String, thread: String?) {
        val who = "${person.name} (${person.id})"
        val spent = spendToday().people[person.id] ?: SlackSpent()
        if (spent.usd >= c.chatDayUsd || spent.tokens >= c.chatDayTokens) {
            remember(who, "reached the chat's daily cap", false)
            return say(channel, thread, "You reached your chat cap for today (${"%.2f".format(spent.usd)} of ${"%.2f".format(c.chatDayUsd)} USD, ${spent.tokens} of ${c.chatDayTokens} tokens). " +
                "It starts again tomorrow (UTC). The commands still work: `status`, `waiting`, `task`.")
        }
        if (!chatting.add(person.id)) return say(channel, thread, "I am still answering your last question. One at a time, please.")
        try {
            chatPool.execute {
                try {
                    say(channel, thread, runCatching { chatTurn(c, person, who, text, channel, thread) }.getOrElse {
                        log.warn("slack chat: {}", it.javaClass.simpleName)
                        remember(who, "asked, and the chat failed", false)
                        "The chat failed. The commands still work: `status`, `waiting`."
                    })
                } finally {
                    chatting.remove(person.id)
                }
            }
        } catch (e: RejectedExecutionException) {
            chatting.remove(person.id)
            say(channel, thread, "Too many questions at once. Try again in a minute.")
        }
    }

    private fun chatTurn(c: SlackConfig, person: SlackPerson, who: String, raw: String, channel: String, thread: String?): String {
        val key = chatKey(channel, thread)
        val before = chats().firstOrNull { it.key == key }
        val inProject = Regex("^in\\s+([^:]+):\\s*(.*)$", RegexOption.DOT_MATCHES_ALL).find(raw)
        val text = (inProject?.groupValues?.get(2) ?: raw).trim().take(CHAT_TEXT_MAX)
        val all = projects.rows()
        val named = inProject?.groupValues?.get(1)?.trim()
        val project = when {
            named != null -> all.firstOrNull { it.id.equals(named, true) || it.name.equals(named, true) }
            before != null -> all.firstOrNull { it.id == before.project }
            c.project != null -> all.firstOrNull { it.id == c.project }
            all.size == 1 -> all[0]
            else -> null
        } ?: return "Which project? Say `in <project>: <your question>`. keel has: ${all.joinToString(", ") { safe(it.name) }}."
        val eff = settings.effective(project.id)
        val model = eff.defaultModel
        try {
            LocalOnly.check(eff, listOf(model), "Slack's chat")
        } catch (e: ApiException) {
            remember(who, "was refused the chat: ${e.message}", false, project = project.id)
            return safe(e.message.orEmpty())
        }
        val res = engine.postWaiting("/slack/chat", mapOf(
            "project_id" to project.id, "root" to project.root, "model" to model,
            "keys" to secrets.engineKeys(model.provider, model.mode) + flows.contributedKeys(project.id),
            "text" to text, "history" to before?.turns.orEmpty(), "status" to "${status("")}\n\nWaiting:\n${waiting()}",
        ))
        synchronized(lock) {
            val s = spendToday()
            val mine = s.people[person.id] ?: SlackSpent()
            kv.put(SPEND, s.copy(people = s.people + (person.id to SlackSpent(mine.usd + res.path("cost_usd").asDouble(0.0), mine.tokens + res.path("tokens").asLong(0)))))
        }
        val answer = res.path("text").asText("")
        val ok = res.path("status").asText() == "done" && answer.isNotBlank()
        remember(who, if (ok) "asked keel in the chat" else "asked, and the chat failed", ok, project = project.id)
        if (!ok) return "The chat failed: ${safe(res.path("error").asText("no answer").take(300))}"
        synchronized(lock) {
            val turns = (before?.turns.orEmpty() + SlackChatTurn("person", text.take(CHAT_KEEP)) + SlackChatTurn("keel", answer.take(CHAT_KEEP))).takeLast(CHAT_TURNS)
            kv.put(CHATS, (chats().filter { it.key != key } + SlackChat(key, project.id, turns, Time.now())).takeLast(CHATS_MAX))
        }
        return safe(answer).take(BLOCK_MAX)
    }

    // ---- to Slack

    private fun say(channel: String, thread: String?, text: String) {
        val token = bot() ?: return
        runCatching { api().call("chat.postMessage", token, mapOf("channel" to channel, "text" to text, "thread_ts" to thread, "parse" to "none", "link_names" to false).filterValues { it != null }) }
            .onFailure { fail(it) }
    }

    private fun whisper(channel: String, user: String, text: String) {
        val token = bot() ?: return
        runCatching { api().call("chat.postEphemeral", token, mapOf("channel" to channel, "user" to user, "text" to text)) }.onFailure { fail(it) }
    }

    private fun fail(e: Throwable) {
        problem = if (e is SlackError) e.message else "Slack could not be reached (${e.javaClass.simpleName})."
        log.warn("slack: {}", problem)
    }

    /** A gate waits: its question goes to the channel, with buttons when it may be answered from Slack. */
    fun gateWaits(e: EngineEvent) = later {
        val c = config()
        val token = bot()
        if (c.on && token != null && c.channel.isNotEmpty() && !quiet(e.projectId)) post(c, token, e)
    }

    private fun quiet(pid: String) = runCatching { settings.effective(pid).notify }.getOrDefault("all") == "none"

    private fun post(c: SlackConfig, token: String, e: EngineEvent) {
        val w = runCatching { engine.thread(e.threadId) }.getOrNull()?.path("waiting")?.takeUnless { it.isMissingNode || it.isNull } ?: return
        val flow = jdbc.query("SELECT title FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, e.threadId).firstOrNull().orEmpty()
        val project = jdbc.query("SELECT name FROM projects WHERE id = ?", { rs, _ -> rs.getString(1) }, e.projectId).firstOrNull() ?: e.projectId
        val ref = code(e.threadId, w.path("id").asText(null))
        val always = alwaysInKeel(w)
        val kept = keptInKeel(w)
        val hasForm = SlackForms.hasForm(w)
        // **Allow every gate from Slack**: a kept gate gets buttons for the second list, not one with a form, and never money or secrets
        val confirm = always == null && kept != null && !hasForm && c.allowAllGates && c.strictAllowed.isNotEmpty()
        val only = always ?: kept.takeUnless { confirm } ?: if (hasForm && !SlackForms.fits(w)) FORM_IN_KEEL else null
        val head = "*${safe(project)}: ${safe(flow)}* waits for you at *${safe(w.path("name").asText(w.path("title").asText()))}*"
        val lines = e.data["summary"].let { it as? List<*> }?.mapNotNull { it?.toString() }.orEmpty().joinToString("\n") { safe(it) }
        val detail = if (c.sendDetail) safe(w.path("detail").asText()).take(DETAIL_MAX) else ""
        val how = only ?: if (confirm) "keel normally keeps this one. ${c.strictAllowed.joinToString { safe(it.name) }} may answer it here, with a reason." else howToAnswer(w, ref)
        val shown = listOf(head, lines, detail).filter { it.isNotBlank() }.joinToString("\n\n")
        val text = listOf(shown, how).filter { it.isNotBlank() }.joinToString("\n\n")
        val blocks = mutableListOf<Map<String, Any?>>(mapOf("type" to "section", "text" to mapOf("type" to "mrkdwn", "text" to text.take(BLOCK_MAX))))
        if (only == null) (if (confirm) keptButtons(w, e.threadId) else if (hasForm) formButton(w, e.threadId) else buttons(w, e.threadId))
            .takeIf { it.isNotEmpty() }?.let { blocks += mapOf("type" to "actions", "elements" to it) }
        val res = runCatching { api(c).call("chat.postMessage", token, mapOf("channel" to c.channel, "text" to "${safe(flow)} waits for you", "blocks" to blocks, "parse" to "none", "link_names" to false)) }
            .onFailure { fail(it) }.getOrNull() ?: return
        keep(PostedGate(e.threadId, e.step.orEmpty(), res.path("channel").asText(c.channel), res.path("ts").asText(), shown))
    }

    /** What a button cannot carry: words. */
    private fun howToAnswer(w: JsonNode, ref: String): String {
        val lines = w.path("choice_info").filter { it.path("why").asText() == "required" }.map { "`answer $ref ${it.path("name").asText()} <reason>`" }.toMutableList()
        if (w.path("options").any { it.asText() == "reject" }) lines += "To send it back: `answer $ref reject <reason>`"
        return lines.joinToString("\n")
    }

    /** One button per answer that needs no words. Each carries the question's id, so an old button can never answer a newer question. */
    private fun buttons(w: JsonNode, tid: String): List<Map<String, Any?>> {
        val qid = w.path("id").asText(null)
        val info = w.path("choice_info")
        fun button(label: String, word: String?) = mapOf("type" to "button", "action_id" to ACTION, "text" to mapOf("type" to "plain_text", "text" to label.take(70)),
            "value" to mapper.writeValueAsString(mapOf("t" to tid, "q" to qid, "c" to word)))
        if (info.isArray && info.size() > 0) return info.filter { it.path("why").asText() != "required" }.take(5).map { button(it.path("label").asText(it.path("name").asText()), it.path("name").asText()) }
        return if (w.path("options").any { it.asText() == "approve" }) listOf(button("Approve", null)) else emptyList()
    }

    private fun formButton(w: JsonNode, tid: String): List<Map<String, Any?>> = listOf(mapOf("type" to "button", "action_id" to FORM_ACTION, "text" to mapOf("type" to "plain_text", "text" to "Answer the form"),
        "value" to mapper.writeValueAsString(mapOf("t" to tid, "q" to w.path("id").asText(null)))))

    /** One button per answer, also one that needs words: the confirm asks for a reason anyway. */
    private fun keptButtons(w: JsonNode, tid: String): List<Map<String, Any?>> {
        val qid = w.path("id").asText(null)
        fun button(label: String, word: String?) = mapOf("type" to "button", "action_id" to KEPT_ACTION, "text" to mapOf("type" to "plain_text", "text" to label.take(70)),
            "value" to mapper.writeValueAsString(mapOf("t" to tid, "q" to qid, "c" to word)))
        val info = w.path("choice_info")
        val named = if (info.isArray && info.size() > 0) info.take(4).map { button(it.path("label").asText(it.path("name").asText()), it.path("name").asText()) }
        else if (w.path("options").any { it.asText() == "approve" }) listOf(button("Approve", null)) else emptyList()
        return if (w.path("options").any { it.asText() == "reject" }) named + button("Send back", "reject") else named
    }

    /** The Supervisor asks the person: its words under the gate's message. */
    fun supervisorAsks(e: EngineEvent) = later {
        val c = config()
        val token = bot() ?: return@later
        if (!c.on || c.channel.isEmpty() || quiet(e.projectId)) return@later
        val posted = posted(e.threadId)
        val says = listOfNotNull(e.data["summary"]?.toString(), e.data["reason"]?.toString()).distinct().joinToString(". ") { safe(it).trimEnd('.', ' ') }
        runCatching { api(c).call("chat.postMessage", token, mapOf("channel" to (posted?.channel ?: c.channel), "thread_ts" to posted?.ts, "text" to "The Supervisor asks you: $says", "parse" to "none", "link_names" to false).filterValues { it != null }) }
            .onFailure { fail(it) }
    }

    /** A gate was answered, here or anywhere: its message loses its buttons and says how it ended. */
    fun gateDecided(e: EngineEvent) = later {
        val token = bot() ?: return@later
        val posted = posted(e.threadId)?.takeIf { it.step == e.step } ?: return@later
        val by = e.data["by"]?.toString()
        val who = when {
            by == null -> "a person in keel"
            by.startsWith("slack:") -> "${safe(by.removePrefix("slack:"))} in Slack"
            by == "supervisor" -> "the Supervisor"
            else -> "keel"
        }
        val said = listOfNotNull(e.data["choice"]?.toString() ?: e.data["decision"]?.toString()).joinToString("")
        val text = "${posted.text}\n\nAnswered *${safe(said)}* by $who."
        runCatching { api().call("chat.update", token, mapOf("channel" to posted.channel, "ts" to posted.ts, "text" to "Answered", "blocks" to listOf(mapOf("type" to "section", "text" to mapOf("type" to "mrkdwn", "text" to text.take(BLOCK_MAX)))))) }
            .onFailure { fail(it) }
        forgetPosted(e.threadId)
    }

    private fun keep(p: PostedGate) = synchronized(lock) {
        kv.put(POSTED, ((kv.get<List<PostedGate>>(POSTED) ?: emptyList()).filter { it.thread != p.thread } + p).takeLast(POSTED_MAX))
    }

    private fun posted(tid: String) = (kv.get<List<PostedGate>>(POSTED) ?: emptyList()).firstOrNull { it.thread == tid }

    private fun forgetPosted(tid: String) = synchronized(lock) {
        kv.put(POSTED, (kv.get<List<PostedGate>>(POSTED) ?: emptyList()).filter { it.thread != tid })
    }

    /** Written down twice: the page's list, and keel's events, so a flow's Record shows it. */
    private fun remember(who: String, what: String, ok: Boolean, thread: String? = null, project: String? = null) {
        synchronized(lock) { kv.put(LOG, (listOf(SlackLogRow(Time.now(), who.take(90), what.take(160), ok)) + (kv.get<List<SlackLogRow>>(LOG) ?: emptyList())).take(LOG_MAX)) }
        val pid = project ?: thread?.let { t -> jdbc.query("SELECT project_id FROM threads WHERE id = ?", { rs, _ -> rs.getString(1) }, t).firstOrNull() } ?: return
        runCatching { events.ingest(listOf(EngineEvent("slack.call", thread ?: "slack", pid, null, Time.now(), null, mapOf("who" to who.take(90), "what" to what.take(160), "ok" to ok)))) }
    }

    companion object {
        const val CONFIG = "plugin.slack.config"
        const val LOG = "plugin.slack.log"
        const val POSTED = "plugin.slack.posted"
        const val BOT = "plugin.slack.bot"
        const val APP = "plugin.slack.app"
        const val ACTION = "keel_answer"
        const val FORM_ACTION = "keel_answer_form"
        const val KEPT_ACTION = "keel_answer_kept"
        const val START = "keel_start"
        const val START_CANCEL = "keel_start_cancel"
        const val CHATS = "plugin.slack.chats"
        const val SPEND = "plugin.slack.spend"
        const val CHAT_WORKERS = 2
        const val CHAT_QUEUE = 20
        const val CHAT_TEXT_MAX = 4000
        const val CHAT_KEEP = 2000
        const val CHAT_TURNS = 12
        const val CHATS_MAX = 50
        const val START_MINUTES = 10
        const val START_TEXT_MAX = 2000
        const val TASK_REF = 8
        const val TASK_REF_MIN = 4
        const val QUEUE = 200
        const val SEEN_MAX = 500
        const val LOG_MAX = 50
        const val POSTED_MAX = 100
        const val REF = 6
        const val QREF = 4
        const val STATUS_MAX = 8
        const val WHY_MAX = 2000
        const val TITLE_MAX = 200
        const val TEXT_MAX = 4000
        const val DETAIL_MAX = 2000
        const val BLOCK_MAX = 2900

        /** The Supervisor's never list (plugins/supervisor policy.NEVER; engine/tests checks they stay the same): these stay in keel. */
        val NEVER = setOf("budget", "dependency", "secrets", "readonly", "note", "final-review", "pr")

        /** What no setting ever gives a button: money and secrets. */
        val ALWAYS = setOf("budget", "secrets")

        /** A title that names a cap: the same words as the Supervisor's (policy.CAP_TITLE). */
        val CAP_TITLE = Regex("cap reached|token cap|cost cap|plan window|budget", RegexOption.IGNORE_CASE)

        /** The first words keel matches itself. Anything else is a question for the chat, when that is on. */
        val COMMANDS = setOf("status", "waiting", "task", "assign", "start", "answer", "help")

        /** A name at the end: `@sara`, or the mention Slack sends for a real one. */
        val ASSIGNEE = Regex("(?:^|\\s+)(<@[A-Z0-9]+(?:\\|[^>]*)?>|@[\\p{L}0-9._-]+)\\s*$")

        const val FORM_IN_KEEL = "Slack cannot show this form: too many questions or options, or a very long option. Answer it in keel."

        const val SENDS_SLACK = "To Slack go: each project's name, flow and task titles, status, the name of a gate and its short lines, the Supervisor's words about a gate, and token counts. " +
            "A gate's whole text goes only if you turn that on below; it can hold code. Slack keeps what it gets, under its rules, and everyone in the channel can read it."
        const val SENDS_MODEL = "To your model company: commands are matched on this computer, with no model. Words reach an agent these ways: a reason or a form's words you give at a gate go to its agent, as " +
            "in keel; a task's text goes to the agents of a flow started from it; what you write after `start` goes to that flow's agents. With the chat on, each message that is no command goes there too, " +
            "with the thread's earlier messages, keel's status lines, and the project's files the model chooses to read."

        const val ANSWER_HOW = "Say `answer <code> <choice> <reason>`. `waiting` lists the codes."
        const val TASK_HOW = "Say `task <title>`, `task <title> @name`, `task <title> | <more words>`, or `task in <project>: <title>`."
        const val ASSIGN_HOW = "Say `assign <task> @name`: the task's key, the start of its id, or its title. The name must be on keel's list."
        const val START_HOW = "Say `start <flow> <what to do>` or `start <flow> in <project>: <what to do>`. For example: `start change in shop: add a late filter`."
        val HELP = "I am keel. I match words; no AI reads this.\n`status`: what runs now\n`status <words>`: one flow or task\n`waiting`: what waits for you\n`answer <code> <choice> <reason>`: answer a gate\n$TASK_HOW"

        fun help(c: SlackConfig) = HELP + (if (c.allowStart) "\n$START_HOW" else "") + "\n$ASSIGN_HOW" +
            if (c.allowChat) "\nAnything else goes to the chat: a model answers, and it can only read." else ""

        /** Text for Slack: nothing in a title can mention @channel, make a link or change the look. */
        fun safe(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(Regex("[*_~`]"), "").replace("@", "@​")
    }
}

/** A flow's gate. */
@Component
class SlackGateEvents(private val slack: SlackService) : EngineEventHandler {
    override val prefix = "gate."
    override fun handle(event: EngineEvent) {
        when (event.type) {
            "gate.waiting" -> slack.gateWaits(event)
            "gate.decided" -> slack.gateDecided(event)
        }
    }
}

/** The Supervisor did not answer: it asks the person. */
@Component
class SlackSupervisorEvents(private val slack: SlackService) : EngineEventHandler {
    override val prefix = "supervisor.escalated"
    override fun handle(event: EngineEvent) = slack.supervisorAsks(event)
}

/** The chat's model turns are agent calls, so the Budget page and Live agents count them. */
@Component
class SlackChatEvents(private val calls: AgentCalls) : EngineEventHandler {
    override val prefix = "slack.chat."
    override fun handle(event: EngineEvent) {
        val at = event.at ?: Time.now()
        when (event.type) {
            "slack.chat.started" -> calls.start(event, at, "slack-chat", null)
            "slack.chat.finished" -> calls.finish(event, at)
        }
    }
}

/** It connects when keel has started. */
@Component
class SlackStart(private val slack: SlackService) {
    @EventListener(ApplicationReadyEvent::class)
    fun ready() = slack.restart()
}

@Component
class SlackConnectionKind : ConnectionKind {
    override val kind = "slack"
    override val title = "Slack"
    override val scope = "keel"
    override val order = 60
    override val fields = listOf(ConnectionField("bot_token", "Bot token", "secret", required = true), ConnectionField("app_token", "App-level token", "secret", required = true),
        ConnectionField("channel", "Channel id", required = true), ConnectionField("allowed", "Who may act", "list", required = true))
    override val tokens = setOf(SlackService.BOT, SlackService.APP)
}

@RestController
@RequestMapping("/api/plugins/slack")
class SlackController(private val slack: SlackService) {
    @GetMapping
    fun view(): SlackView = slack.view()

    @PostMapping
    fun save(@RequestBody body: SlackBody): SlackView = slack.save(body)

    @DeleteMapping
    fun forget(): SlackView = slack.forget()
}
