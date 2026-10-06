package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.header
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import java.nio.file.Files

/** v0.5.1: the Repo page as a small read-only IDE — search (git grep), quick open files, raw, changes, diffs, commits. */
class RepoIdeApiTest : ApiTest() {

    private val files = mapOf(
        "README.md" to "# Scores\nThe score service.\n",
        ".gitignore" to "ignored/\n*.log\n",
        "api/src/main/kotlin/app/Score.kt" to "package app\n\nclass Score(val value: Int)\nfun scoreOf(v: Int) = Score(v)\n// scoreboard is not a Score\nval score = scoreOf(1)\n",
        "api/src/main/kotlin/app/ScoreService.kt" to "package app\n\nclass ScoreService { fun save(s: Score) = s }\n",
        "web/src/score.ts" to "export const score = 1; // SCORE\n",
        "docs/notes.md" to "score notes\n",
        ".env.local" to "SCORE_SECRET=1\n",
        "logo.svg" to "<svg xmlns=\"http://www.w3.org/2000/svg\"><script>alert(1)</script></svg>\n",
    )

    private fun search(pid: String, q: String, vararg params: Pair<String, String>) = mvc.perform(
        MockMvcRequestBuilders.get("/api/projects/$pid/repo/search").param("q", q).apply { params.forEach { (k, v) -> param(k, v) } },
    )

    private fun paths(r: com.fasterxml.jackson.databind.JsonNode) = r["results"].map { it["path"].asText() }

    @Test
    fun `search finds text with line, column and ranges, skips ignored and secret files, finds untracked ones`() {
        val (pid, root) = newProject("ide-search", files)
        Files.createDirectories(root.resolve("ignored"))
        Files.writeString(root.resolve("ignored/score.txt"), "score\n")
        Files.writeString(root.resolve("debug.log"), "score\n")
        Files.writeString(root.resolve("new-score.txt"), "a new score here\n")

        val r = search(pid, "Score").andExpect(status().isOk).json()
        val ps = paths(r)
        // case-insensitive by default
        assertThat(ps).contains("api/src/main/kotlin/app/Score.kt", "web/src/score.ts", "README.md", "new-score.txt")
        assertThat(ps).doesNotContain("ignored/score.txt", "debug.log", ".env.local")
        assertThat(r["truncated"].asBoolean()).isFalse()
        assertThat(r["files"].asInt()).isEqualTo(ps.size)
        val score = r["results"].first { it["path"].asText() == "api/src/main/kotlin/app/Score.kt" }
        val line3 = score["matches"].first { it["line"].asInt() == 3 }
        assertThat(line3["text"].asText()).isEqualTo("class Score(val value: Int)")
        assertThat(line3["column"].asInt()).isEqualTo(7)
        assertThat(line3["length"].asInt()).isEqualTo(5)
        assertThat(line3["ranges"][0].map { it.asInt() }).containsExactly(6, 11)

        // match case
        val cased = search(pid, "SCORE", "case" to "true").json()
        assertThat(paths(cased)).containsExactly("web/src/score.ts")

        // whole word and match case: "scoreboard", "scoreOf" and "Score" do not match
        val word = search(pid, "score", "word" to "true", "case" to "true", "include" to "*.kt").json()
        val lines = word["results"].flatMap { f -> f["matches"].map { f["path"].asText() + ":" + it["line"].asInt() } }
        assertThat(lines).containsExactly("api/src/main/kotlin/app/Score.kt:6")
        assertThat(word["results"][0]["matches"][0]["ranges"].map { r -> r.map { it.asInt() } }).containsExactly(listOf(4, 9))

        // regex
        val re = search(pid, "fun \\w+\\(", "regex" to "true").json()
        assertThat(paths(re)).containsExactly("api/src/main/kotlin/app/Score.kt", "api/src/main/kotlin/app/ScoreService.kt")
        assertThat(re["results"][0]["matches"][0]["text"].asText()).isEqualTo("fun scoreOf(v: Int) = Score(v)")
        search(pid, "fun (", "regex" to "true").andExpect(status().isBadRequest)
        assertThat(search(pid, "fun (", "regex" to "true").json()["error"].asText()).isEqualTo("The regex is not valid")
    }

    @Test
    fun `search include and exclude globs, the result cap, and paths outside the repo are refused`() {
        val (pid, _) = newProject("ide-globs", files)
        assertThat(paths(search(pid, "score", "include" to "*.kt").json()))
            .containsExactlyInAnyOrder("api/src/main/kotlin/app/Score.kt", "api/src/main/kotlin/app/ScoreService.kt")
        assertThat(paths(search(pid, "score", "include" to "web/, docs").json())).containsExactlyInAnyOrder("web/src/score.ts", "docs/notes.md")
        val ex = paths(search(pid, "score", "exclude" to "*.kt, *.md").json())
        assertThat(ex).contains("web/src/score.ts").noneMatch { it.endsWith(".kt") || it.endsWith(".md") }

        val capped = search(pid, "score", "max" to "2").json()
        assertThat(capped["matches"].asInt()).isEqualTo(2)
        assertThat(capped["truncated"].asBoolean()).isTrue()

        search(pid, "score", "include" to "../outside").andExpect(status().isForbidden)
        search(pid, "score", "include" to "api/../../x").andExpect(status().isForbidden)
        search(pid, "score", "exclude" to "/etc").andExpect(status().isForbidden)
        search(pid, "score", "include" to ":(top)x").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/search?q=").andExpect(status().isBadRequest)
        get("/api/projects/nope/repo/search?q=x").andExpect(status().isNotFound)
    }

    @Test
    fun `the file list has tracked and untracked files, not ignored, deleted or secret ones`() {
        val (pid, root) = newProject("ide-files", files)
        Files.writeString(root.resolve("untracked.txt"), "u\n")
        Files.writeString(root.resolve("debug.log"), "x\n")
        Files.delete(root.resolve("docs/notes.md"))
        val list = get("/api/projects/$pid/repo/files").andExpect(status().isOk).json()
        val names = list["files"].map { it.asText() }
        assertThat(names).contains("README.md", "api/src/main/kotlin/app/Score.kt", "untracked.txt", "logo.svg")
        assertThat(names).doesNotContain("debug.log", "docs/notes.md", ".env.local")
        assertThat(list["truncated"].asBoolean()).isFalse()
    }

    @Test
    fun `raw serves the file with a sandbox CSP, the tree loads one folder lazily`() {
        val (pid, root) = newProject("ide-raw", files)
        val text = get("/api/projects/$pid/repo/raw?path=api/src/main/kotlin/app/Score.kt").andExpect(status().isOk)
            .andExpect(header().string("Content-Security-Policy", org.hamcrest.Matchers.startsWith("sandbox")))
            .andExpect(header().string("X-Content-Type-Options", "nosniff"))
            .andReturn().response
        assertThat(text.contentType).startsWith("text/plain")
        assertThat(text.getContentAsString(Charsets.UTF_8)).isEqualTo(files["api/src/main/kotlin/app/Score.kt"])
        val svg = get("/api/projects/$pid/repo/raw?path=logo.svg").andExpect(status().isOk).andReturn().response
        assertThat(svg.contentType).isEqualTo("image/svg+xml")
        assertThat(svg.getHeader("Content-Security-Policy")).contains("sandbox").contains("default-src 'none'")
        Files.write(root.resolve("bin.dat"), byteArrayOf(1, 0, 2, 0))
        assertThat(get("/api/projects/$pid/repo/raw?path=bin.dat").andReturn().response.contentType).isEqualTo("application/octet-stream")
        assertThat(get("/api/projects/$pid/repo/file?path=bin.dat").json()["binary"].asBoolean()).isTrue()

        get("/api/projects/$pid/repo/raw?path=../x").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/raw?path=.env.local").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/raw?path=.git/config").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/raw?path=nope.txt").andExpect(status().isNotFound)

        val sub = get("/api/projects/$pid/repo/tree?dir=api/src&depth=1").andExpect(status().isOk).json()
        assertThat(sub.map { it["path"].asText() }).containsExactly("api/src/main")
        assertThat(sub[0]["depth"].asInt()).isEqualTo(3)
        val deep = get("/api/projects/$pid/repo/tree?dir=api/src&depth=4").json()
        assertThat(deep.map { it["path"].asText() }).contains("api/src/main/kotlin/app/Score.kt")
        get("/api/projects/$pid/repo/tree?dir=../").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/tree?dir=README.md").andExpect(status().isNotFound)
    }

    @Test
    fun `changes, diffs against HEAD and base, commits of the branch with their files`() {
        val (pid, root) = newProject("ide-diff", files)
        git(root, "checkout", "-q", "-b", "feat/scores")
        Files.writeString(root.resolve("api/src/main/kotlin/app/Score.kt"), files["api/src/main/kotlin/app/Score.kt"]!!.replace("Int", "Long"))
        gitEnv(root, emptyMap(), "-c", "user.name=keelbot", "commit", "-q", "-am", "feat(AC-002): scores are Long")
        Files.writeString(root.resolve("README.md"), "# Scores v2\nThe score service.\n")
        Files.writeString(root.resolve("web/src/score.ts"), "export const score = 2;\n")
        git(root, "add", "web/src/score.ts")
        Files.writeString(root.resolve("fresh.txt"), "new\nfile\n")
        Files.delete(root.resolve("docs/notes.md"))

        val ch = get("/api/projects/$pid/repo/changes").andExpect(status().isOk).json().associateBy { it["path"].asText() }
        assertThat(ch["README.md"]!!["unstaged"].asText()).isEqualTo("M")
        assertThat(ch["README.md"]!!.has("staged")).isFalse()
        assertThat(ch["web/src/score.ts"]!!["staged"].asText()).isEqualTo("M")
        assertThat(ch["fresh.txt"]!!["untracked"].asBoolean()).isTrue()
        assertThat(ch["docs/notes.md"]!!["unstaged"].asText()).isEqualTo("D")

        val head = get("/api/projects/$pid/repo/diff?path=README.md&against=head").andExpect(status().isOk).json()
        assertThat(head["ref"].asText()).isEqualTo("HEAD")
        assertThat(head["diff"].asText()).contains("-# Scores", "+# Scores v2")
        val fresh = get("/api/projects/$pid/repo/diff?path=fresh.txt").json()
        assertThat(fresh["diff"].asText()).contains("+new", "+file")
        val gone = get("/api/projects/$pid/repo/diff?path=docs/notes.md").json()
        assertThat(gone["diff"].asText()).contains("-score notes")

        // against the base: the committed change of this branch is in it, too
        val base = get("/api/projects/$pid/repo/diff?path=api/src/main/kotlin/app/Score.kt&against=base").json()
        assertThat(base["ref"].asText()).startsWith("main (")
        assertThat(base["diff"].asText()).contains("-class Score(val value: Int)", "+class Score(val value: Long)")
        assertThat(get("/api/projects/$pid/repo/diff?path=api/src/main/kotlin/app/Score.kt&against=head").json()["diff"].asText()).isEmpty()
        get("/api/projects/$pid/repo/diff?path=../x").andExpect(status().isForbidden)
        get("/api/projects/$pid/repo/diff?path=README.md&against=yesterday").andExpect(status().isBadRequest)

        val branch = get("/api/projects/$pid/repo/commits?range=branch").json()
        assertThat(branch.map { it["message"].asText() }).containsExactly("feat(AC-002): scores are Long")
        assertThat(branch[0]["keel"].asBoolean()).isTrue()
        val all = get("/api/projects/$pid/repo/commits").json()
        assertThat(all.map { it["keel"].asBoolean() }).containsExactly(true, false)

        val sha = branch[0]["sha"].asText()
        val c = get("/api/projects/$pid/repo/commit?sha=$sha").andExpect(status().isOk).json()
        assertThat(c["files"].map { it["path"].asText() + " " + it["status"].asText() }).containsExactly("api/src/main/kotlin/app/Score.kt M")
        assertThat(c["keel"].asBoolean()).isTrue()
        val cd = get("/api/projects/$pid/repo/diff?path=api/src/main/kotlin/app/Score.kt&sha=$sha").json()
        assertThat(cd["diff"].asText()).contains("+class Score(val value: Long)")
        get("/api/projects/$pid/repo/commit?sha=not-a-sha;rm").andExpect(status().isBadRequest)
        get("/api/projects/$pid/repo/commit?sha=deadbeef").andExpect(status().isNotFound)

        // the file view names the AC that changed it on this branch and the rule that applies now
        val fv = get("/api/projects/$pid/repo/file?path=api/src/main/kotlin/app/Score.kt").json()
        assertThat(fv["ac"].asText()).isEqualTo("AC-002")
        assertThat(fv["phase"].asText()).isEqualTo("none")
        assertThat(fv["verdict"].asText()).isEqualTo("allow")
        assertThat(fv["binary"].asBoolean()).isFalse()
        assertThat(fv["modified"].asLong()).isGreaterThan(0)
    }
}
