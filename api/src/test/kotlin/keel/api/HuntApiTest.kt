package keel.api

import keel.api.support.ApiTest
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

/** The hunt backlog routes pass the engine's GET /projects/{p}/hunts[/{run}] and close through for the Hunt page. */
class HuntApiTest : ApiTest() {
    @Test
    fun `hunts list, one run and close go through the engine`() {
        val (pid, _) = newProject("hunt-api")
        val runs = get("/api/projects/$pid/hunts").andExpect(status().isOk).json()
        assertThat(runs[0]["run"].asText()).isEqualTo("2026-10-05-01")
        assertThat(runs[0]["counts"]["proven"].asInt()).isEqualTo(1)

        val one = get("/api/projects/$pid/hunts/2026-10-05-01").andExpect(status().isOk).json()
        assertThat(one["candidates"][0]["id"].asText()).isEqualTo("F-001")
        assertThat(one["report_markdown"].asText()).startsWith("# Bug hunt")
        get("/api/projects/$pid/hunts/nope").andExpect(status().isNotFound)
        get("/api/projects/missing/hunts").andExpect(status().isNotFound)

        post("/api/projects/$pid/hunts/2026-10-05-01/close", """{"id":"F-001","as":"fixed","note":""}""").andExpect(status().isBadRequest)
        post("/api/projects/$pid/hunts/2026-10-05-01/close", """{"id":"F-001","as":"done","note":"x"}""").andExpect(status().isBadRequest)
        val closed = post("/api/projects/$pid/hunts/2026-10-05-01/close", """{"id":"F-001","as":"fixed","note":"PR #3"}""")
            .andExpect(status().isOk).json()
        assertThat(closed["candidates"][0]["close"]["note"].asText()).isEqualTo("PR #3")
        assertThat(engine.lastBody("/projects/$pid/hunts/2026-10-05-01/close")!!["as"].asText()).isEqualTo("fixed")
    }
}

class HuntWorkflowKeysTest {
    @Test
    fun `gate choices, a gate's when and a batch read from the state survive the api's workflow model`() {
        val doc = keel.api.workflows.WorkflowDoc.parse("""
            name: hunt-like
            steps:
              - { id: prove, kind: parallel, name: provers, agent: prover, from: prove_items, batch: "${'$'}data.hunt.prove_concurrency" }
              - { id: sweep_gate, kind: gate, name: candidates, when: { data: hunt.mode, equals: semi }, choices: { prove: prove, stop: end } }
        """.trimIndent())
        val yaml = doc.toYaml()
        listOf("batch: \$data.hunt.prove_concurrency", "data: hunt.mode", "prove: prove", "stop: end").forEach { assertThat(yaml).contains(it) }
        val two = keel.api.workflows.WorkflowDoc.parse("name: n\nsteps:\n  - { id: p, kind: parallel, name: p, agent: a, from: x, batch: 2 }\n")
        assertThat(two.toYaml()).contains("batch: 2")
    }
}
