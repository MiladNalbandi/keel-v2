package keel.api

import keel.api.plugins.CiService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.11.0 the CI/CD plugin: its pipelines, its own fix workflow (listed only where it is on), the fix flow's rules,
 *  and the watcher that tells about a failed run once (and starts the fix when the setting says so). */
class CiApiTest : ApiTest() {
    @Autowired lateinit var ci: CiService

    @Test
    fun `the pipelines and the fix workflow come with the plugin`() {
        engine.ciRuns.clear()
        val (pid, _) = newProject("ci-runs")
        get("/api/projects/$pid/ci/runs").andExpect(status().isConflict)
        assertThat(get("/api/projects/$pid/workflows").json().map { it["id"].asText() }).doesNotContain("ci-fix")
        put("/api/projects/$pid/plugins/ci", mapOf("enabled" to true)).andExpect(status().isOk)
        engine.ciRun(201, "main", failed = true)
        val runs = get("/api/projects/$pid/ci/runs").andExpect(status().isOk).json()
        assertThat(runs[0]["id"].asLong()).isEqualTo(201)
        assertThat(get("/api/projects/$pid/ci/runs/201").json()["log"].asText()).isEqualTo("FAIL")
        post("/api/projects/$pid/ci/runs/201/rerun").andExpect(status().isOk)
        assertThat(engine.lastBody("/plugins/ci/rerun")!!["run"].asLong()).isEqualTo(201)
        val fix = get("/api/projects/$pid/workflows").json().first { it["id"].asText() == "ci-fix" }
        assertThat(fix["plugin"].asText()).isEqualTo("ci")
    }

    @Test
    fun `the fix flow needs the git plugin, the failed branch in the project folder, and a free folder`() {
        engine.ciRuns.clear()
        val (pid, root) = newProject("ci-fix")
        put("/api/projects/$pid/plugins/ci", mapOf("enabled" to true)).andExpect(status().isOk)
        engine.ciRun(301, "feat/other", failed = true)
        val noGit = post("/api/projects/$pid/ci/fix", mapOf("run" to 301)).andExpect(status().isConflict).json()
        assertThat(noGit["hint"].asText()).contains("Turn on Git")
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        val other = post("/api/projects/$pid/ci/fix", mapOf("run" to 301)).andExpect(status().isConflict).json()
        assertThat(other["error"].asText()).contains("CI failed on feat/other, but the project folder is on")
        val branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()
        engine.ciRun(302, branch, failed = true)
        engine.nextThreadIds.add("t-ci-1")
        post("/api/projects/$pid/ci/fix", emptyMap<String, Any>()).andExpect(status().isOk)      // the newest failed run of the branch
        val body = engine.lastBody("/threads")!!
        assertThat(body["workflow"]["id"].asText()).isEqualTo("ci-fix")
        assertThat(body["title"].asText()).isEqualTo("Fix CI: ci on $branch")
        assertThat(body["request"].asText()).contains("run #302")
        assertThat(body["settings"]["plugins"].map { it.asText() }).contains("ci", "git")
    }

    @Test
    fun `the watcher tells about a failed run once, and starts the fix when the setting says so`() {
        engine.ciRuns.clear()
        val (pid, root) = newProject("ci-watch")
        put("/api/projects/$pid/plugins/ci", mapOf("enabled" to true)).andExpect(status().isOk)
        put("/api/projects/$pid/plugins/git", mapOf("enabled" to true)).andExpect(status().isOk)
        val branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").trim()
        engine.ciRun(401, branch, failed = true)
        engine.ciRun(402, branch, failed = false)
        assertThat(post("/api/projects/$pid/ci/check").json()["told"].map { it.asLong() }).containsExactly(401L)
        assertThat(post("/api/projects/$pid/ci/check").json()["told"].size()).isEqualTo(0)           // once per run
        val n = get("/api/notifications").json().first { it["title"].asText() == "CI failed: ci on $branch" }
        assertThat(n["link"].asText()).isEqualTo("/jobs/pipelines")

        put("/api/projects/$pid/settings", mapOf("ci_on_failure" to "fix")).andExpect(status().isOk)
        put("/api/projects/$pid/settings", mapOf("ci_on_failure" to "sometimes")).andExpect(status().isBadRequest)
        engine.ciRun(403, branch, failed = true)
        engine.nextThreadIds.add("t-ci-2")
        assertThat(ci.check(pid)).containsExactly(403L)
        assertThat(engine.lastBody("/threads")!!["workflow"]["id"].asText()).isEqualTo("ci-fix")
        assertThat(get("/api/notifications").json().first { it["body"].asText().contains("keel started the fix flow") }).isNotNull
    }
}
