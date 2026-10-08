package keel.api.map

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status
import java.nio.file.Files

/** The Map plugin's api part: the map routes proxy the engine (moved from RepoApiTest and IndexApiTest). */
class MapApiTest : ApiTest() {

    @Test
    fun `the engine builds the map for the project's folder and keeps it`() {
        val (pid, _) = newProject("map-knowledge")
        assertThat(get("/api/projects/$pid/map").json()["missing"].asText()).contains("No map yet")
        // the engine builds the map (no keel v1 binary involved) and keeps it
        val built = post("/api/projects/$pid/map/rebuild").andExpect(status().isOk).json()
        assertThat(built["levels"]["er"]).isNotNull()
        assertThat(engine.lastBody("/projects/$pid/map")!!["root"].asText()).endsWith("map-knowledge")
        assertThat(get("/api/projects/$pid/map").json()["sha"].asText()).isEqualTo("abc1234")
    }

    @Test
    fun `the map is the engine's, a map file keel v1 left in the project is not read`() {
        val (pid, root) = newProject("index-oldmap")
        Files.createDirectories(root.resolve(".keel"))
        Files.writeString(root.resolve(".keel/map.json"), """{"sha":"old1234","at":"2026-01-01T00:00:00Z","levels":{}}""")
        assertThat(get("/api/projects/$pid/map").json()["missing"].asText()).contains("No map yet")
        post("/api/projects/$pid/map/rebuild").andExpect(status().isOk)
        assertThat(get("/api/projects/$pid/map").json()["sha"].asText()).isEqualTo("abc1234")
    }

    @Test
    fun `an unknown project is not found`() {
        get("/api/projects/nope/map").andExpect(status().isNotFound)
        post("/api/projects/nope/map/rebuild").andExpect(status().isNotFound)
    }
}
