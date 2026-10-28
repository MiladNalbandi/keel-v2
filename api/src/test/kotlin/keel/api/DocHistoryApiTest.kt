package keel.api

import keel.api.knowledge.DocHistoryService
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** v0.15.9 keel's file history: versions from engine events, diff, restore, and what a flow applied. */
class DocHistoryApiTest : ApiTest() {

    private val path = ".keel/specs/scores.md"

    private fun version(pid: String, tid: String, text: String, by: String = "agent", name: String? = "explorer") =
        mapOf("type" to "doc.version", "thread_id" to tid, "project_id" to pid, "step" to "spec", "at" to "2026-10-14T10:00:00Z",
            "data" to mapOf("path" to path, "kind" to "spec", "sha256" to DocHistoryService.sha256(text.toByteArray()), "content" to text,
                "size" to text.length, "by_kind" to by, "by_name" to name, "phase" to "spec"))

    private fun send(vararg events: Map<String, Any?>) = post("/internal/events", events.toList(), mapOf("X-Keel-Token" to TOKEN)).andExpect(status().isOk)

    @Test
    fun `versions come from engine events, the same text twice is one version, and the events table has no content`(@Autowired jdbc: JdbcTemplate) {
        val (pid, root) = newProject("history-versions", mapOf(".keel/config.yml" to "layout: keel\n"))
        Files.createDirectories(root.resolve(".keel/specs"))
        Files.writeString(root.resolve(path), "# Scores v2\n")
        send(version(pid, "t-1", "# Scores\n"), version(pid, "t-1", "# Scores\n"), version(pid, "t-1", "# Scores v2\n", "keel", "spec_freeze"))
        val list = get("/api/projects/$pid/docs/history/versions?path=$path").json()
        assertThat(list.map { it["version"].asInt() }).containsExactly(2, 1)
        assertThat(list[0]["by_kind"].asText()).isEqualTo("keel")
        assertThat(list[0].has("content") && !list[0]["content"].isNull).isFalse()
        val stored = jdbc.queryForObject("SELECT COUNT(*) FROM events WHERE type = 'doc.version' AND data_json LIKE '%\"content\"%'", Int::class.java)
        assertThat(stored).isEqualTo(0)
        assertThat(get("/api/projects/$pid/docs/history").json()[0]["state"].asText()).isEqualTo("ok")

        val v1 = list[1]["id"].asText()
        assertThat(get("/api/projects/$pid/docs/history/versions/$v1").json()["content"].asText()).isEqualTo("# Scores\n")
        val diff = get("/api/projects/$pid/docs/history/diff?path=$path&from=$v1&to=file").json()["diff"].asText()
        assertThat(diff).contains("-# Scores\n").contains("+# Scores v2\n")
    }

    @Test
    fun `two flows that wrote the same spec make a conflict, and restoring the other version ends it`() {
        val (pid, root) = newProject("history-conflict", mapOf(".keel/config.yml" to "layout: keel\n"))
        Files.createDirectories(root.resolve(".keel/specs"))
        Files.writeString(root.resolve(path), "# Scores from the folder\n")
        send(version(pid, "t-a", "# Scores from the worktree\n"))
        assertThat(get("/api/projects/$pid/docs/history").json()[0]["state"].asText()).isEqualTo("changed")
        send(version(pid, "t-b", "# Scores from the folder\n"))
        val f = get("/api/projects/$pid/docs/history").json()[0]
        assertThat(f["state"].asText()).isEqualTo("conflict")
        val versions = get("/api/projects/$pid/docs/history/versions?path=$path").json()
        assertThat(f["conflict"].map { it.asText() }).containsExactly(versions[0]["id"].asText(), versions[1]["id"].asText())
        post("/api/projects/$pid/docs/history/restore", mapOf("version_id" to f["conflict"][1].asText())).andExpect(status().isOk)
        assertThat(Files.readString(root.resolve(path))).contains("worktree")
        val after = get("/api/projects/$pid/docs/history").json()[0]
        assertThat(after["state"].asText()).isEqualTo("ok")
        assertThat(after.has("conflict") && !after["conflict"].isNull).isFalse()
    }

    @Test
    fun `the same flow writing twice is not a conflict`() {
        val (pid, root) = newProject("history-no-conflict", mapOf(".keel/config.yml" to "layout: keel\n"))
        Files.createDirectories(root.resolve(".keel/specs"))
        Files.writeString(root.resolve(path), "# Two\n")
        send(version(pid, "t-a", "# One\n"), version(pid, "t-a", "# Two\n"))
        assertThat(get("/api/projects/$pid/docs/history").json()[0]["state"].asText()).isEqualTo("ok")
    }

    @Test
    fun `a deleted spec is missing and comes back with restore`() {
        val (pid, root) = newProject("history-restore", mapOf(".keel/config.yml" to "layout: keel\n"))
        send(version(pid, "t-2", "# Scores\n- **AC-1** [API] saved\n"))
        assertThat(get("/api/projects/$pid/docs/history").json()[0]["state"].asText()).isEqualTo("missing")
        val id = get("/api/projects/$pid/docs/history/versions?path=$path").json()[0]["id"].asText()
        val r = post("/api/projects/$pid/docs/history/restore", mapOf("version_id" to id)).andExpect(status().isOk).json()
        assertThat(r["by_kind"].asText()).isEqualTo("user")
        assertThat(Files.readString(root.resolve(path))).contains("AC-1")
        assertThat(get("/api/projects/$pid/docs/history").json()[0]["state"].asText()).isEqualTo("ok")
    }

    @Test
    fun `a spec that cannot be read while the list is built gives a state, never an empty answer`() {
        // On Docker Desktop's file sharing a just-deleted file can still look present while reading it fails; an
        // unreadable file fails the same read on every machine.
        val (pid, root) = newProject("history-unreadable", mapOf(".keel/config.yml" to "layout: keel\n"))
        Files.createDirectories(root.resolve(".keel/specs"))
        Files.writeString(root.resolve(path), "# Scores\n")
        send(version(pid, "t-5", "# Scores\n"))
        val f = root.resolve(path).toFile()
        f.setReadable(false)
        try {
            val res = get("/api/projects/$pid/docs/history").andExpect(status().isOk).andReturn().response.contentAsString
            assertThat(res).isNotBlank()
            assertThat(mapper.readTree(res)[0]["state"].asText()).isEqualTo("unreadable")
        } finally {
            f.setReadable(true)
        }
    }

    @Test
    fun `a file error on the server is a 500 with words, not an empty 200`() {
        val r = keel.api.common.ErrorAdvice().other(java.nio.file.NoSuchFileException("/p/.keel/specs/x.md"))
        assertThat(r).isNotNull()
        assertThat(r!!.statusCode.value()).isEqualTo(500)
    }

    @Test
    fun `the diff reads only keel's own files with a history, never any other file`() {
        val (pid, root) = newProject("history-diff-paths", mapOf(".keel/config.yml" to "layout: keel\n", ".env" to "TOKEN=x\n"))
        send(version(pid, "t-6", "# Scores\n"))
        for (p in listOf("/etc/passwd", "../../etc/passwd", ".env", ".keel/specs/../../.env", "README.md")) {
            get("/api/projects/$pid/docs/history/diff?path=$p&from=file&to=file").andExpect(status().isBadRequest)
        }
        Files.createDirectories(root.resolve(".keel/specs"))
        Files.writeString(root.resolve(path), "# Scores\n")
        get("/api/projects/$pid/docs/history/diff?path=$path&from=file&to=file").andExpect(status().isOk)
    }

    @Test
    fun `restore never writes through a symlink, nor a version stored with masked lines`(@Autowired jdbc: JdbcTemplate) {
        val (pid, root) = newProject("history-symlink", mapOf(".keel/config.yml" to "layout: keel\n"))
        val outside = Files.createTempFile("keel-outside", ".txt")
        Files.writeString(outside, "keep me\n")
        Files.createDirectories(root.resolve(".keel/specs"))
        Files.createSymbolicLink(root.resolve(path), outside)
        send(version(pid, "t-7", "# Scores\n"))
        val id = get("/api/projects/$pid/docs/history/versions?path=$path").json()[0]["id"].asText()
        post("/api/projects/$pid/docs/history/restore", mapOf("version_id" to id)).andExpect(status().isBadRequest)
        assertThat(Files.readString(outside)).isEqualTo("keep me\n")

        Files.delete(root.resolve(path))
        jdbc.update("UPDATE doc_versions SET masked = 1 WHERE id = ?", id)
        post("/api/projects/$pid/docs/history/restore", mapOf("version_id" to id)).andExpect(status().isConflict)
    }

    @Test
    fun `a repeated keel_applied is one row, and a huge diff is not drawn`() {
        val (pid, _) = newProject("history-dedupe", mapOf(".keel/config.yml" to "layout: keel\n"))
        val applied = mapOf("type" to "keel.applied", "thread_id" to "t-8", "project_id" to pid, "at" to "2026-10-14T10:01:00Z",
            "data" to mapOf("kind" to "commit", "sha" to "abc", "subject" to "s", "files" to listOf(path)))
        send(applied, applied)
        assertThat(get("/api/threads/t-8/applied").json()["items"]).hasSize(1)
        val big = (1..2500).joinToString("\n") { "line $it" }
        assertThat(DocHistoryService.unified(big, big + "\nmore", "x.md")).contains("too big to compare")
    }

    @Test
    fun `restore only writes keel's own files`(@Autowired jdbc: JdbcTemplate) {
        val (pid, _) = newProject("history-outside", mapOf(".keel/config.yml" to "layout: keel\n"))
        jdbc.update("INSERT INTO doc_versions(id, project_id, path, kind, version, sha256, content, size, by_kind, at) VALUES " +
            "('dv-bad', ?, 'src/Main.kt', 'spec', 1, 'x', 'evil', 4, 'agent', '2026-10-14T10:00:00Z')", pid)
        post("/api/projects/$pid/docs/history/restore", mapOf("version_id" to "dv-bad")).andExpect(status().isBadRequest)
    }

    @Test
    fun `a clarify document has a history and can be restored`() {
        // 0.18.64 the engine records <specs>/clarify documents too: the person edits them while the result gate waits
        val (pid, root) = newProject("history-clarify", mapOf(".keel/config.yml" to "layout: keel\n"))
        val rel = ".keel/specs/clarify/2026-10-28-ranks.md"
        val text = "# Ranks\n- **AC-1** [API] ranked\n"
        send(mapOf("type" to "doc.version", "thread_id" to "t-c", "project_id" to pid, "step" to "result", "at" to "2026-10-28T10:00:00Z",
            "data" to mapOf("path" to rel, "kind" to "spec", "sha256" to DocHistoryService.sha256(text.toByteArray()), "content" to text,
                "size" to text.length, "by_kind" to "user", "by_name" to "edited while the flow waited", "phase" to "spec")))
        val id = get("/api/projects/$pid/docs/history/versions?path=$rel").json()[0]["id"].asText()
        post("/api/projects/$pid/docs/history/restore", mapOf("version_id" to id)).andExpect(status().isOk)
        assertThat(Files.readString(root.resolve(rel))).contains("AC-1")
    }

    @Test
    fun `a commit sets the sha on the newest version and shows in what the flow applied`() {
        val (pid, _) = newProject("history-applied", mapOf(".keel/config.yml" to "layout: keel\n"))
        send(version(pid, "t-3", "# S\n"),
            mapOf("type" to "keel.applied", "thread_id" to "t-3", "project_id" to pid, "at" to "2026-10-14T10:01:00Z",
                "data" to mapOf("kind" to "commit", "sha" to "abc1234", "subject" to "docs: spec", "files" to listOf(path))))
        assertThat(get("/api/projects/$pid/docs/history/versions?path=$path").json()[0]["commit_sha"].asText()).isEqualTo("abc1234")
        val a = get("/api/threads/t-3/applied").json()["items"]
        assertThat(a.map { it["kind"].asText() + ":" + it["ref"].asText() }).containsExactly("commit:abc1234")
        assertThat(a[0]["files"].map { it.asText() }).containsExactly(path)
    }

    @Test
    fun `a started flow records its branch`() {
        val (pid, _) = newProject("history-branch")
        val tid = post("/api/projects/$pid/flows", mapOf("workflow_id" to "feature", "title" to "Scores")).andExpect(status().isOk).json()["thread_id"].asText()
        assertThat(get("/api/threads/$tid/applied").json()["items"].map { it["kind"].asText() }).contains("branch")
    }
}
