package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files
import java.nio.file.Path

/** v0.15.7 Add projects from your folders: find the repos in KEEL_FOLDERS, walk the folders, add many at once. */
class FoldersApiTest : ApiTest() {
    private val main: Path = foldersDir.resolve("main")
    private val extra: Path = foldersDir.resolve("extra")

    private fun repo(p: Path): Path {
        Files.createDirectories(p)
        if (!Files.exists(p.resolve(".git"))) git(p, "init", "-q", "-b", "main")
        return p
    }

    /**
     * main/                      a folder of repos
     *   app/  (repo, with app/sub a repo inside it)
     *   team/api, team/web       (repos, depth 2)
     *   a/b/deep                 (repo, depth 3)   a/b/c/too-deep (depth 4: not found)
     *   node_modules/pkg, build/gen, .hidden/repo  (skipped)
     *   loop -> main (a symlink loop), escape -> a repo outside every root
     * extra/                     itself a repo
     */
    @BeforeEach
    fun tree() {
        if (Files.exists(main.resolve("app"))) return
        repo(main.resolve("app"))
        repo(main.resolve("app/sub"))
        repo(main.resolve("team/api"))
        repo(main.resolve("team/web"))
        repo(main.resolve("a/b/deep"))
        repo(main.resolve("a/b/c/too-deep"))
        repo(main.resolve("node_modules/pkg"))
        repo(main.resolve("build/gen"))
        repo(main.resolve(".hidden/repo"))
        Files.createSymbolicLink(main.resolve("loop"), main)
        Files.createSymbolicLink(main.resolve("escape"), repo(Files.createTempDirectory("keel-outside").resolve("secret")))
        repo(extra)
    }

    private fun browse(path: String) = mvc.perform(MockMvcRequestBuilders.get("/api/folders/browse").param("path", path))

    @Test
    fun `finds the repos up to three folders deep and skips build output, hidden folders and symlinks that loop or leave`() {
        val res = get("/api/folders").andExpect(status().isOk).json()
        assertThat(res["roots"].map { it["path"].asText() to it["exists"].asBoolean() }).containsExactly(
            main.toString() to true, extra.toString() to true, foldersDir.resolve("missing").toString() to false)
        val repos = res["repos"].map { it["path"].asText() }
        assertThat(repos).containsExactly(
            main.resolve("a/b/deep").toString(), main.resolve("app").toString(), main.resolve("team/api").toString(),
            main.resolve("team/web").toString(), extra.toString())
        val app = res["repos"].first { it["path"].asText() == main.resolve("app").toString() }
        assertThat(app["name"].asText()).isEqualTo("app")
        assertThat(app["root"].asText()).isEqualTo(main.toString())
        assertThat(app.has("project_id")).isTrue()
        assertThat(res["truncated"].asBoolean()).isFalse()
    }

    @Test
    fun `walks the folders inside the roots and refuses anything outside them with 403`() {
        val top = browse(main.toString()).andExpect(status().isOk).json()
        assertThat(top["root"].asText()).isEqualTo(main.toString())
        assertThat(top["parent"].isNull).isTrue()
        // no hidden folder, no node_modules or build, no symlink that leaves the roots; the loop stays inside, so it shows
        assertThat(top["dirs"].map { it["name"].asText() }).containsExactly("a", "app", "loop", "team")
        assertThat(top["dirs"].first { it["name"].asText() == "app" }["repo"].asBoolean()).isTrue()
        assertThat(top["dirs"].first { it["name"].asText() == "team" }["repo"].asBoolean()).isFalse()

        val team = browse(main.resolve("team").toString()).andExpect(status().isOk).json()
        assertThat(team["parent"].asText()).isEqualTo(main.toString())
        assertThat(team["dirs"].map { it["name"].asText() to it["repo"].asBoolean() }).containsExactly("api" to true, "web" to true)

        // no path: the first root that is there
        assertThat(get("/api/folders/browse").andExpect(status().isOk).json()["path"].asText()).isEqualTo(main.toString())
        // keel's data folder may be walked too (the demo project lives there)
        browse(dataDir.toString()).andExpect(status().isOk)

        browse("/etc").andExpect(status().isForbidden)
        browse(foldersDir.toString()).andExpect(status().isForbidden)
        browse("$main/../extra").andExpect(status().isForbidden)
        browse("$main/team/../../..").andExpect(status().isForbidden)
        browse(main.resolve("escape").toString()).andExpect(status().isForbidden)
        browse(main.resolve("nothing-here").toString()).andExpect(status().isNotFound)
    }

    @Test
    fun `adds many repos at once and skips one that is a project already, with why`() {
        val web = main.resolve("team/web").toString()
        val deepRoot = main.resolve("a/b/deep").toString()
        val first = post("/api/projects", mapOf("root" to web)).andExpect(status().isOk).json()["id"].asText()

        val res = post("/api/projects/bulk", mapOf(
            "roots" to listOf(web, deepRoot, main.resolve("no-such-folder").toString(), deepRoot),
            "names" to mapOf(deepRoot to "Deep one"),
        )).andExpect(status().isOk).json()
        assertThat(res["added"].map { it["name"].asText() to it["root"].asText() }).containsExactly("Deep one" to deepRoot)
        val deep = res["added"][0]["id"].asText()
        assertThat(res["skipped"].map { it["root"].asText() }).containsExactly(web, main.resolve("no-such-folder").toString())
        assertThat(res["skipped"][0]["why"].asText()).isEqualTo("already a project ($first)")
        assertThat(res["skipped"][1]["why"].asText()).contains("There is no folder")

        // the list marks both as projects now
        val repos = get("/api/folders").json()["repos"]
        assertThat(repos.first { it["path"].asText() == web }["project_id"].asText()).isEqualTo(first)
        assertThat(repos.first { it["path"].asText() == deepRoot }["project_id"].asText()).isEqualTo(deep)
        assertThat(get("/api/projects").json().map { it["id"].asText() }).contains(first, deep)

        post("/api/projects/bulk", mapOf("roots" to emptyList<String>())).andExpect(status().isBadRequest)
    }
}
