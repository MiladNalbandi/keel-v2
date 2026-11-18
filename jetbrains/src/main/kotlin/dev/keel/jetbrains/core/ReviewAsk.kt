package dev.keel.jetbrains.core

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.project.Project
import dev.keel.jetbrains.api.answerAfter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap

/**
 * Ask KeelBot about review code and wait for the answer: the review cards ("Ask KeelBot what changed here") and the
 * comment box (0.36) share one ask-mode chat per PR ("<project>#<PR>"), so a follow-up keeps what was asked before and a
 * question asked while an answer runs waits in the chat's queue.
 *
 *   ask(text) ─▶ the PR's chat (made once) ─▶ POST …/turn ─▶ every 1.5 s: live text so far, then the answer ─▶ show
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
                repeat(240) {
                    delay(1_500)
                    val partial = svc.live.liveText.value[turn.callId]
                    val s = api.chat(pid, cid)
                    val answer = answerAfter(s.messages, turn.n)
                    if (answer != null) {
                        edt { show(answer.text.ifBlank { null } ?: s.error ?: "KeelBot gave no answer.", true) }
                        return@launch
                    }
                    if (!partial.isNullOrBlank()) edt { show(partial, false) }
                }
                edt { show("KeelBot is still working. Open the chat to see its answer.", true) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                edt { show(KeelProjectService.say(e), true) }
            }
        }
    }
}
