package keel.api.helper

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** v0.15.2 KeelBot's chat folders: the engine keeps them; the api lets a project see and change only its own. With the
 *  KeelBot plugin (plugins/keelbot/api, keel.api.helper); it runs with keel's test support (ApiTest, StubEngine). */
class HelperFoldersApiTest : ApiTest() {
    private fun patch(url: String, body: Any) = mvc.perform(
        MockMvcRequestBuilders.patch(url).contentType(MediaType.APPLICATION_JSON).content(mapper.writeValueAsString(body)))

    /** The engine's folders, like it keeps them: GET lists one project's (?project=), POST makes one. */
    private val folders = mutableListOf<MutableMap<String, Any?>>()

    private fun serveFolders() {
        engine.extraRoutes["/helper/folders"] = { body ->
            if (body == null) {
                val project = engine.calls.last().query.orEmpty().removePrefix("project=")
                200 to folders.filter { it["project"] == project }
            } else {
                val f = mutableMapOf<String, Any?>("id" to "hf_${folders.size + 1}", "project" to body["project_id"].asText(),
                    "name" to body["name"].asText(), "chats" to 0)
                folders += f
                200 to f
            }
        }
        for (id in listOf("hf_1", "hf_2")) {
            engine.extraRoutes["/helper/folders/$id"] = { body ->
                val f = folders.first { it["id"] == id }
                if (body == null) { folders.remove(f); 200 to mapOf("ok" to true, "moved" to 0) }
                else { f["name"] = body["name"].asText(); 200 to f }
            }
        }
    }

    @AfterEach
    fun forget() {
        listOf("/helper/folders", "/helper/folders/hf_1", "/helper/folders/hf_2").forEach { engine.extraRoutes.remove(it) }
    }

    @Test
    fun `a project makes, renames and deletes its own folders, and moves its chats into them`() {
        serveFolders()
        val (pid, _) = newProject("helper-folders")
        val (other, _) = newProject("helper-folders-other")

        val made = post("/api/projects/$pid/helper/folders", mapOf("name" to "  Payments ")).andExpect(status().isOk).json()
        assertThat(made["id"].asText()).isEqualTo("hf_1")
        assertThat(engine.lastBody("/helper/folders")!!["project_id"].asText()).isEqualTo(pid)
        assertThat(engine.lastBody("/helper/folders")!!["name"].asText()).isEqualTo("Payments")
        post("/api/projects/$pid/helper/folders", mapOf("name" to "  ")).andExpect(status().isBadRequest)
        post("/api/projects/$other/helper/folders", mapOf("name" to "Theirs")).andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/helper/folders").json().map { it["name"].asText() }).containsExactly("Payments")

        patch("/api/projects/$pid/helper/folders/hf_1", mapOf("name" to "Billing")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/folders/hf_1")!!["name"].asText()).isEqualTo("Billing")
        patch("/api/projects/$pid/helper/folders/hf_1", mapOf("name" to "")).andExpect(status().isBadRequest)

        // a chat goes into a folder of its own project, and out of it with ""
        val sid = post("/api/projects/$pid/helper/sessions").json()["id"].asText()
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("folder" to "hf_1")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!["folder"].asText()).isEqualTo("hf_1")
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("folder" to "")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!["folder"].asText()).isEqualTo("")
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("title" to "Ranks")).andExpect(status().isOk)
        assertThat(engine.lastBody("/helper/sessions/$sid")!!.has("folder")).isFalse()

        // another project's folder is not found here: not to rename, delete or move a chat into
        val calls = engine.calls.size
        patch("/api/projects/$pid/helper/folders/hf_2", mapOf("name" to "Mine now")).andExpect(status().isNotFound)
        delete("/api/projects/$pid/helper/folders/hf_2").andExpect(status().isNotFound)
        patch("/api/projects/$pid/helper/sessions/$sid", mapOf("folder" to "hf_2")).andExpect(status().isNotFound)
        assertThat(engine.calls.drop(calls).none { it.method != "GET" }).isTrue()

        delete("/api/projects/$pid/helper/folders/hf_1").andExpect(status().isOk)
        assertThat(engine.calls.last { it.path == "/helper/folders/hf_1" }.method).isEqualTo("DELETE")
        assertThat(get("/api/projects/$pid/helper/folders").json().size()).isEqualTo(0)
        get("/api/projects/nope/helper/folders").andExpect(status().isNotFound)
    }
}
