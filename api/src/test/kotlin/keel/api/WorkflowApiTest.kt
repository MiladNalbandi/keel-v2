package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.header
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

class WorkflowApiTest : ApiTest() {

    @Test
    fun `templates come from the engine and project workflows are listed with them`() {
        val (pid, _) = newProject("wf-list")
        val list = get("/api/projects/$pid/workflows").andExpect(status().isOk).json()
        val feature = list.first { it["id"].asText() == "feature" }
        assertThat(feature["source"].asText()).isEqualTo("keel")
        assertThat(feature["steps"].size()).isEqualTo(5)
        assertThat(feature["yaml"].asText()).contains("name:")
    }

    @Test
    fun `locked steps cannot be removed while keel rules are on`() {
        val (pid, _) = newProject("wf-locked")
        val wf = post("/api/projects/$pid/workflows", mapOf("name" to "Hotfix", "from" to "template:feature", "keel_rules" to true))
            .andExpect(status().isOk).json()
        val wid = wf["id"].asText()
        assertThat(wf["version"].asInt()).isEqualTo(1)
        assertThat(wf["based_on"].asText()).isEqualTo("keel/feature")

        val withoutGate = wf["steps"].filter { it["id"].asText() != "g-spec" }
        val refused = put("/api/workflows/$wid", mapOf("steps" to withoutGate)).andExpect(status().isConflict).json()
        assertThat(refused["error"].asText()).contains("spec approval")
        assertThat(refused["hint"].asText()).isEqualTo("Turn keel rules off for this workflow")
        assertThat(get("/api/workflows/$wid").json()["version"].asInt()).isEqualTo(1)

        // Removing an unlocked step is fine.
        val withoutSpec = wf["steps"].filter { it["id"].asText() != "spec" }
        val v2 = put("/api/workflows/$wid", mapOf("steps" to withoutSpec)).andExpect(status().isOk).json()
        assertThat(v2["version"].asInt()).isEqualTo(2)

        // With keel rules off, the gate can go.
        val v3 = put("/api/workflows/$wid", mapOf("keel_rules" to false, "steps" to v2["steps"].filter { it["id"].asText() != "g-spec" }))
            .andExpect(status().isOk).json()
        assertThat(v3["version"].asInt()).isEqualTo(3)
        assertThat(v3["keel_rules"].asBoolean()).isFalse()
        assertThat(v3["steps"].map { it["id"].asText() }).doesNotContain("g-spec")
    }

    @Test
    fun `the engine validates every save`() {
        val (pid, _) = newProject("wf-validate")
        val wid = post("/api/projects/$pid/workflows", mapOf("name" to "Mine", "from" to "blank")).json()["id"].asText()
        val bad = "name: Mine\nkeel_rules: false\nsteps:\n  - { id: INVALID, kind: code, name: nope }\n"
        val e = put("/api/workflows/$wid", mapOf("yaml" to bad)).andExpect(status().isUnprocessableEntity).json()
        assertThat(e["hint"].asText()).contains("INVALID")
        put("/api/workflows/feature", mapOf("name" to "x")).andExpect(status().isConflict)
    }

    @Test
    fun `export then import gives the same workflow`() {
        val (pid, _) = newProject("wf-roundtrip")
        val wf = post("/api/projects/$pid/workflows", mapOf("name" to "Round trip", "from" to "template:feature")).json()
        val wid = wf["id"].asText()

        val res = get("/api/workflows/$wid/export")
            .andExpect(status().isOk)
            .andExpect(header().string("Content-Disposition", "attachment; filename=$wid.workflow.yaml"))
            .andReturn().response
        assertThat(res.contentType).startsWith("text/yaml")
        val yaml = res.contentAsString
        assertThat(yaml).contains("name: Round trip").contains("lock: true")

        val imported = post("/api/projects/$pid/workflows/import", mapOf("yaml" to yaml)).andExpect(status().isOk).json()
        val iw = imported["workflow"]
        assertThat(iw["id"].asText()).isNotEqualTo(wid)
        assertThat(iw["name"].asText()).isEqualTo("Round trip")
        assertThat(iw["keel_rules"].asBoolean()).isEqualTo(wf["keel_rules"].asBoolean())
        assertThat(iw["steps"]).isEqualTo(wf["steps"])

        val review = imported["review"]
        assertThat(review["steps"].asInt()).isEqualTo(5)
        assertThat(review["gates"].asInt()).isEqualTo(1)
        assertThat(review["agents"].map { it.asText() }).containsExactly("explorer", "test-author", "implementer")
        assertThat(review["edits_files"].asBoolean()).isTrue()
        assertThat(review["valid"].asBoolean()).isTrue()
    }

    @Test
    fun `bad imports are refused`() {
        val (pid, _) = newProject("wf-badimport")
        post("/api/projects/$pid/workflows/import", mapOf("yaml" to "just: [a list")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/workflows/import", mapOf("url" to "file:///etc/passwd")).andExpect(status().isBadRequest)
        post("/api/projects/$pid/workflows/import", emptyMap<String, String>()).andExpect(status().isBadRequest)
    }

    @Test
    fun `library items install per project or for all`() {
        val (pid, _) = newProject("wf-library")
        val lib = get("/api/library?project=$pid").json()
        assertThat(lib.map { it["id"].asText() }).containsExactlyInAnyOrder(
            "dependency-upgrade", "flaky-test-fixer", "api-contract-first", "release-notes", "weekly-hunt",
        )
        val notes = lib.first { it["id"].asText() == "release-notes" }
        assertThat(notes["mcp"].map { it.asText() }).contains("github")
        assertThat(notes["steps"].asInt()).isEqualTo(4)
        assertThat(notes["installed"].asBoolean()).isFalse()

        val w = post("/api/projects/$pid/library/release-notes/install", mapOf("scope" to "project")).andExpect(status().isOk).json()
        assertThat(w["source"].asText()).isEqualTo("library")
        assertThat(get("/api/library?project=$pid").json().first { it["id"].asText() == "release-notes" }["installed"].asBoolean()).isTrue()
        // again = same workflow
        val again = post("/api/projects/$pid/library/release-notes/install", mapOf("scope" to "project")).json()
        assertThat(again["id"]).isEqualTo(w["id"])

        val all = post("/api/projects/$pid/library/weekly-hunt/install", mapOf("scope" to "all")).andExpect(status().isOk).json()
        val (other, _) = newProject("wf-library-other")
        assertThat(get("/api/projects/$other/workflows").json().map { it["id"].asText() }).contains(all["id"].asText())
        delete("/api/workflows/${all["id"].asText()}").andExpect(status().isOk)
        delete("/api/workflows/feature").andExpect(status().isConflict)
    }
}
