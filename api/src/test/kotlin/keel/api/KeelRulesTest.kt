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
}
