package dev.keel.jetbrains.api

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

// The keel api's JSON, only the fields the plugin's screens read. keel writes snake_case (Jackson SNAKE_CASE); the Json
// instance in KeelApi maps it to these camelCase names. Unknown fields are ignored, so a newer keel never breaks the plugin.

// ---------------------------------------------------------------- pairing (POST /api/devices/pair, GET /api/devices/me)

@Serializable
data class PairRequest(val code: String, val name: String, val platform: String = "jetbrains")

@Serializable
data class Device(val id: String, val name: String = "", val platform: String = "", val createdAt: String? = null)

@Serializable
data class KeelInfo(val name: String = "keel", val version: String = "")

@Serializable
data class PairResponse(val token: String, val device: Device, val keel: KeelInfo = KeelInfo())

/** GET /api/devices/me: the device this token belongs to (the keel side may add more; only `device` is read). */
@Serializable
data class DeviceMe(val device: Device? = null, val keel: KeelInfo? = null, val id: String? = null, val name: String? = null)

@Serializable
data class Health(val ok: Boolean = false, val engine: Boolean = false, val version: String = "", val fake: Boolean = false)

// ---------------------------------------------------------------- projects

@Serializable
data class Project(
    val id: String,
    val name: String,
    val root: String = "",
    val branch: String = "",
    val flow: String? = null,
    val phase: String = "",
    val waiting: Int = 0,
    val running: Int = 0,
)

// ---------------------------------------------------------------- inbox (GET /api/inbox, POST /api/inbox/{tid}/act)

@Serializable
data class ChoiceInfo(
    val name: String,
    val label: String = name,
    val description: String = "",
    /** required | optional | none */
    val why: String = "none",
    val default: Boolean = false,
)

@Serializable
data class QuestionOption(
    val label: String,
    val description: String? = null,
    val recommended: Boolean = false,
    /** required | optional: picking it takes a reason ("<label>: <reason>") */
    val why: String? = null,
)

@Serializable
data class ClarifyQuestion(
    val id: String,
    val question: String,
    val why: String? = null,
    val options: List<QuestionOption> = emptyList(),
    val multiple: Boolean = false,
)

@Serializable
data class Labels(val approve: String? = null, val reject: String? = null)

@Serializable
data class InboxPermission(val id: String, val session: String = "", val command: String = "", val path: String? = null)

@Serializable
data class InboxItem(
    val projectId: String,
    val projectName: String = "",
    val threadId: String = "",
    val flow: String = "",
    val workflowId: String? = null,
    val step: String? = null,
    /** gate | clarify | fix | budget | usage | dependency | permission | task | ... */
    val kind: String,
    val title: String = "",
    val detail: String = "",
    val more: Boolean = false,
    val options: List<String> = emptyList(),
    val choices: List<String>? = null,
    val choiceInfo: List<ChoiceInfo>? = null,
    val questions: List<ClarifyQuestion>? = null,
    val labels: Labels? = null,
    val id: String? = null,
    val phase: String? = null,
    val ac: String? = null,
    val runMode: String? = null,
    val since: String? = null,
    val permission: InboxPermission? = null,
)

@Serializable
data class InboxView(val items: List<InboxItem> = emptyList(), val count: Int = 0)

@Serializable
data class InboxCount(val count: Int = 0, val projects: Map<String, Int> = emptyMap())

/** The answer to a waiting question; `id` binds it to the question it answers (a stale screen gets a 409). */
@Serializable
data class InboxAnswer(
    val decision: String,
    val id: String? = null,
    val why: String? = null,
    val payload: JsonElement? = null,
)

@Serializable
data class DecideBody(val decision: String, val why: String = "")

// ---------------------------------------------------------------- flows

@Serializable
data class Ac(val id: String, val layer: String = "", val title: String = "", val status: String = "todo")

@Serializable
data class Waiting(
    val step: String = "",
    val kind: String = "gate",
    val title: String = "",
    val detail: String = "",
    val options: List<String> = emptyList(),
    val id: String? = null,
    val labels: Labels? = null,
    val questions: List<ClarifyQuestion>? = null,
    val choices: List<String>? = null,
    val choiceInfo: List<ChoiceInfo>? = null,
    val name: String? = null,
)

@Serializable
data class Usage(
    val tokensIn: Long = 0,
    val tokensOut: Long = 0,
    val tokensCached: Long = 0,
    val costUsd: Double = 0.0,
    val capTokens: Long = 0,
)

@Serializable
data class ThreadState(
    val threadId: String,
    val projectId: String = "",
    val workflowId: String = "",
    val title: String = "",
    /** running | waiting | done | failed | stopped */
    val status: String = "running",
    val current: String? = null,
    val phase: String = "",
    val ac: String? = null,
    val acs: List<Ac> = emptyList(),
    val waiting: Waiting? = null,
    val usage: Usage = Usage(),
    val error: String? = null,
    val updatedAt: String = "",
    val runMode: String? = null,
    val gateLog: List<String> = emptyList(),
)

@Serializable
data class WorkflowStep(val id: String, val kind: String = "", val name: String = "", val agent: String? = null, val phase: String? = null)

@Serializable
data class Workflow(val id: String, val name: String = id, val source: String? = null, val steps: List<WorkflowStep> = emptyList())

@Serializable
data class FlowView(val thread: ThreadState? = null, val workflow: Workflow? = null)

/** One flow of a project's history (GET /api/projects/{pid}/runs). */
@Serializable
data class RunRow(
    val threadId: String,
    val title: String = "",
    val workflowId: String? = null,
    val status: String = "running",
    val phase: String? = null,
    val current: String? = null,
    val waiting: String? = null,
    val acsDone: Int = 0,
    val acsTotal: Int = 0,
    val tokens: Long = 0,
    val where: String = "folder",
    val branch: String? = null,
    val error: String? = null,
    val createdAt: String = "",
    val updatedAt: String = "",
    val costUsd: Double = 0.0,
    val latest: Boolean = false,
)

@Serializable
data class Checkpoint(val id: String, val n: Int = 0, val step: String = "", val at: String = "", val note: String = "")

@Serializable
data class StartFlowBody(
    val workflowId: String,
    val title: String,
    val request: String? = null,
    val where: String = "auto",
    val runMode: String = "manual",
    val capTokens: Int? = null,
    /** Flow inputs the workflow may read from its state data (the IDE sends model / effort when picked). */
    val options: Map<String, String>? = null,
)

@Serializable
data class RewindBody(val checkpointId: String)

@Serializable
data class Spend(val tokens: Long = 0, val costUsd: Double = 0.0)

@Serializable
data class CapLeft(val id: String = "", val scope: String = "", val unit: String = "", val limit: Double = 0.0, val used: Double = 0.0)

@Serializable
data class BudgetNow(val today: Spend = Spend(), val month: Spend = Spend(), val caps: List<CapLeft> = emptyList())

// ---------------------------------------------------------------- the review plugin (/api/projects/{pid}/review/...)

@Serializable
data class HostRef(val kind: String = "github", val host: String = "", val path: String = "", val web: String = "")

@Serializable
data class PrSummary(
    val number: Int,
    val title: String = "",
    val author: String = "",
    val branch: String = "",
    val base: String = "",
    val draft: Boolean = false,
    val updatedAt: String = "",
    val url: String = "",
    val reviewRequested: Boolean = false,
    val mine: Boolean = false,
    val assigned: Boolean = false,
)

@Serializable
data class PrList(
    val host: HostRef? = null,
    val me: String? = null,
    val prs: List<PrSummary> = emptyList(),
    val counts: Map<String, Int> = emptyMap(),
    val note: String? = null,
)

@Serializable
data class CheckRun(val name: String, val state: String = "", val url: String? = null)

@Serializable
data class ReviewComment(val id: String, val author: String = "", val body: String = "", val at: String = "", val url: String? = null)

@Serializable
data class ReviewThread(
    val id: String,
    val path: String? = null,
    val line: Int? = null,
    val side: String = "RIGHT",
    val resolved: Boolean = false,
    val outdated: Boolean = false,
    val comments: List<ReviewComment> = emptyList(),
    val startLine: Int? = null,
)

@Serializable
data class ChangedFile(
    val path: String,
    val status: String = "",
    val added: Int = 0,
    val removed: Int = 0,
    val binary: Boolean = false,
    /** a renamed file: its old path */
    val from: String? = null,
)

@Serializable
data class Draft(val id: String, val path: String? = null, val line: Int? = null, val side: String = "RIGHT", val body: String = "", val createdAt: String = "")

@Serializable
data class DraftBody(val key: String, val path: String?, val line: Int?, val side: String = "RIGHT", val body: String)

@Serializable
data class SubmitBody(val key: String, val event: String = "COMMENT", val body: String = "")

@Serializable
data class SubmitResult(val posted: Int = 0, val event: String = "", val url: String? = null, val view: ReviewView? = null)

@Serializable
data class ReviewView(
    val key: String,
    val kind: String = "pr",
    val number: Int? = null,
    val title: String = "",
    val author: String? = null,
    val body: String? = null,
    val base: String = "",
    val branch: String = "",
    val url: String? = null,
    val state: String? = null,
    val draft: Boolean = false,
    val checks: List<CheckRun> = emptyList(),
    val approved: List<String> = emptyList(),
    val changesRequested: List<String> = emptyList(),
    val files: List<ChangedFile> = emptyList(),
    val added: Int = 0,
    val removed: Int = 0,
    val threads: List<ReviewThread> = emptyList(),
    val drafts: List<Draft> = emptyList(),
    val canPost: Boolean = false,
    val host: HostRef? = null,
    val me: String? = null,
    val notes: List<String> = emptyList(),
)

/** 0.4.0 a whole file as the review's head (or base) commit has it (GET /review/file) */
@Serializable
data class ReviewFile(val path: String = "", val ref: String = "", val sha: String = "", val text: String = "", val truncated: Boolean = false)

@Serializable
data class FileDiff(val path: String = "", val diff: String = "", val binary: Boolean = false, val truncated: Boolean = false)

@Serializable
data class JudgeItem(val claim: String = "", val verdict: String = "unclear", val why: String = "", val fix: String? = null)

@Serializable
data class JudgeResult(
    val stage: String = "check",
    val items: List<JudgeItem> = emptyList(),
    val reply: String? = null,
    val text: String? = null,
    val agent: String? = null,
    val model: String? = null,
    val effort: String? = null,
    val tokens: Long? = null,
    val flowId: String? = null,
    val title: String? = null,
)

@Serializable
data class AiRun(
    val id: String = "",
    val kind: String = "",
    /** running | done | failed */
    val status: String = "running",
    val stale: Boolean = false,
    val result: JudgeResult? = null,
    val error: String? = null,
)

@Serializable
data class Addressed(val flowId: String = "", val title: String = "", val branch: String? = null)

@Serializable
data class AiState(
    val judges: Map<String, AiRun> = emptyMap(),
    val addressed: Map<String, Addressed> = emptyMap(),
)

@Serializable
data class AddressResult(val flowId: String = "", val title: String = "", val branch: String? = null, val note: String = "")

@Serializable
data class KeyBody(val key: String, val body: String? = null, val resolved: Boolean? = null, val again: Boolean? = null, val harder: Boolean? = null, val note: String? = null)

// ---------------------------------------------------------------- KeelBot (/api/projects/{pid}/helper/...)

@Serializable
data class Model(val provider: String = "", val mode: String = "", val model: String = "", val effort: String? = null)

@Serializable
data class HelperMessageData(
    val status: String? = null,
    val provider: String? = null,
    val model: String? = null,
    val effort: String? = null,
    val tokensIn: Long? = null,
    val tokensOut: Long? = null,
    val costUsd: Double? = null,
    val files: List<String>? = null,
    val mode: String? = null,
    val reverted: Boolean? = null,
)

@Serializable
data class HelperMessage(
    val n: Int,
    /** user | helper | note */
    val role: String,
    val text: String = "",
    val callId: String? = null,
    val data: HelperMessageData = HelperMessageData(),
    val at: String = "",
)

@Serializable
data class HelperSession(
    val id: String,
    val project: String = "",
    /** ask | edit | fix | side */
    val mode: String = "ask",
    val title: String = "",
    val model: Model = Model(),
    /** idle | running | failed */
    val status: String = "idle",
    val error: String? = null,
    val tokensIn: Long = 0,
    val tokensOut: Long = 0,
    val tokensCached: Long = 0,
    val costUsd: Double = 0.0,
    val turns: Int = 0,
    val createdAt: String = "",
    val updatedAt: String = "",
    val messages: List<HelperMessage>? = null,
    val busy: Boolean? = null,
)

@Serializable
data class NewSession(val mode: String = "ask", val title: String? = null)

@Serializable
data class PatchSession(val mode: String? = null, val title: String? = null, val model: Model? = null)

/** One model of the catalog (GET /api/providers/models): its efforts, when it has its own list. */
data class CatalogModel(val id: String, val label: String, val efforts: List<String>? = null)

/** One provider of the catalog: the modes it runs on (subscription, api, opencode...) with their models. */
data class CatalogProvider(val id: String, val label: String, val modes: Map<String, List<CatalogModel>>, val efforts: List<String>) {
    fun effortsOf(model: String, mode: String): List<String> = modes[mode]?.firstOrNull { it.id == model }?.efforts ?: efforts
}

@Serializable
data class TurnBody(val text: String)

@Serializable
data class TurnStarted(val session: String = "", val callId: String = "", val n: Int = 0)

@Serializable
data class HelperChange(val path: String, val status: String = "modified", val added: Int = 0, val removed: Int = 0)

@Serializable
data class UndoBody(val path: String? = null)

@Serializable
data class DoneBody(val message: String? = null)

@Serializable
data class HelperDone(val ok: Boolean = false, val sha: String? = null, val message: String? = null, val error: String? = null)

@Serializable
data class JobStep(
    val n: Int = 0,
    val at: String = "",
    val kind: String = "text",
    val text: String = "",
    val tool: String? = null,
    val path: String? = null,
    val ok: Boolean? = null,
    val diff: String? = null,
    val output: String? = null,
    val ms: Long? = null,
    val server: String? = null,
)

/** One agent call of the jobs list (GET /api/jobs?project=), for a flow's activity when it is opened mid-run. */
@Serializable
data class JobRow(
    val id: String,
    val projectId: String? = null,
    val threadId: String? = null,
    val agent: String? = null,
    val provider: String? = null,
    val model: String? = null,
    val step: String? = null,
    val phase: String? = null,
    val ac: String? = null,
    val status: String = "",
    val startedAt: String = "",
    val endedAt: String? = null,
    val tokensIn: Long = 0,
    val tokensOut: Long = 0,
    val costUsd: Double = 0.0,
    val effort: String? = null,
)

@Serializable
data class JobSteps(val steps: List<JobStep> = emptyList(), val running: Boolean = false)

/** One engine event from the live stream (GET /api/events, server-sent events). */
@Serializable
data class EngineEvent(
    val type: String = "",
    val threadId: String = "",
    val projectId: String = "",
    val step: String? = null,
    val at: String = "",
    val callId: String? = null,
    val data: JsonElement? = null,
)

/** keel's error body: `{error, hint}`. */
@Serializable
data class ErrorBody(val error: String? = null, val hint: String? = null)
