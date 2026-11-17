package dev.keel.jetbrains.api

import kotlinx.coroutines.runBlocking
import org.junit.Assume.assumeTrue
import org.junit.Test

/**
 * The plugin's client against a real keel: every read the tool window makes must decode what keel really answers.
 * Only with KEEL_LIVE_URL and KEEL_LIVE_KEY (a throw-away keel, as e2e/daily starts one); skipped otherwise.
 */
class LiveKeelTest {
    @Test fun everyReadDecodesWhatKeelAnswers() = runBlocking {
        val url = System.getenv("KEEL_LIVE_URL"); val key = System.getenv("KEEL_LIVE_KEY")
        assumeTrue("no live keel", !url.isNullOrBlank() && !key.isNullOrBlank())
        val api = KeelApi(url, token = { key })
        val bad = mutableListOf<String>()
        suspend fun <T> read(what: String, f: suspend () -> T): T? = runCatching { f() }
            .onSuccess { println("ok   $what: ${it.toString().take(140)}") }
            .onFailure {
                // keel saying "no" in its own words (a plugin that is off for the project) is an answer the plugin shows
                if (it is KeelException && it.status == 409) return@onFailure println("ok   $what: keel says ${it.message}")
                bad += "$what: ${it::class.simpleName}: ${it.message?.take(400)}"; println("FAIL $what: ${it.message?.take(400)}")
            }.getOrNull()
        read("health") { api.health() }
        read("inbox") { api.inbox() }
        read("inboxCount") { api.inboxCount() }
        read("providerModels") { api.providerModels() }
        val projects = read("projects") { api.projects() }.orEmpty()
        for (p in projects) {
            val pid = p.id
            val runs = read("runs $pid") { api.runs(pid) }.orEmpty()
            for (r in runs) {
                read("flowOf $pid ${r.title} (${r.status})") { api.flowOf(pid, r.threadId) }
                read("history ${r.title}") { api.history(r.threadId) }
            }
            read("workflows $pid") { api.workflows(pid) }
            read("budgetNow $pid") { api.budgetNow(pid) }
            read("jobs $pid") { api.jobs(pid) }
            val chats = read("chats $pid") { api.chats(pid) }.orEmpty()
            for (c in chats.take(3)) {
                val full = read("chat ${c.id}") { api.chat(pid, c.id) }
                read("changes ${c.id}") { api.changes(pid, c.id) }
                full?.messages?.mapNotNull { it.callId }?.take(2)?.forEach { cid -> read("jobSteps $cid") { api.jobSteps(cid) } }
            }
            for (f in listOf("review", "mine", "all")) read("prs $pid $f") { api.prs(pid, f) }
        }
        check(bad.isEmpty()) { "the plugin cannot read ${bad.size} answer(s) of keel:\n" + bad.joinToString("\n") }
    }
}
