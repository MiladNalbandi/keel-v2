package dev.keel.android.data

import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import java.time.Instant

/**
 * Demo mode (debug builds, like keel's KEEL_FAKE): an OkHttp interceptor that answers the app's api calls with the
 * mockup's data, so every screen runs through the real client code without a keel. Answers change the data
 * (an answered question leaves the inbox, a reply is added to its thread, KeelBot answers a message).
 */
class DemoBackend {
    private fun ago(sec: Long) = Instant.now().minusSeconds(sec).toString()
    private val pid = "app-order-management-system"

    private val projects = listOf(
        Project(pid, pid, "/workspace/$pid", "feature/SHOPS-1702-totals", "feature", "green", waiting = 3, running = 2),
        Project("billing-router", "billing-router", "/workspace/billing-router", "main"),
    )

    private val specAcs = listOf(
        Ac("AC-1", "api", "The VAT webhook keeps the stored VAT when the payload has no VAT.", "todo"),
        Ac("AC-2", "db", "vat_updated_at changes only when the VAT changes.", "todo"),
        Ac("AC-3", "api", "A cleared VAT is logged with the supplier and the order.", "todo"),
        Ac("AC-4", "ui", "The checkout page shows \"last known VAT\" with its date.", "todo"),
    )

    private val threads = mutableMapOf(
        "t-1769" to ThreadState(
            threadId = "t-1769", projectId = pid, workflowId = "feature", title = "Keep the stored VAT when it is absent",
            status = "waiting", current = "spec-gate", phase = "spec", acs = specAcs,
            waiting = Waiting(
                step = "spec-gate", kind = "gate", title = "Approve the spec?", id = "q-spec-1769",
                detail = "Keep the stored VAT when Pricing Engine sends none.\n4 acceptance criteria, 2 API changes. The test step starts after you approve.",
                options = listOf("approve", "reject"),
                labels = Labels("Approve", "Change it"),
                choices = listOf("approve", "change", "stop"),
                choiceInfo = listOf(
                    ChoiceInfo("approve", "Approve", "The test step starts: an agent writes a failing test for each criterion.", "none", true),
                    ChoiceInfo("change", "Change it", "Tell keel what to change. It rewrites the spec and asks you again.", "required"),
                    ChoiceInfo("stop", "Stop the flow", "The flow ends here. The spec stays in .keel/specs."),
                ),
            ),
            usage = Usage(tokensIn = 14_000, tokensOut = 4_200, costUsd = 0.42), updatedAt = ago(720),
        ),
        "t-1771" to ThreadState(
            threadId = "t-1771", projectId = pid, workflowId = "interview", title = "Supplier VAT sync for Acme and Globex",
            status = "waiting", current = "interview", phase = "triage",
            waiting = Waiting(
                step = "interview", kind = "clarify", title = "Which suppliers should sync the VAT first?", id = "q-suppliers",
                questions = listOf(
                    ClarifyQuestion(
                        "suppliers", "Which suppliers should sync the VAT first?", "The adapter is built per supplier.",
                        listOf(QuestionOption("Acme", recommended = true), QuestionOption("Globex", recommended = true), QuestionOption("Initech"), QuestionOption("Umbrella")),
                        multiple = true,
                    ),
                ),
            ),
            usage = Usage(costUsd = 1.08), updatedAt = ago(2400),
        ),
        "t-1702" to ThreadState(
            threadId = "t-1702", projectId = pid, workflowId = "feature", title = "Checkout page totals",
            status = "waiting", current = "green", phase = "green", ac = "AC-3",
            acs = listOf(
                Ac("AC-1", "api", "totals count paid orders only", "done"),
                Ac("AC-2", "api", "the totals follow the date filter", "done"),
                Ac("AC-3", "api", "returned orders are not counted twice", "red"),
            ),
            waiting = Waiting(
                step = "green__fix", kind = "fix", title = "AC-3 is still red after 3 tries. What now?", id = "q-stuck-1702",
                detail = "TotalsTest::returned_counted_once\nexpected 1284, got 1290\ntests/Unit/TotalsTest.php:48",
                choices = listOf("again", "investigate", "skip", "stop"),
                choiceInfo = listOf(
                    ChoiceInfo("again", "Try again with a note", "One more try, with your hint in the prompt.", "optional", true),
                    ChoiceInfo("investigate", "Find the cause first", "The investigator reads the failure and the code, then code tries again."),
                    ChoiceInfo("skip", "Skip AC-3", "The flow goes on without it. A reason is needed; it shows in the PR.", "required"),
                    ChoiceInfo("stop", "Stop the flow", "The commits so far stay on the branch."),
                ),
            ),
            usage = Usage(tokensIn = 33_000, tokensOut = 8_000, costUsd = 0.96), updatedAt = ago(600),
            gateLog = listOf("spec approved by you"),
        ),
        "t-1785" to ThreadState(
            threadId = "t-1785", projectId = pid, workflowId = "quick", title = "Order search: accept lower-case references",
            status = "waiting", current = "change", phase = "change",
            waiting = Waiting(
                step = "change", kind = "fix", title = "make the change failed", id = "q-crash-1785",
                detail = "The provider refused the key. Check the key in Settings › Models & keys (Test says what is wrong), then retry.\n\n" +
                    "What happened (auth): Error code: 401 - invalid x-api-key\n\nApprove to try again, reject to stop the flow.",
                options = listOf("approve", "reject"),
                crash = CrashInfo("auth", "The provider refused the key", "Check the key in Settings › Models & keys (Test says what is wrong), then retry."),
            ),
            usage = Usage(tokensIn = 4_100, tokensOut = 500, costUsd = 0.02), updatedAt = ago(240),
            gateLog = listOf("plan approved by you", "make the change: the provider refused the key"),
        ),
    )

    private val runs = mutableListOf(
        RunRow("t-1769", "Keep the stored VAT when it is absent", "feature", "waiting", "spec", "spec-gate", "Approve the spec?", 0, 4, 18_000, "worktree", "feature/SHOPS-1769", costUsd = 0.42, updatedAt = ago(720)),
        RunRow("t-1771", "Supplier VAT sync for Acme and Globex", "interview", "running", "green", "green", null, 2, 5, 52_000, "worktree", "feature/SHOPS-1771", costUsd = 1.08, updatedAt = ago(300)),
        RunRow("t-1702", "Checkout page totals", "feature", "waiting", "green", "green", "AC-3 is still red after 3 tries", 2, 3, 41_000, "folder", "feature/SHOPS-1702-totals", costUsd = 0.96, updatedAt = ago(600)),
        RunRow("t-1785", "Order search: accept lower-case references", "quick", "waiting", "change", "change", "make the change failed", 0, 0, 4_600, "worktree", "quick/order-search-case", costUsd = 0.02, updatedAt = ago(240)),
        RunRow("t-fix-9", "Fix: duplicate webhook events", "fix", "running", "gate", "review", null, 1, 1, 12_000, "worktree", "fix/dup-webhooks", costUsd = 0.31, updatedAt = ago(120)),
        RunRow("t-1650", "Order search by reference", "feature", "done", "done", null, null, 6, 6, 88_000, "worktree", "feature/SHOPS-1650", costUsd = 2.10, updatedAt = ago(172_800)),
    )

    private val history = mapOf(
        "t-1702" to listOf(
            Checkpoint("c1", 1, "spec gate", ago(3000), "You approved the spec (3 criteria)."),
            Checkpoint("c2", 2, "red", ago(2500), "Tests written: 3 red, as expected."),
            Checkpoint("c3", 3, "AC gate", ago(1900), "AC-1 green. Reviewed: ok."),
            Checkpoint("c4", 4, "AC gate", ago(1100), "AC-2 green. Reviewed: ok."),
            Checkpoint("c5", 5, "green", ago(600), "AC-3 red after 3 tries. The flow waits for you."),
        ),
    )

    private val inboxPermission = InboxItem(
        projectId = pid, projectName = pid, threadId = "", flow = "KeelBot", kind = "permission", title = "KeelBot asks to run a command",
        detail = "vendor/bin/phpunit --filter VatAbsent", id = "perm-1", since = ago(300),
        permission = InboxPermission("perm-1", "chat-1", "vendor/bin/phpunit --filter VatAbsent"),
    )
    private var permissionOpen = true

    private val reviews = DemoReviews()
    private val prs get() = reviews.list

    private val judges = mutableMapOf<String, AiRun>()
    private val judgeAsked = mutableMapOf<String, Int>()

    private val chatMessages = mutableListOf(
        HelperMessage(1, "user", "Why does VatAbsentKeepsStoredTest fail? It passed yesterday.\n@tests/Integration/…/VatAbsentKeepsStoredTest.php", at = ago(400)),
        HelperMessage(
            2, "helper",
            "**The webhook clears the VAT.** Since yesterday's change, `VatWebhook::handle()` saves `null` when the payload has no VAT, so the stored one is lost.\n\n" +
                "- `src/Vat/VatWebhook.php:58` writes the field without a check\n- the test is right: it matches AC-1 of the spec\n\n" +
                "```keel-questions\n{\"questions\":[{\"id\":\"fix\",\"question\":\"How should I fix it?\",\"options\":[" +
                "{\"label\":\"Fix the webhook\",\"description\":\"Keep the stored VAT when none comes. 1 file, the test stays.\",\"recommended\":true}," +
                "{\"label\":\"Fix it and log it\",\"description\":\"Also log a cleared VAT with the supplier (AC-3).\"}," +
                "{\"label\":\"Start a fix flow\",\"description\":\"Test first, then the change, then a review.\"}]}]}\n```",
            callId = "call-2", data = HelperMessageData(status = "done", provider = "claude", model = "sonnet", effort = "high", tokensIn = 12_000, tokensOut = 2_200, costUsd = 0.09),
            at = ago(380),
        ),
    )
    private var chatMode = "ask"
    private var chatModel = Model("claude", "api", "sonnet", "high")

    private fun session(withMessages: Boolean) = HelperSession(
        id = "chat-1", project = pid, mode = chatMode, title = "Why does the VAT test fail?",
        model = chatModel, status = "idle",
        tokensIn = 12_000, tokensOut = 2_200, costUsd = 0.09, turns = chatMessages.count { it.role == "user" },
        updatedAt = ago(380), messages = if (withMessages) chatMessages.toList() else null,
    )

    private fun inbox(): InboxView {
        val items = threads.values.filter { it.status == "waiting" && it.waiting != null }.map { t ->
            val w = t.waiting!!
            InboxItem(
                projectId = t.projectId, projectName = t.projectId, threadId = t.threadId, flow = t.title, workflowId = t.workflowId,
                step = w.step, kind = w.kind, title = w.title, detail = w.detail, options = w.options, choices = w.choices,
                choiceInfo = w.choiceInfo, questions = w.questions, labels = w.labels, id = w.id, phase = t.phase, ac = t.ac,
                since = t.updatedAt,
            )
        } + listOfNotNull(inboxPermission.takeIf { permissionOpen })
        return InboxView(items, items.size)
    }

    fun handle(method: String, path: String, query: Map<String, String>, body: String): Pair<Int, String> {
        val seg = path.trim('/').split('/')
        fun <T> ok(ser: KSerializer<T>, v: T) = 200 to KeelJson.encodeToString(ser, v)
        fun bodyObj() = runCatching { KeelJson.parseToJsonElement(body) as JsonObject }.getOrDefault(JsonObject(emptyMap()))
        fun JsonObject.s(k: String) = (this[k] as? JsonPrimitive)?.contentOrNull
        return when {
            path == "/api/health" -> ok(Health.serializer(), Health(true, true, "demo", true))
            path == "/api/devices/me" -> ok(DeviceMe.serializer(), DeviceMe(Device("demo", "This phone", "android")))
            path == "/api/projects" -> ok(ListSerializer(Project.serializer()), projects)
            path == "/api/inbox" -> ok(InboxView.serializer(), inbox())
            path == "/api/inbox/count" -> ok(InboxCount.serializer(), InboxCount(inbox().count))
            seg.size == 4 && seg[1] == "inbox" && seg[3] == "act" -> {
                val t = threads[seg[2]] ?: return 404 to """{"error":"No such flow"}"""
                val b = bodyObj()
                if (b.s("id") != null && b.s("id") != t.waiting?.id) return 409 to """{"error":"This flow now asks something else","hint":"Reload: the question changed."}"""
                val done = t.copy(status = "running", waiting = null, phase = if (t.phase == "spec") "red" else t.phase)
                threads[t.threadId] = done
                runs.replaceAll { if (it.threadId == t.threadId) it.copy(status = "running", waiting = null, phase = done.phase) else it }
                ok(ThreadState.serializer(), done)
            }
            seg.size == 4 && seg[1] == "approvals" -> { permissionOpen = false; 200 to "{}" }
            seg.size == 4 && seg[1] == "projects" && seg[3] == "runs" -> ok(ListSerializer(RunRow.serializer()), if (seg[2] == pid) runs else emptyList())
            seg.size == 5 && seg[1] == "projects" && seg[3] == "flows" -> {
                val t = threads[seg[4]] ?: runs.firstOrNull { it.threadId == seg[4] }?.let { r ->
                    ThreadState(r.threadId, pid, r.workflowId ?: "feature", r.title, r.status, r.current, r.phase ?: "", acs = (1..r.acsTotal).map { Ac("AC-$it", "api", "criterion $it", if (it <= r.acsDone) "done" else "todo") }, usage = Usage(costUsd = r.costUsd), updatedAt = r.updatedAt)
                }
                ok(FlowView.serializer(), FlowView(t, Workflow(t?.workflowId ?: "feature", "${t?.workflowId ?: "feature"} (keel)")))
            }
            seg.size == 4 && seg[1] == "projects" && seg[3] == "flows" && method == "POST" -> {
                val b = bodyObj()
                val r = RunRow("t-new-${runs.size}", b.s("title") ?: "New flow", b.s("workflow_id"), "running", "spec", "spec", costUsd = 0.0, updatedAt = Instant.now().toString(), where = "worktree")
                runs.add(0, r)
                ok(ThreadState.serializer(), ThreadState(r.threadId, pid, r.workflowId ?: "feature", r.title, "running", phase = "spec"))
            }
            seg.size == 4 && seg[1] == "projects" && seg[3] == "workflows" -> ok(ListSerializer(Workflow.serializer()), listOf(Workflow("feature", "feature (keel)"), Workflow("fix", "fix (keel)"), Workflow("interview", "interview (keel)")))
            seg.size == 5 && seg[1] == "projects" && seg[3] == "budget" -> ok(BudgetNow.serializer(), BudgetNow(Spend(410_000, 1.76), caps = listOf(CapLeft("d", "day", "usd", 20.0, 7.6))))
            seg.size == 4 && seg[1] == "threads" && seg[3] == "history" -> ok(ListSerializer(Checkpoint.serializer()), history[seg[2]] ?: emptyList())
            seg.size == 4 && seg[1] == "threads" && seg[3] == "stop" -> {
                val t = threads[seg[2]]?.copy(status = "stopped", waiting = null) ?: ThreadState(seg[2], status = "stopped")
                threads[seg[2]] = t
                runs.replaceAll { if (it.threadId == seg[2]) it.copy(status = "stopped", waiting = null) else it }
                ok(ThreadState.serializer(), t)
            }
            path.endsWith("/review/prs") -> {
                val f = query["filter"] ?: "review"
                val list = when (f) { "mine" -> prs.filter { it.mine }; "all" -> prs; else -> prs.filter { it.reviewRequested } }
                ok(PrList.serializer(), PrList(HostRef("github", "github.com", "acme/checkout"), "alex", list, mapOf("review" to 2, "mine" to 2, "all" to 4)))
            }
            path.endsWith("/review/view") -> ok(ReviewView.serializer(), reviews.view(query["key"]))
            path.endsWith("/review/diff") -> ok(FileDiff.serializer(), reviews.diff(query["key"], query["path"].orEmpty()))
            path.endsWith("/review/drafts") && method == "POST" -> {
                val b = bodyObj()
                ok(Draft.serializer(), reviews.addDraft(b.s("key"), b.s("path"), b.s("line")?.toIntOrNull(), b.s("side") ?: "RIGHT", b.s("body").orEmpty()))
            }
            seg.size == 6 && seg[3] == "review" && seg[4] == "drafts" && method == "DELETE" -> { reviews.deleteDraft(seg[5]); 200 to """{"ok":true}""" }
            path.endsWith("/review/submit") -> ok(SubmitResult.serializer(), reviews.submit(bodyObj().s("key")))
            path == "/api/providers/models" -> 200 to CATALOG
            path.endsWith("/review/ai") -> {
                judges.replaceAll { tid, run ->
                    if (run.status == "running" && (judgeAsked.merge(tid, 1, Int::plus) ?: 0) >= 2) run.copy(status = "done", result = judgeResult(tid)) else run
                }
                ok(AiState.serializer(), AiState(judges.toMap()))
            }
            seg.size == 7 && seg[3] == "review" && seg[4] == "threads" && seg[6] == "judge" -> {
                judges[seg[5]] = AiRun("j-${seg[5]}", "judge", "running")
                judgeAsked[seg[5]] = 0
                ok(AiState.serializer(), AiState(judges.toMap()))
            }
            seg.size == 7 && seg[3] == "review" && seg[4] == "threads" && seg[6] == "reply" -> {
                ok(ReviewView.serializer(), reviews.reply(seg[5], bodyObj().s("body").orEmpty()))
            }
            seg.size == 7 && seg[3] == "review" && seg[4] == "threads" && seg[6] == "resolve" -> {
                ok(ReviewView.serializer(), reviews.resolve(seg[5], (bodyObj()["resolved"] as? JsonPrimitive)?.contentOrNull != "false"))
            }
            seg.size == 7 && seg[3] == "review" && seg[4] == "threads" && seg[6] == "address" ->
                ok(AddressResult.serializer(), AddressResult("t-addr", "Address: factory in setUp()", "keel/address-412", "keel started a fix flow on its own branch."))
            path.endsWith("/helper/sessions") && method == "GET" -> ok(ListSerializer(HelperSession.serializer()), listOf(session(false)))
            path.endsWith("/helper/sessions") && method == "POST" -> {
                chatMessages.clear()
                chatMode = bodyObj().s("mode") ?: "ask"
                ok(HelperSession.serializer(), session(true).copy(title = "New chat"))
            }
            seg.size == 6 && seg[3] == "helper" && seg[4] == "sessions" && method == "PATCH" -> {
                val b = bodyObj()
                chatMode = b.s("mode") ?: chatMode
                (b["model"] as? JsonObject)?.let { m -> chatModel = Model(m.s("provider").orEmpty(), m.s("mode").orEmpty(), m.s("model").orEmpty(), m.s("effort")) }
                ok(HelperSession.serializer(), session(true))
            }
            seg.size == 6 && seg[3] == "helper" && seg[4] == "sessions" -> ok(HelperSession.serializer(), session(true))
            seg.size == 7 && seg[3] == "helper" && seg[6] == "turn" -> {
                val text = bodyObj().s("text").orEmpty()
                val n = (chatMessages.maxOfOrNull { it.n } ?: 0) + 1
                chatMessages += HelperMessage(n, "user", text, at = Instant.now().toString())
                chatMessages += HelperMessage(
                    n + 1, "helper",
                    if (text.startsWith(ANSWERS_PREFIX)) "Sent. I start the change in Edit mode: `src/Vat/VatWebhook.php` keeps the stored VAT when none comes."
                    else "This is the demo keel: a real keel answers here, reading your project.",
                    callId = "call-${n + 1}", data = HelperMessageData(status = "done", provider = "claude", model = "sonnet"), at = Instant.now().toString(),
                )
                ok(TurnStarted.serializer(), TurnStarted("chat-1", "call-${n + 1}", n))
            }
            seg.size == 7 && seg[3] == "helper" && seg[6] == "changes" -> ok(ListSerializer(HelperChange.serializer()), emptyList())
            seg.size == 4 && seg[1] == "jobs" && seg[3] == "steps" -> ok(
                JobSteps.serializer(),
                JobSteps(
                    listOf(
                        JobStep(1, kind = "tool", text = "vendor/bin/phpunit --filter VatAbsent", ok = false),
                        JobStep(2, kind = "tool", text = "git log -1 --stat src/Vat"),
                        JobStep(3, kind = "read", path = "src/Vat/VatWebhook.php"),
                        JobStep(4, kind = "read", path = "src/Vat/VatRepository.php:40–72"),
                        JobStep(5, kind = "read", path = "tests/Integration/Vat/VatAbsentKeepsStoredTest.php"),
                    ),
                ),
            )
            else -> 404 to """{"error":"The demo keel does not know $method $path","hint":"Pair with a real keel."}"""
        }
    }

    private fun judgeResult(tid: String) = JudgeResult(
        stage = "done",
        items = listOf(
            JudgeItem(
                "a factory would fit here", "unclear",
                "Lines 50–78 set up suppliers, resolver and link with raw inserts; a factory would shorten them. The diff does not show whether the repo already has factories (e.g. Foundry).",
                "Use a factory in setUp() if one exists, else keep the inserts.",
            ),
        ),
        reply = when (tid) {
            "412-1" -> "Good idea if the repo has factories; I checked and will switch setUp() to them, else keep the inserts."
            "412-2" -> "Yes: the log line gets the order id next to the supplier."
            "409-1" -> "Thanks, the jitter looks right to me."
            else -> "Thanks, I will look at it."
        },
        agent = "judge", model = "claude · sonnet", effort = "high", tokens = 2_000,
    )

    /** The interceptor: every call to the demo address is answered here. */
    fun interceptor() = Interceptor { chain ->
        val req = chain.request()
        val buf = Buffer()
        req.body?.writeTo(buf)
        val query = (0 until req.url.querySize).associate { req.url.queryParameterName(it) to (req.url.queryParameterValue(it) ?: "") }
        val (code, json) = synchronized(this) { handle(req.method, req.url.encodedPath, query, buf.readUtf8()) }
        Response.Builder().request(req).protocol(Protocol.HTTP_1_1).code(code).message(if (code < 300) "OK" else "Error")
            .header("Content-Type", "application/json")
            .body(json.toResponseBody(KeelApi.JSON)).build()
    }

    fun client(): OkHttpClient = OkHttpClient.Builder().addInterceptor(interceptor()).build()
}

/** The demo's model catalog (GET /api/providers/models), in the shape keel 0.26 sends: every model on its API key. */
private val CATALOG = """
{"claude":{"label":"Anthropic (Claude)","modes":{"api":[{"id":"opus","label":"opus"},{"id":"sonnet","label":"sonnet"},{"id":"haiku","label":"haiku","efforts":[]}]},
"efforts":["low","medium","high","xhigh","max"],"default":{"mode":"api","model":"sonnet","effort":"high"},"source":"builtin"},
"codex":{"label":"OpenAI","modes":{"api":[{"id":"gpt-5.5","label":"gpt-5.5"},{"id":"gpt-5.5-mini","label":"gpt-5.5-mini"}]},
"efforts":["minimal","low","medium","high"],"default":{"mode":"api","model":"gpt-5.5","effort":"medium"},"source":"builtin"},
"copilot":{"label":"GitHub Models","modes":{"api":[{"id":"openai/gpt-5","label":"openai/gpt-5"},{"id":"openai/gpt-5-mini","label":"openai/gpt-5-mini"}]},
"efforts":[],"default":{"mode":"api","model":"openai/gpt-5"},"source":"builtin"}}
""".trimIndent()
