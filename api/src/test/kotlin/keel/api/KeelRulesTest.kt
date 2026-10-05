package keel.api

import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import keel.api.repo.ClassifyConfig
import keel.api.repo.KeelRules
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.core.io.ClassPathResource

/** The ported classify + MATRIX must agree with keel v1's own fixture. */
class KeelRulesTest {
    private val mapper = jacksonObjectMapper()
    private val fixture = ClassPathResource("keel/keel_rules.json").inputStream.use { mapper.readTree(it) }
    private val rules = KeelRules(mapper)

    @Test
    fun `classify matches keel v1 for every fixture case`() {
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
