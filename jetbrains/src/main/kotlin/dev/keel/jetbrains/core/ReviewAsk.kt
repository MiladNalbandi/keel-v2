package dev.keel.jetbrains.core

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.project.Project
import dev.keel.jetbrains.api.answerAfter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Ask KeelBot about review code and wait for the answer: the review cards ("Ask KeelBot what changed here") and the
 * comment box (0.36) share one ask-mode chat per PR ("<project>#<PR>"), so a follow-up keeps what was asked before and a
 * question asked while an answer runs waits in the chat's queue.
 *
 *   ask(text) ─▶ the PR's chat (made once) ─▶ POST …/turn ─▶ the live text as it is written ─▶ the answer ─▶ show
 *
 * 0.37 (#192) the text so far shows as each helper.delta arrives (not once per poll), and helper.finished of the turn
 * reads the answer at once; the poll (0.4 s, then 1.5 s) is only the fallback for a missed event.
 */
object ReviewAsk {
    /** 0.35 the KeelBot chat of each PR's review ("<project>#<PR>"), for the IDE's life. */
    private val chats = ConcurrentHashMap<String, String>()

    fun chatKey(pid: String, pr: Any?): String = "$pid#$pr"

    /**
     * Asks [text] in the chat of [chatKey]. [onChat] gets the chat's id once it is known; [show] gets the text so far
     * (done = false) and then the answer or why there is none (done = true). Both run on the EDT. Cancel the job to stop
     * waiting (the answer still lands in the chat).
     */
    fun ask(project: Project, chatKey: String, text: String, onChat: (String) -> Unit = {}, show: (String, Boolean) -> Unit): Job? {
        val svc = KeelProjectService.of(project)
        val pid = svc.pid ?: return null
        fun edt(block: () -> Unit) = ApplicationManager.getApplication().invokeLater({ if (!project.isDisposed) block() }, ModalityState.any())
        return svc.scope.launch {
            try {
                val api = KeelConnection.get().ensure()
                var chatId = chats[chatKey]
                val turn = chatId?.let { runCatching { api.send(pid, it, text) }.getOrNull() } ?: run {
                    val chat = api.newChat(pid, "ask")
                    chatId = chat.id
                    chats[chatKey] = chat.id
                    api.send(pid, chat.id, text)
                }
                val cid = chatId!!
                edt { onChat(cid) }
                val done = AtomicBoolean(false)
                val finished = Channel<Unit>(Channel.CONFLATED)
                coroutineScope {
                    // the answer as KeelBot writes it, as it comes (never after the final answer: done)
                    val live = launch {
                        svc.live.liveText.collect { m ->
                            val partial = m[turn.callId]
                            if (!partial.isNullOrBlank() && !done.get()) edt { if (!done.get()) show(partial, false) }
                        }
                    }
                    val ended = launch {
                        svc.live.changes.collect { ev ->
                            if (ev.type == "helper.finished" && ev.callId == turn.callId) finished.trySend(Unit)
                        }
                    }
                    try {
                        var got: String? = null
                        for (i in 0 until 240) {
                            withTimeoutOrNull(if (i < 3) 400L else 1_500L) { finished.receive() }
                            val s = api.chat(pid, cid)
                            val answer = answerAfter(s.messages, turn.n)
                            if (answer != null) {
                                got = answer.text.ifBlank { null } ?: s.error ?: "KeelBot gave no answer."
                                break
                            }
                        }
                        done.set(true)
                        val shown = got ?: "KeelBot is still working. Open the chat to see its answer."
                        edt { show(shown, true) }
                    } finally {
                        done.set(true)
                        live.cancel()
                        ended.cancel()
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                edt { show(KeelProjectService.say(e), true) }
            }
        }
    }
}
