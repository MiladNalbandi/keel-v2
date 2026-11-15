package dev.keel.jetbrains.core

import com.intellij.openapi.Disposable
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Disposer
import dev.keel.jetbrains.api.AiRun
import dev.keel.jetbrains.api.AiState
import dev.keel.jetbrains.api.FixRun
import dev.keel.jetbrains.api.KeelApi
import dev.keel.jetbrains.api.ReviewView
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The pull request open in the Review tab, shared by the tab and the threads inside the editor: its view (files,
 * threads, pending comments), keel's checks of the threads (judge runs, polled while they run) and what was dismissed.
 */
@Service(Service.Level.PROJECT)
class ReviewSession(private val project: Project) {
    @Volatile var key: String? = null; private set
    @Volatile var view: ReviewView? = null; private set
    @Volatile var ai: AiState = AiState(); private set
    @Volatile var error: String? = null; private set
    val dismissed = HashSet<String>()
    private val listeners = CopyOnWriteArrayList<() -> Unit>()
    private var poll: Job? = null

    private val svc get() = KeelProjectService.of(project)

    fun onChange(parent: Disposable, l: () -> Unit) {
        listeners += l
        Disposer.register(parent) { listeners -= l }
    }

    private fun changed() = listeners.forEach { it() }

    fun open(prKey: String, refresh: Boolean = false) {
        if (prKey != key) {
            view = null
            ai = AiState()
            dismissed.clear()
        }
        key = prKey
        reload(refresh)
    }

    fun close() {
        key = null
        view = null
        poll?.cancel()
        changed()
    }

    fun reload(refresh: Boolean = false) {
        val k = key ?: return
        val pid = svc.pid ?: return
        svc.io({ api -> api.review(pid, k, refresh) to runCatching { api.ai(pid, k) }.getOrNull() }) { r ->
            if (key != k) return@io
            r.onSuccess { (v, a) ->
                view = v
                if (a != null) ai = a
                error = null
                if (ai.running()) pollAi()
            }.onFailure { error = KeelProjectService.say(it) }
            changed()
        }
    }

    /** A call that answers the new view (reply, resolve). */
    fun act(work: suspend (KeelApi, String, String) -> ReviewView, done: (Throwable?) -> Unit = {}) {
        val k = key ?: return
        val pid = svc.pid ?: return
        svc.io({ work(it, pid, k) }) { r ->
            r.onSuccess { if (key == k) view = it }
            done(r.exceptionOrNull())
            changed()
            // 0.26.1 an author's "fixed" after a new commit: keel checks it in the background (it never resolves)
            if (r.isSuccess) refreshAi()
        }
    }

    /** keel's checks read again (after a reply: a fix check may have started); polled while one runs. */
    fun refreshAi() {
        val k = key ?: return
        val pid = svc.pid ?: return
        svc.io({ it.ai(pid, k) }) { r ->
            if (key != k) return@io
            r.onSuccess { ai = it; changed(); if (it.running()) pollAi() }
        }
    }

    /** keel's check of the author's "fixed" for a thread (0.26.1). */
    fun fixOf(threadId: String): FixRun? = ai.fixed[threadId]

    /** Reply drafts by thread: keel's reply for the same point waits in each thread's Reply (0.26.1). Nothing is posted. */
    val drafts: MutableMap<String, String> = java.util.concurrent.ConcurrentHashMap()

    /** Any other call; the view is read again after it. */
    fun <T> call(work: suspend (KeelApi, String, String) -> T, done: (Result<T>) -> Unit = {}) {
        val k = key ?: return
        val pid = svc.pid ?: return
        svc.io({ work(it, pid, k) }) { r ->
            done(r)
            reload()
        }
    }

    fun judgeOf(threadId: String): AiRun? = ai.judges[threadId]?.takeIf { threadId !in dismissed }

    /** keel's "Is the reviewer right?" check; it runs in the background, so poll until it is done. */
    fun judge(threadId: String, again: Boolean = false, harder: Boolean = false, done: (Throwable?) -> Unit = {}) {
        val k = key ?: return
        val pid = svc.pid ?: return
        dismissed -= threadId
        svc.io({ it.judge(pid, k, threadId, again, harder) }) { r ->
            r.onSuccess { ai = it }
            done(r.exceptionOrNull())
            changed()
            if (r.isSuccess) pollAi()
        }
    }

    private fun pollAi() {
        if (poll?.isActive == true) return
        val k = key ?: return
        val pid = svc.pid ?: return
        poll = svc.scope.launch {
            repeat(120) {
                delay(2_000)
                if (key != k) return@launch
                val a = runCatching { KeelConnection.get().ensure().ai(pid, k) }.getOrNull() ?: return@repeat
                ai = a
                com.intellij.openapi.application.ApplicationManager.getApplication().invokeLater({ changed() }, com.intellij.openapi.application.ModalityState.any())
                if (!a.running()) return@launch
            }
        }
    }

    /** Tests: a review and keel's checks as keel would answer them, without a keel. */
    @org.jetbrains.annotations.TestOnly
    internal fun showForTest(v: ReviewView, a: AiState) {
        view = v
        ai = a
    }

    fun dismiss(threadId: String) {
        dismissed += threadId
        changed()
    }

    companion object {
        fun of(project: Project): ReviewSession = project.service()
    }
}

/** A check of keel's still runs: "Is the reviewer right?" or the check of an author's "fixed". */
private fun AiState.running() = judges.values.any { it.status == "running" } || fixed.values.any { it.status == "running" }
