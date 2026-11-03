package dev.keel.jetbrains.api

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNamingStrategy
import kotlinx.serialization.serializer
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.CoroutineContext

@OptIn(ExperimentalSerializationApi::class)
val KeelJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
    coerceInputValues = true
    isLenient = true
    namingStrategy = JsonNamingStrategy.SnakeCase
}

/**
 * What went wrong with a call. `status` 0 = keel could not be reached (no answer at all); otherwise the HTTP status with
 * keel's own `{error, hint}`.
 */
class KeelException(val status: Int, override val message: String, val hint: String? = null, cause: Throwable? = null) :
    IOException(message, cause) {
    val unreachable: Boolean get() = status == 0

    /** The device token is no longer accepted (the device was removed on the keel side, or keel forgot it). */
    val deviceRemoved: Boolean
        get() = (status == 403 && message.contains("device", ignoreCase = true) && message.contains("removed", ignoreCase = true)) ||
            (status == 403 && message.contains("sign-in key", ignoreCase = true)) ||
            status == 401
}

/**
 * A small typed client for the keel api, with only what the plugin's screens need. Every call sends
 * `Authorization: Bearer <device token>` (except pairing). Paths are the api's own (see the README's endpoint table).
 */
class KeelApi(
    baseUrl: String,
    private val token: () -> String?,
    val http: OkHttpClient = defaultClient(),
    private val io: CoroutineContext = Dispatchers.IO,
    /** Told when keel says this device was removed: the plugin asks to connect again. */
    private val onRemoved: () -> Unit = {},
) {
    val base: HttpUrl = normalizeBase(baseUrl) ?: throw KeelException(0, "That is not a keel address: $baseUrl", "Use http://<ip>:<port> or https://<name>.")

    // ---------------------------------------------------------------- pairing
    suspend fun pair(code: String, deviceName: String): PairResponse =
        call("POST", "/api/devices/pair", PairRequest(code.trim(), deviceName), auth = false)

    suspend fun me(): DeviceMe = call("GET", "/api/devices/me")

    suspend fun health(): Health = call("GET", "/api/health", auth = false)

    // ---------------------------------------------------------------- projects, inbox
    suspend fun projects(): List<Project> = call("GET", "/api/projects")

    suspend fun inbox(): InboxView = call("GET", "/api/inbox")

    suspend fun inboxCount(): InboxCount = call("GET", "/api/inbox/count")

    /** Answer the question a flow waits at. 409: the flow now asks something else (reload). */
    suspend fun answer(threadId: String, answer: InboxAnswer): ThreadState = call("POST", "/api/inbox/${enc(threadId)}/act", answer)

    /** A command that waits for the person's OK (kind permission): once | always | deny. */
    suspend fun decideApproval(id: String, decision: String, why: String = ""): Unit =
        callUnit("POST", "/api/approvals/${enc(id)}/decide", DecideBody(decision, why))

    // ---------------------------------------------------------------- flows
    suspend fun runs(pid: String, limit: Int = 30): List<RunRow> = call("GET", "/api/projects/${enc(pid)}/runs?limit=$limit")

    suspend fun flowOf(pid: String, tid: String): FlowView = call("GET", "/api/projects/${enc(pid)}/flows/${enc(tid)}")

    suspend fun history(tid: String): List<Checkpoint> = call("GET", "/api/threads/${enc(tid)}/history")

    suspend fun stop(tid: String): ThreadState = call("POST", "/api/threads/${enc(tid)}/stop", emptyMap<String, String>())

    /** Resume a stopped flow from a saved step (the web's Resume: a rewind to the newest checkpoint). */
    suspend fun rewind(tid: String, checkpointId: String): ThreadState =
        call("POST", "/api/threads/${enc(tid)}/rewind", RewindBody(checkpointId))

    suspend fun workflows(pid: String): List<Workflow> = call("GET", "/api/projects/${enc(pid)}/workflows")

    suspend fun startFlow(pid: String, body: StartFlowBody): ThreadState = call("POST", "/api/projects/${enc(pid)}/flows", body)

    suspend fun budgetNow(pid: String): BudgetNow = call("GET", "/api/projects/${enc(pid)}/budget/now")

    // ---------------------------------------------------------------- the review plugin
    private fun r(pid: String) = "/api/projects/${enc(pid)}/review"

    /** filter: review | assigned | mine | all */
    suspend fun prs(pid: String, filter: String): PrList = call("GET", "${r(pid)}/prs?filter=$filter")

    suspend fun review(pid: String, key: String, refresh: Boolean = false): ReviewView =
        call("GET", "${r(pid)}/view?key=${enc(key)}${if (refresh) "&refresh=true" else ""}")

    suspend fun diff(pid: String, key: String, path: String): FileDiff = call("GET", "${r(pid)}/diff?key=${enc(key)}&path=${enc(path)}")

    /** 0.4.0 the whole file as the review's head or base commit has it (side: head | base) */
    suspend fun reviewFile(pid: String, key: String, path: String, side: String): ReviewFile =
        call("GET", "${r(pid)}/file?key=${enc(key)}&path=${enc(path)}&side=$side")

    suspend fun reply(pid: String, key: String, threadId: String, body: String): ReviewView =
        call("POST", "${r(pid)}/threads/${enc(threadId)}/reply", KeyBody(key, body = body))

    suspend fun resolve(pid: String, key: String, threadId: String, resolved: Boolean): ReviewView =
        call("POST", "${r(pid)}/threads/${enc(threadId)}/resolve", KeyBody(key, resolved = resolved))

    suspend fun ai(pid: String, key: String): AiState = call("GET", "${r(pid)}/ai?key=${enc(key)}")

    /** keel's "Is the reviewer right?" check of one thread; it runs in the background (poll [ai]). */
    suspend fun judge(pid: String, key: String, threadId: String, again: Boolean = false, harder: Boolean = false): AiState =
        call("POST", "${r(pid)}/threads/${enc(threadId)}/judge", KeyBody(key, again = again.takeIf { it }, harder = harder.takeIf { it }))

    suspend fun address(pid: String, key: String, threadId: String, note: String? = null): AddressResult =
        call("POST", "${r(pid)}/threads/${enc(threadId)}/address", KeyBody(key, note = note))

    /** A comment on a line, kept as a pending comment of your review (the web's line comment, PendingTab). */
    suspend fun addDraft(pid: String, key: String, path: String, line: Int, side: String, body: String): Draft =
        call("POST", "${r(pid)}/drafts", DraftBody(key, path, line, side, body))

    suspend fun deleteDraft(pid: String, id: String): Unit = callUnit("DELETE", "${r(pid)}/drafts/${enc(id)}")

    /** Send the pending comments as one review (COMMENT | APPROVE | REQUEST_CHANGES). */
    suspend fun submitReview(pid: String, key: String, event: String = "COMMENT", body: String = ""): SubmitResult =
        call("POST", "${r(pid)}/submit", SubmitBody(key, event, body))

    // ---------------------------------------------------------------- models
    /** The model catalog the web's ModelPicker reads (GET /api/providers/models). */
    suspend fun providerModels(): List<CatalogProvider> = parseCatalog(call<kotlinx.serialization.json.JsonElement>("GET", "/api/providers/models"))

    suspend fun setChatModel(pid: String, sid: String, model: Model): HelperSession =
        call("PATCH", "${h(pid)}/sessions/${enc(sid)}", PatchSession(model = model))

    // ---------------------------------------------------------------- KeelBot
    private fun h(pid: String) = "/api/projects/${enc(pid)}/helper"

    suspend fun chats(pid: String): List<HelperSession> = call("GET", "${h(pid)}/sessions")

    suspend fun chat(pid: String, sid: String): HelperSession = call("GET", "${h(pid)}/sessions/${enc(sid)}")

    suspend fun newChat(pid: String, mode: String): HelperSession = call("POST", "${h(pid)}/sessions", NewSession(mode))

    suspend fun setChatMode(pid: String, sid: String, mode: String): HelperSession =
        call("PATCH", "${h(pid)}/sessions/${enc(sid)}", PatchSession(mode = mode))

    suspend fun send(pid: String, sid: String, text: String): TurnStarted = call("POST", "${h(pid)}/sessions/${enc(sid)}/turn", TurnBody(text))

    suspend fun stopChat(pid: String, sid: String): HelperSession = call("POST", "${h(pid)}/sessions/${enc(sid)}/stop", emptyMap<String, String>())

    suspend fun changes(pid: String, sid: String): List<HelperChange> = call("GET", "${h(pid)}/sessions/${enc(sid)}/changes")

    suspend fun undo(pid: String, sid: String, path: String? = null): List<HelperChange> =
        call("POST", "${h(pid)}/sessions/${enc(sid)}/undo", UndoBody(path))

    /** Keep: keel commits KeelBot's files after the checks. */
    suspend fun keep(pid: String, sid: String): HelperDone = call("POST", "${h(pid)}/sessions/${enc(sid)}/done", DoneBody())

    suspend fun jobSteps(callId: String, after: Int = 0): JobSteps =
        call("GET", "/api/jobs/${enc(callId)}/steps" + if (after > 0) "?after=$after" else "")

    /** The project's recent agent calls (newest first). */
    suspend fun jobs(pid: String, limit: Int = 50): List<JobRow> = call("GET", "/api/jobs?project=${enc(pid)}&limit=$limit")

    // ---------------------------------------------------------------- live stream
    /** The SSE url: this project's events plus the notifications of every project. */
    fun eventsUrl(pid: String?): HttpUrl = base.newBuilder().addPathSegments("api/events").apply {
        addQueryParameter("project", pid ?: "*")
        if (pid != null) addQueryParameter("notify", "all")
    }.build()

    fun authHeader(): String? = token()?.let { "Bearer $it" }

    // ---------------------------------------------------------------- transport
    suspend inline fun <reified T> call(method: String, path: String, body: Any? = null, auth: Boolean = true): T =
        callWith(serializer(), method, path, body?.let { encodeBody(it) }, auth)

    fun encodeBody(body: Any): String = when (body) {
        is String -> body
        is Map<*, *> -> if (body.isEmpty()) "{}" else KeelJson.encodeToString(serializer<Map<String, String>>(), body.mapKeys { it.key.toString() }.mapValues { it.value.toString() })
        else -> KeelJson.encodeToString(KeelJson.serializersModule.serializer(body::class.java), body)
    }

    suspend fun callUnit(method: String, path: String, body: Any? = null) {
        raw(method, path, body?.let { encodeBody(it) }, true)
    }

    suspend fun <T> callWith(ser: KSerializer<T>, method: String, path: String, body: String?, auth: Boolean): T {
        val text = raw(method, path, body, auth)
        return try {
            KeelJson.decodeFromString(ser, text.ifBlank { "{}" })
        } catch (e: Exception) {
            throw KeelException(200, "keel sent an answer the plugin cannot read.", "Is this a keel v2 address? Update the plugin or keel.", e)
        }
    }

    private suspend fun raw(method: String, path: String, body: String?, auth: Boolean): String = withContext(io) {
        val url = (base.toString().trimEnd('/') + path).toHttpUrlOrNull()
            ?: throw KeelException(0, "Bad address: $path")
        val req = Request.Builder().url(url)
            .header("Accept", "application/json")
            .header("X-Keel-Via", "jetbrains")
            .apply { if (auth) token()?.let { header("Authorization", "Bearer $it") } }
            .method(method, body?.toRequestBody(JSON) ?: if (method == "GET" || method == "HEAD") null else "{}".toRequestBody(JSON))
            .build()
        val res = try {
            http.newCall(req).execute()
        } catch (e: IOException) {
            throw KeelException(0, "Cannot reach keel at ${base.host}.", UNREACHABLE_HINT, e)
        }
        res.use {
            val text = it.body?.string().orEmpty()
            if (!it.isSuccessful) {
                val err = runCatching { KeelJson.decodeFromString(ErrorBody.serializer(), text) }.getOrNull()
                val ex = KeelException(it.code, err?.error ?: "keel answered ${it.code}.", err?.hint)
                if (auth && ex.deviceRemoved) onRemoved()
                throw ex
            }
            val type = it.header("Content-Type").orEmpty()
            if (text.isNotBlank() && !type.contains("json") && text.trimStart().startsWith("<")) {
                throw KeelException(it.code, "keel sent a web page instead of data.", "Check the address: it must be keel's api, not another site.")
            }
            text
        }
    }

    companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
        const val UNREACHABLE_HINT =
            "Check that keel runs (keel2 status) and the address in Settings › Tools › keel."

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()

        /** "192.168.1.5:8080" → http://192.168.1.5:8080/; keeps https; drops a trailing /api and any path. */
        fun normalizeBase(raw: String): HttpUrl? {
            var s = raw.trim()
            if (s.isEmpty()) return null
            if (!s.contains("://")) s = "http://$s"
            val u = s.toHttpUrlOrNull() ?: return null
            if (u.scheme != "http" && u.scheme != "https") return null
            return u.newBuilder().encodedPath("/").query(null).fragment(null).build()
        }

        fun enc(s: String): String = java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}
