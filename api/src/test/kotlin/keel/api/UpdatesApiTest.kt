package keel.api

import keel.api.common.KvStore
import keel.api.updates.Command
import keel.api.updates.ReleaseCache
import keel.api.updates.ReleaseCheck
import keel.api.updates.ReleaseSource
import keel.api.updates.Semver
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.time.Instant

/** keel 0.18.66 the daily release check and the update notice (updates/ReleaseCheck). A stand-in source: no GitHub. */
class UpdatesApiTest : ApiTest() {
    @Autowired lateinit var check: ReleaseCheck
    @Autowired lateinit var kv: KvStore

    private val calls = mutableListOf<String>()
    private lateinit var before: ReleaseSource

    private fun release(tag: String, body: String = "## What's new\n- Settings › Access\n- a pop-up for updates\n") = mapper.writeValueAsString(
        mapOf("tag_name" to tag, "name" to "keel v2 $tag", "body" to body, "html_url" to "https://example.test/releases/$tag",
            "published_at" to "2026-10-28T10:00:00Z", "draft" to false, "prerelease" to false),
    )

    private fun answer(json: String) { check.source = ReleaseSource { url -> calls += url; json } }

    @BeforeEach
    fun fresh() {
        before = check.source
        kv.delete(ReleaseCheck.CACHE_KEY)
        kv.delete(ReleaseCheck.SETTINGS_KEY)
        calls.clear()
    }

    @AfterEach
    fun back() { check.source = before }

    @Test
    fun `versions compare as semver, a pre-release is older than its release, dev is never older`() {
        assertThat(Semver.newer("0.18.65", "0.18.64")).isTrue()
        assertThat(Semver.newer("v0.19.0", "0.18.99")).isTrue()
        assertThat(Semver.newer("0.18.10", "0.18.9")).isTrue()
        assertThat(Semver.newer("0.18.64", "0.18.64")).isFalse()
        assertThat(Semver.newer("0.18.63", "0.18.64")).isFalse()
        assertThat(Semver.newer("0.18.65", "0.18.65-rc.1")).isTrue()
        assertThat(Semver.newer("0.18.65-rc.1", "0.18.65")).isFalse()
        assertThat(Semver.newer("0.18.65", "dev")).isFalse()
        assertThat(Semver.newer("nightly", "0.18.64")).isFalse()
    }

    @Test
    fun `GET reads only what is kept, Check now asks the source and keeps the notes`() {
        answer(release("v99.0.0"))
        val first = get("/api/updates").andExpect(status().isOk).json()
        assertThat(calls).isEmpty()
        assertThat(first["latest"].isNull).isTrue()
        assertThat(first["newer"].asBoolean()).isFalse()
        assertThat(first["check_daily"].asBoolean()).isTrue()

        val v = post("/api/updates/check").andExpect(status().isOk).json()
        assertThat(calls).hasSize(1)
        assertThat(v["latest"].asText()).isEqualTo("99.0.0")
        assertThat(v["newer"].asBoolean()).isTrue()
        assertThat(v["notes"].asText()).contains("Settings › Access")
        assertThat(v["url"].asText()).isEqualTo("https://example.test/releases/v99.0.0")
        assertThat(v["checked_at"].asText()).endsWith("Z")
        assertThat(v["error"].isNull).isTrue()
        assertThat(v["command"].asText()).contains("keel2 update")
        // keel_health says it too (GET /api/health needs no sign-in)
        assertThat(get("/api/health").json()["latest"].asText()).isEqualTo("99.0.0")
    }

    @Test
    fun `a failed check is silent, keeps what it found before and says why`() {
        answer(release("v99.0.1"))
        post("/api/updates/check").andExpect(status().isOk)
        check.source = ReleaseSource { throw java.net.ConnectException("refused") }
        val v = post("/api/updates/check").andExpect(status().isOk).json()
        assertThat(v["error"].asText()).contains("no network")
        assertThat(v["latest"].asText()).isEqualTo("99.0.1")
        answer("{\"message\": \"Not Found\"}")
        assertThat(post("/api/updates/check").json()["error"].asText()).contains("no release version")
    }

    @Test
    fun `long release notes are cut`() {
        answer(release("v99.0.2", "line\n".repeat(10_000)))
        val notes = post("/api/updates/check").json()["notes"].asText()
        assertThat(notes.length).isLessThanOrEqualTo(ReleaseCheck.NOTES_MAX + 3)
        assertThat(notes).endsWith("…")
    }

    @Test
    fun `the daily tick checks only when it is on and a day has passed, and never with the scheduler off`() {
        assertThat(ReleaseCheck.due(ReleaseCache(), Instant.now())).isTrue()
        assertThat(ReleaseCheck.due(ReleaseCache(checkedAt = Instant.now().minusSeconds(3600).toString()), Instant.now())).isFalse()
        assertThat(ReleaseCheck.due(ReleaseCache(checkedAt = Instant.now().minusSeconds(25 * 3600).toString()), Instant.now())).isTrue()
        // tests run with keel.release-check.scheduler=false: the tick makes no call
        answer(release("v99.0.3"))
        check.tick()
        Thread.sleep(100)
        assertThat(calls).isEmpty()
    }

    @Test
    fun `the settings - check every day and the channel - are saved, a wrong channel is refused`() {
        val v = put("/api/updates/settings", mapOf("check_daily" to false, "channel" to "product")).andExpect(status().isOk).json()
        assertThat(v["check_daily"].asBoolean()).isFalse()
        assertThat(v["channel"].asText()).isEqualTo("product")
        assertThat(get("/api/updates").json()["channel"].asText()).isEqualTo("product")
        put("/api/updates/settings", mapOf("check_daily" to true, "channel" to "nightly")).andExpect(status().isBadRequest)
    }

    @Test
    fun `an MCP write token may not change the update settings or check`() {
        val write = post("/api/mcp/token", mapOf("level" to "write"), mapOf("X-Keel-Token" to TOKEN)).json()["token"].asText()
        val mcp = mapOf("X-Keel-Via" to "mcp", "X-Keel-Mcp-Token" to write)
        put("/api/updates/settings", mapOf("check_daily" to false), mcp).andExpect(status().isForbidden)
        post("/api/updates/check", null, mcp).andExpect(status().isForbidden)
    }

    @Test
    fun `the update command keeps a moving tag, names the new tag for a pinned one and switches channels`() {
        val repo = Command.REPO
        assertThat(Command.of("0.18.66", "stable", "full", "$repo:latest", false).first).isEqualTo("keel2 update")
        assertThat(Command.of("0.18.66", "product", "product", "$repo:product-beta", false).first).isEqualTo("keel2 update")
        assertThat(Command.of("0.18.66", "stable", "full", null, false).first).isEqualTo("keel2 update")
        val pinned = Command.of("0.18.66", "stable", "full", "$repo:0.18.64", false)
        assertThat(pinned.first).isEqualTo("KEEL_IMAGE=$repo:0.18.66 keel2 update")
        assertThat(pinned.second).contains("pinned")
        assertThat(Command.of("0.18.66", "product", "product", "$repo:0.18.64-product-beta", false).first)
            .isEqualTo("KEEL_IMAGE=$repo:0.18.66-product-beta keel2 update")
        assertThat(Command.of("0.18.66", "product", "full", "$repo:latest", false).first)
            .isEqualTo("KEEL_IMAGE=$repo:product-beta keel2 update")
        assertThat(Command.of("0.18.66", "stable", "full", "keel-v2:dev", false).first)
            .isEqualTo("KEEL_IMAGE=$repo:latest keel2 update")
        assertThat(Command.of("0.18.66", "stable", "full", null, true).first).isEqualTo("keel2 update")
        assertThat(Command.repoOf("registry.example:5000/team/keel-v2:0.18.64")).isEqualTo("registry.example:5000/team/keel-v2")
    }
}
