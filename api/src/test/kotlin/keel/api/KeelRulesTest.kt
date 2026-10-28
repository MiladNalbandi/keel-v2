package keel.api

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.repo.ClassifyConfig
import keel.api.repo.KeelRules
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.core.io.ClassPathResource

/** classify + MATRIX must agree with the shared rules file (the engine reads the same bytes). */
class KeelRulesTest {
    private val mapper = jacksonObjectMapper()
    private val fixture = ClassPathResource("keel/keel_rules.json").inputStream.use { mapper.readTree(it) }
    private val rules = KeelRules(mapper)

    @Test
    fun `classify matches the shared rules file for every case`() {
        @Suppress("UNCHECKED_CAST")
        val cfg = ClassifyConfig.from(mapper.convertValue(fixture["classify_cfg"], Map::class.java) as Map<String, Any?>)
        fixture["classify"].fields().forEach { (path, bucket) ->
            assertThat(KeelRules.classify(cfg, path)).describedAs(path).isEqualTo(bucket.asText())
        }
    }

    @Test
    fun `secret files come from the shared list the engine's guard reads`() {
        val list = fixture["SECRET_FILES"]
        list["names"].forEach { assertThat(keel.api.repo.RepoService.isSecret(it.asText())).describedAs(it.asText()).isTrue() }
        list["readable"].forEach { assertThat(keel.api.repo.RepoService.isSecret(it.asText())).describedAs(it.asText()).isFalse() }
        assertThat(keel.api.repo.RepoService.isSecret("deploy/server.PEM")).isTrue()
        assertThat(keel.api.repo.RepoService.isSecret(".pgpass")).isTrue()
        assertThat(keel.api.repo.RepoService.isSecret("id_ed25519")).isTrue()
        assertThat(keel.api.repo.RepoService.isSecret("README.md")).isFalse()
    }

    @Test
    fun `matrix verdicts`() {
        val cfg = ClassifyConfig()
        assertThat(rules.frozen("red", cfg, "apps/api/src/main/kotlin/A.kt")).isTrue()
        assertThat(rules.frozen("red", cfg, "apps/api/src/test/kotlin/ATest.kt")).isFalse()
        assertThat(rules.frozen("green", cfg, "apps/api/src/test/kotlin/ATest.kt")).isTrue()
        assertThat(rules.frozen("green", cfg, "apps/api/src/main/kotlin/A.kt")).isFalse()
        assertThat(rules.verdict("green", "migration")).isEqualTo("new-only")
        assertThat(rules.frozen("none", cfg, "anything")).isFalse()
        assertThat(rules.frozen("spec", cfg, "specs/a.md")).isFalse()
        assertThat(rules.frozen("spec", cfg, ".env")).isTrue()
        assertThat(rules.matrix.keys).contains("hunt-triage", "bug-fix", "coverage-fix")
    }

    @Test
    fun `without a web layer a server-rendered page is the backend's, with one it stays the web's`() {
        val page = "src/main/resources/templates/owners/ownerDetails.html"
        val noWeb = ClassifyConfig(webLayer = false)
        assertThat(KeelRules.classify(noWeb, page)).isEqualTo("api-main")
        assertThat(KeelRules.classify(noWeb, "src/main/resources/static/resources/css/petclinic.css")).isEqualTo("api-main")
        assertThat(KeelRules.classify(noWeb, "src/main/resources/templates/fragments/layout.jte")).isEqualTo("api-main")
        assertThat(KeelRules.classify(noWeb, "src/test/resources/templates/sample.html")).isEqualTo("api-test")
        assertThat(rules.frozen("green", noWeb, page)).isFalse()
        assertThat(rules.frozen("red", noWeb, page)).isTrue()
        val web = ClassifyConfig()
        assertThat(KeelRules.classify(web, page)).isEqualTo("web-src")
        assertThat(KeelRules.classify(web, "src/main/resources/templates/fragments/layout.jte")).isEqualTo("other")
    }
}
