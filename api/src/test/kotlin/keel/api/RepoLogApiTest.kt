package keel.api

import com.fasterxml.jackson.databind.JsonNode
import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/** v0.15.2 Code › Source control › Log (like JetBrains' Git log): the branch list and the log with its graph, refs and filters. */
class RepoLogApiTest : ApiTest() {

    private var clock = 1_700_000_000L

    /** A commit by `who` one minute after the last one (the log's date order is then known). */
    private fun commit(root: Path, who: String, message: String, file: String? = null, text: String = "$message\n") {
        if (file != null) {
            val f = root.resolve(file)
            Files.createDirectories(f.parent)
            Files.writeString(f, text)
            git(root, "add", "-A")
        }
        clock += 60
        gitEnv(root, env(who), "commit", "-q", "--allow-empty", "-m", message)
    }

    private fun env(who: String) = mapOf(
        "GIT_AUTHOR_NAME" to who, "GIT_AUTHOR_EMAIL" to "${who.lowercase()}@example.com",
        "GIT_AUTHOR_DATE" to "$clock +0000", "GIT_COMMITTER_DATE" to "$clock +0000",
    )

    private fun sha(root: Path, rev: String) = git(root, "rev-parse", rev).trim()

    /**
     * main: first commit · two (Bob) · four (Bob, tags v1 and v2); feat/x (current): three (Ann) · merge main · five (Ann, keel's).
     * origin/main is at two; a review ref is at three (never a badge).
     */
    private fun history(name: String): Pair<String, Path> {
        val (pid, root) = newProject(name)
        commit(root, "Bob", "two", "src/b.txt")
        git(root, "checkout", "-q", "-b", "feat/x")
        commit(root, "Ann", "three", "src/c.txt")
        git(root, "checkout", "-q", "main")
        commit(root, "Bob", "four", "d.txt")
        git(root, "tag", "v1")
        clock += 60
        gitEnv(root, env("Bob"), "tag", "-a", "v2", "-m", "release two")
        git(root, "checkout", "-q", "feat/x")
        clock += 60
        gitEnv(root, env("Ann"), "merge", "-q", "--no-ff", "-m", "merge main", "main")
        commit(root, "Ann", "five\n\nCo-Authored-By: KeelBot <keel.dev.bot@gmail.com>", "src/c.txt", "c2\n")
        git(root, "update-ref", "refs/remotes/origin/main", "main~1")
        git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        git(root, "update-ref", "refs/keel/review/pr-1", "feat/x~2")
        return pid to root
    }

    private fun log(pid: String, vararg params: Pair<String, String>) = mvc.perform(
        MockMvcRequestBuilders.get("/api/projects/$pid/repo/log").apply { params.forEach { (k, v) -> param(k, v) } },
    )

    private fun subjects(r: JsonNode) = r["commits"].map { it["subject"].asText() }

    @Test
    fun `the branch list has the local branches (the current one marked, ahead and behind the base), remote branches and tags`() {
        val (pid, root) = history("log-refs")
        val r = get("/api/projects/$pid/repo/refs").andExpect(status().isOk).json()
        assertThat(r["head"].asText()).isEqualTo("feat/x")
        assertThat(r["base"].asText()).isEqualTo("main")
        assertThat(r["local"].map { it["name"].asText() }).containsExactly("feat/x", "main")
        val feat = r["local"][0]
        assertThat(feat["current"].asBoolean()).isTrue()
        assertThat(feat["ahead"].asInt() to feat["behind"].asInt()).isEqualTo(3 to 0)
        assertThat(feat["sha"].asText()).isEqualTo(sha(root, "feat/x"))
        assertThat(feat["subject"].asText()).isEqualTo("five")
        assertThat(r["local"][1]["current"].asBoolean()).isFalse()
        assertThat(r["local"][1].has("ahead")).isFalse()   // the base itself
        // origin/HEAD is a symbolic ref: not a branch of its own
        assertThat(r["remote"].map { it["name"].asText() }).containsExactly("origin/main")
        assertThat(r["tags"].map { it["name"].asText() }).containsExactly("v2", "v1")
        // an annotated tag points at its commit
        assertThat(r["tags"].map { it["sha"].asText() }.toSet()).containsExactly(sha(root, "main"))
    }

    @Test
    fun `the current branch's log has graph parents, refs, and which commits the base has already`() {
        val (pid, root) = history("log-branch")
        val r = log(pid).andExpect(status().isOk).json()
        assertThat(r["branch"].asText()).isEqualTo("feat/x")
        assertThat(r["head"].asText()).isEqualTo("feat/x")
        assertThat(r["base"].asText()).isEqualTo("main")
        assertThat(r["ahead"].asInt() to r["behind"].asInt()).isEqualTo(3 to 0)
        assertThat(r["has_more"].asBoolean()).isFalse()
        assertThat(subjects(r)).containsExactly("five", "merge main", "four", "three", "two", "first commit")
        assertThat(r["commits"].map { it["in_base"].asBoolean() }).containsExactly(false, false, true, false, true, true)

        val c = r["commits"]
        assertThat(c[0]["sha"].asText()).isEqualTo(sha(root, "feat/x"))
        assertThat(c[0]["parents"].map { it.asText() }).containsExactly(c[1]["sha"].asText())
        assertThat(c[1]["parents"].map { it.asText() }).containsExactly(c[3]["sha"].asText(), c[2]["sha"].asText())
        assertThat(c[5]["parents"].size()).isZero()
        assertThat(c[0]["author"].asText()).isEqualTo("Ann")
        assertThat(c[0]["email"].asText()).isEqualTo("ann@example.com")
        assertThat(c[0]["at"].asText()).startsWith("2023-11-14T")
        assertThat(c.map { it["keel"].asBoolean() }).containsExactly(true, false, false, false, false, false)

        val refs = { i: Int -> c[i]["refs"].map { "${it["kind"].asText()}:${it["name"].asText()}${if (it["current"].asBoolean()) "*" else ""}" } }
        assertThat(refs(0)).containsExactly("local:feat/x*")
        assertThat(refs(2)).containsExactlyInAnyOrder("local:main", "tag:v1", "tag:v2")
        assertThat(refs(4)).containsExactly("remote:origin/main")   // no origin/HEAD
        assertThat(refs(3)).isEmpty()                                // refs/keel/review/pr-1 is not a branch
    }

    @Test
    fun `another branch, a remote branch, a tag and all branches`() {
        val (pid, root) = history("log-others")
        git(root, "checkout", "-q", "-b", "spike", "main")
        commit(root, "Cy", "spike only", "spike.txt")
        git(root, "checkout", "-q", "feat/x")

        val main = log(pid, "branch" to "main").json()
        assertThat(subjects(main)).containsExactly("four", "two", "first commit")
        assertThat(main["commits"].map { it["in_base"].asBoolean() }.toSet()).containsExactly(true)
        assertThat(main["ahead"].asInt() to main["behind"].asInt()).isEqualTo(0 to 0)

        val remote = log(pid, "branch" to "origin/main").json()
        assertThat(remote["branch"].asText()).isEqualTo("origin/main")
        assertThat(subjects(remote)).containsExactly("two", "first commit")
        assertThat(remote["ahead"].asInt() to remote["behind"].asInt()).isEqualTo(0 to 1)

        assertThat(subjects(log(pid, "branch" to "v2").json())).containsExactly("four", "two", "first commit")

        val all = log(pid, "all" to "true").json()
        assertThat(all["branch"].isNull).isTrue()
        assertThat(subjects(all)).containsExactly("spike only", "five", "merge main", "four", "three", "two", "first commit")
        assertThat(all["commits"].filter { !it["in_base"].asBoolean() }.map { it["subject"].asText() })
            .containsExactly("spike only", "five", "merge main", "three")
    }

    @Test
    fun `filters by author and text (fixed strings, any case), by path with the graph kept, and finds a commit by its id`() {
        val (pid, root) = history("log-filters")
        git(root, "checkout", "-q", "-b", "spike", "main")
        commit(root, "Cy", "spike only (a+b)", "spike.txt")
        git(root, "checkout", "-q", "feat/x")

        assertThat(subjects(log(pid, "author" to "ANN").json())).containsExactly("five", "merge main", "three")
        assertThat(subjects(log(pid, "q" to "FOUR").json())).containsExactly("four")
        assertThat(subjects(log(pid, "author" to "bob", "q" to "two").json())).containsExactly("two")
        assertThat(subjects(log(pid, "author" to "ann", "q" to "two").json())).isEmpty()
        // a regex character is only a character
        assertThat(subjects(log(pid, "all" to "true", "q" to "(a+b)").json())).containsExactly("spike only (a+b)")

        val file = log(pid, "path" to "src/c.txt").json()
        assertThat(subjects(file)).containsExactly("five", "three")
        // the parents are rewritten to the shown commits, so the graph stays one line
        assertThat(file["commits"][0]["parents"].map { it.asText() }).containsExactly(file["commits"][1]["sha"].asText())
        assertThat(subjects(log(pid, "path" to "src").json())).containsExactly("five", "three", "two")
        assertThat(subjects(log(pid, "path" to "gone.txt").json())).isEmpty()

        val three = sha(root, "feat/x~2")
        assertThat(subjects(log(pid, "q" to three.take(9)).json())).containsExactly("three")
        // a commit id the shown branch does not have: searched as text (nothing), but all branches have it
        val spike = sha(root, "spike")
        assertThat(subjects(log(pid, "q" to spike).json())).isEmpty()
        assertThat(subjects(log(pid, "all" to "true", "q" to spike.take(7)).json())).containsExactly("spike only (a+b)")
    }

    @Test
    fun `a merge commit shows what it brought into its first parent, its files and their diff`() {
        val (pid, root) = history("log-merge")
        val merge = sha(root, "feat/x~1")
        val view = get("/api/projects/$pid/repo/commit?sha=$merge").andExpect(status().isOk).json()
        assertThat(view["files"].map { it["path"].asText() + " " + it["status"].asText() }).containsExactly("d.txt A")
        val d = get("/api/projects/$pid/repo/diff?path=d.txt&sha=$merge").andExpect(status().isOk).json()
        assertThat(d["diff"].asText()).contains("+four")
        // a plain commit is as before
        val five = get("/api/projects/$pid/repo/commit?sha=${sha(root, "feat/x")}").json()
        assertThat(five["files"].map { it["path"].asText() + " " + it["status"].asText() }).containsExactly("src/c.txt M")
    }

    @Test
    fun `pages with limit and skip`() {
        val (pid, _) = history("log-pages")
        val first = log(pid, "limit" to "2").json()
        assertThat(subjects(first)).containsExactly("five", "merge main")
        assertThat(first["has_more"].asBoolean()).isTrue()
        val second = log(pid, "limit" to "2", "skip" to "2").json()
        assertThat(subjects(second)).containsExactly("four", "three")
        assertThat(second["has_more"].asBoolean()).isTrue()
        val last = log(pid, "limit" to "10", "skip" to "4").json()
        assertThat(subjects(last)).containsExactly("two", "first commit")
        assertThat(last["has_more"].asBoolean()).isFalse()
    }

    @Test
    fun `a branch name is never an option, a range or a revision, and the path stays inside the repo`() {
        val (pid, _) = history("log-refuse")
        log(pid, "branch" to "-p").andExpect(status().isBadRequest)
        log(pid, "branch" to "main..feat/x").andExpect(status().isBadRequest)
        log(pid, "branch" to "HEAD~1").andExpect(status().isBadRequest)
        log(pid, "branch" to "main^{tree}").andExpect(status().isBadRequest)
        log(pid, "branch" to "--all").andExpect(status().isBadRequest)
        log(pid, "branch" to "nope").andExpect(status().isNotFound)
        log(pid, "path" to "../outside").andExpect(status().isForbidden)
        log(pid, "path" to ".git/config").andExpect(status().isForbidden)
        log(pid, "path" to ":(glob)**").andExpect(status().isOk)   // a name, never a pathspec: no file has it
            .also { assertThat(subjects(it.json())).isEmpty() }
        get("/api/projects/nope/repo/log").andExpect(status().isNotFound)
        get("/api/projects/nope/repo/refs").andExpect(status().isNotFound)
    }

    @Test
    fun `without a base branch nothing is marked as already on the base`() {
        val (pid, root) = newProject("log-nobase")
        git(root, "branch", "-m", "main", "trunk")
        commit(root, "Ann", "second", "a.txt")
        val r = log(pid).json()
        assertThat(r["base"].isNull).isTrue()
        assertThat(subjects(r)).containsExactly("second", "first commit")
        assertThat(r["commits"].map { it["in_base"].asBoolean() }.toSet()).containsExactly(false)
        assertThat(get("/api/projects/$pid/repo/refs").json()["local"].map { it["name"].asText() }).containsExactly("trunk")
    }
}
