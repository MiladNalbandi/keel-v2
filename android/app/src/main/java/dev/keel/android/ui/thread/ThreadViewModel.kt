package dev.keel.android.ui.thread

import androidx.lifecycle.viewModelScope
import dev.keel.android.data.AiState
import dev.keel.android.data.DiffLine
import dev.keel.android.data.KeelSession
import dev.keel.android.data.LiveHub
import dev.keel.android.data.ReviewThread
import dev.keel.android.data.ReviewView
import dev.keel.android.data.diffSnippet
import dev.keel.android.ui.common.KeelViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class PrTab { THREADS, FILES, CHECKS }

data class FileDiffState(val loading: Boolean = true, val rows: List<dev.keel.android.data.DiffRow> = emptyList(), val binary: Boolean = false, val truncated: Boolean = false, val error: Throwable? = null)

data class ThreadUi(
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val error: Throwable? = null,
    val view: ReviewView? = null,
    val ai: AiState = AiState(),
    val tab: PrTab = PrTab.THREADS,
    val selected: String? = null,
    val snippets: Map<String, List<DiffLine>> = emptyMap(),
    /** keel's checks the person dismissed (on this phone only) */
    val dismissed: Set<String> = emptySet(),
    /** the reply drafts by thread: keel's reply for the same point can go into several boxes (0.26.1) */
    val replies: Map<String, String> = emptyMap(),
    /** a thread to scroll to (a "Same point as the thread on line N" link), and a count so the same one can be asked again */
    val focus: String? = null,
    val focusN: Int = 0,
    val busy: String? = null,
    val actionError: Throwable? = null,
    val note: String? = null,
    /** a file's diff for the diff screen, by path */
    val files: Map<String, FileDiffState> = emptyMap(),
) {
    val drafts get() = view?.drafts.orEmpty()
    val me get() = view?.me
    val open get() = view?.threads.orEmpty().filter { !it.resolved }
    val resolved get() = view?.threads.orEmpty().filter { it.resolved }
    val current: ReviewThread? get() = open.firstOrNull { it.id == selected } ?: open.firstOrNull()
    fun replyOf(id: String) = replies[id].orEmpty()
    fun waitsForMe(t: ReviewThread) = !t.resolved && t.comments.lastOrNull()?.author.let { it != null && it != me }
}

/**
 * One pull request of the review plugin: its threads (reply, resolve), keel's "Is the reviewer right?" check (judge,
 * polled while it runs), Address it (a flow that makes the change), and its files and checks.
 */
class ThreadViewModel(session: KeelSession, live: LiveHub?, private val projectId: String, number: Int) : KeelViewModel(session, live) {
    val key = "pr:$number"
    private val _ui = MutableStateFlow(ThreadUi())
    val ui: StateFlow<ThreadUi> = _ui.asStateFlow()
    private var poll: Job? = null

    init {
        load()
    }

    fun refresh() {
        _ui.update { it.copy(refreshing = true) }
        load(refresh = true)
    }

    fun load(refresh: Boolean = false) {
        viewModelScope.launch {
            try {
                val v = api.review(projectId, key, refresh)
                val ai = runCatching { api.ai(projectId, key) }.getOrDefault(_ui.value.ai)
                _ui.update { s ->
                    val firstWaiting = v.threads.firstOrNull { !it.resolved && it.comments.lastOrNull()?.author != v.me } ?: v.threads.firstOrNull { !it.resolved }
                    s.copy(loading = false, refreshing = false, error = null, view = v, ai = ai, selected = s.selected?.takeIf { id -> v.threads.any { it.id == id && !it.resolved } } ?: firstWaiting?.id)
                }
                snippets(v)
                if (ai.running()) startPoll()
            } catch (e: Exception) {
                _ui.update { it.copy(loading = false, refreshing = false, error = if (it.view == null) e else null, actionError = if (it.view != null) e else null) }
            }
        }
    }

    private fun snippets(v: ReviewView) {
        val paths = v.threads.filter { !it.resolved && it.path != null && it.line != null }.mapNotNull { it.path }.distinct()
        paths.forEach { path ->
            viewModelScope.launch {
                val d = runCatching { api.diff(projectId, key, path) }.getOrNull() ?: return@launch
                _ui.update { s ->
                    val add = v.threads.filter { it.path == path && !it.resolved }.associate { it.id to diffSnippet(d.diff, it.line, it.startLine) }
                    s.copy(snippets = s.snippets + add)
                }
            }
        }
    }

    fun select(id: String) = _ui.update { it.copy(selected = id) }
    fun setTab(t: PrTab) = _ui.update { it.copy(tab = t) }
    fun setReply(threadId: String, v: String) = _ui.update { it.copy(replies = it.replies + (threadId to v)) }

    /** Show another thread of the same point: pick it and scroll to it. */
    fun goTo(threadId: String) = _ui.update { it.copy(selected = threadId, tab = PrTab.THREADS, focus = threadId, focusN = it.focusN + 1) }
    fun dismiss(id: String) = _ui.update { it.copy(dismissed = it.dismissed + id) }
    fun clearNote() = _ui.update { it.copy(note = null, actionError = null) }

    /** keel's check: is the reviewer right? `harder` = check deeper (a stronger model, the whole files). */
    fun judge(threadId: String, harder: Boolean = false) {
        _ui.update { it.copy(busy = "judge", actionError = null, dismissed = it.dismissed - threadId) }
        viewModelScope.launch {
            try {
                val ai = api.judge(projectId, key, threadId, again = harder || _ui.value.ai.judges[threadId] != null, harder = harder)
                _ui.update { it.copy(busy = null, ai = ai) }
                startPoll()
            } catch (e: Exception) {
                _ui.update { it.copy(busy = null, actionError = e) }
            }
        }
    }

    private fun startPoll() {
        if (poll?.isActive == true) return
        poll = viewModelScope.launch {
            repeat(90) {
                delay(2_000)
                val ai = runCatching { api.ai(projectId, key) }.getOrNull() ?: return@repeat
                _ui.update { it.copy(ai = ai) }
                if (!ai.running()) return@launch
            }
        }
    }

    fun useAsReply(threadId: String) {
        val r = _ui.value.ai.judges[threadId]?.result ?: return
        val text = r.reply ?: r.items.firstOrNull()?.fix ?: return
        _ui.update { it.copy(selected = threadId, replies = it.replies + (threadId to text)) }
    }

    /** keel's reply into this thread's box and into the box of each other thread of the same point. Nothing is posted:
     *  the person sends each one. */
    fun useOnAll(threadId: String) {
        val r = _ui.value.ai.judges[threadId]?.result ?: return
        val text = r.reply ?: return
        val ids = listOf(threadId) + r.sameAs.map { it.threadId }
        _ui.update { s ->
            s.copy(selected = threadId, replies = s.replies + ids.associateWith { text },
                note = "keel's reply is in the reply box of ${if (ids.size == 2) "both threads" else "all ${ids.size} threads"}. You send each one.")
        }
    }

    fun address(threadId: String) {
        _ui.update { it.copy(busy = "address", actionError = null) }
        viewModelScope.launch {
            try {
                val r = api.address(projectId, key, threadId)
                _ui.update { it.copy(busy = null, note = "keel started “${r.title}”${r.branch?.let { b -> " on $b" } ?: ""}. Follow it in Flows.") }
            } catch (e: Exception) {
                _ui.update { it.copy(busy = null, actionError = e) }
            }
        }
    }

    fun resolve(threadId: String, resolved: Boolean = true) {
        _ui.update { it.copy(busy = "resolve", actionError = null) }
        viewModelScope.launch {
            try {
                val v = api.resolve(projectId, key, threadId, resolved)
                _ui.update { s -> s.copy(busy = null, view = v, selected = v.threads.firstOrNull { !it.resolved }?.id, note = if (resolved) "Resolved." else "Opened again.") }
            } catch (e: Exception) {
                _ui.update { it.copy(busy = null, actionError = e) }
            }
        }
    }

    fun loadFile(path: String, force: Boolean = false) {
        if (!force && _ui.value.files[path]?.let { it.error == null } == true) return
        _ui.update { it.copy(files = it.files + (path to FileDiffState())) }
        viewModelScope.launch {
            try {
                val d = api.diff(projectId, key, path)
                _ui.update { it.copy(files = it.files + (path to FileDiffState(false, dev.keel.android.data.parseDiff(d.diff), d.binary, d.truncated))) }
            } catch (e: Exception) {
                _ui.update { it.copy(files = it.files + (path to FileDiffState(false, error = e))) }
            }
        }
    }

    /** A comment on a line: kept as a pending comment of your review until [sendReview] (the web's line comment). */
    fun comment(path: String, line: Int, side: String, body: String) {
        if (body.isBlank()) return
        _ui.update { it.copy(busy = "comment", actionError = null) }
        viewModelScope.launch {
            try {
                val d = api.addDraft(projectId, key, path, line, side, body.trim())
                _ui.update { s -> s.copy(busy = null, view = s.view?.copy(drafts = s.view.drafts + d), note = "Comment on line $line kept for your review. Send the review to post it.") }
            } catch (e: Exception) {
                _ui.update { it.copy(busy = null, actionError = e) }
            }
        }
    }

    fun deleteDraft(id: String) {
        viewModelScope.launch {
            try {
                api.deleteDraft(projectId, id)
                _ui.update { s -> s.copy(view = s.view?.copy(drafts = s.view.drafts.filterNot { it.id == id })) }
            } catch (e: Exception) {
                _ui.update { it.copy(actionError = e) }
            }
        }
    }

    /** Post the pending comments as one review (COMMENT). */
    fun sendReview() {
        _ui.update { it.copy(busy = "review", actionError = null) }
        viewModelScope.launch {
            try {
                val r = api.submitReview(projectId, key)
                _ui.update { it.copy(busy = null, view = r.view ?: it.view?.copy(drafts = emptyList()), note = "Review sent: ${r.posted} comment${if (r.posted == 1) "" else "s"} posted.") }
                if (r.view == null) load()
            } catch (e: Exception) {
                _ui.update { it.copy(busy = null, actionError = e) }
            }
        }
    }

    fun sendReply(threadId: String) {
        val s = _ui.value
        val body = s.replyOf(threadId).trim()
        if (body.isEmpty() || s.busy != null) return
        _ui.update { it.copy(busy = "reply", actionError = null) }
        viewModelScope.launch {
            try {
                val v = api.reply(projectId, key, threadId, body)
                _ui.update { it.copy(busy = null, view = v, replies = it.replies - threadId, note = "Reply posted.") }
                // an author's "fixed" after a new commit: keel checks it in the background (it never resolves)
                runCatching { api.ai(projectId, key) }.getOrNull()?.let { ai ->
                    _ui.update { it.copy(ai = ai) }
                    if (ai.running()) startPoll()
                }
            } catch (e: Exception) {
                _ui.update { it.copy(busy = null, actionError = e) }
            }
        }
    }
}

/** A check of keel's still runs: "Is the reviewer right?" or the check of an author's "fixed". */
private fun AiState.running() = judges.values.any { it.status == "running" } || fixed.values.any { it.status == "running" }
