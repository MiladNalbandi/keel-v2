package keel.api.repo

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import keel.api.common.Yaml
import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path

/** The part of a keel v1 config that file classification needs. */
data class ClassifyConfig(
    val backendDir: String = "apps/api",
    val migrations: String = "src/main/resources/db/migration",
    val frontendDir: String = "apps/web",
    val contractFile: String = "contracts/openapi.yaml",
    val e2eDir: String = "e2e",
    val smokeDir: String = "smoke",
    val specsDir: String = "specs",
    val protected: List<String> = listOf("**/.env", "**/.env.*", "!**/.env.example"),
    val generated: List<String> = listOf("**/generated/**", "**/build/generated/**"),
) {
    companion object {
        /** Reads `<root>/.keel/config.yml` over keel v1's defaults. */
        fun load(root: Path): ClassifyConfig {
            val f = root.resolve(".keel/config.yml")
            val user = if (Files.isRegularFile(f)) Yaml.readMap(Files.readString(f)) ?: emptyMap() else emptyMap()
            return from(user)
        }

        fun from(m: Map<String, Any?>): ClassifyConfig {
            val d = ClassifyConfig()
            fun sec(name: String): Map<*, *> = m[name] as? Map<*, *> ?: emptyMap<String, Any>()
            fun s(sec: Map<*, *>, key: String, def: String) = sec[key]?.toString() ?: def
            val guards = sec("guards")
            @Suppress("UNCHECKED_CAST")
            return ClassifyConfig(
                backendDir = s(sec("backend"), "dir", d.backendDir),
                migrations = s(sec("backend"), "migrations", d.migrations),
                frontendDir = s(sec("frontend"), "dir", d.frontendDir),
                contractFile = s(sec("contract"), "file", d.contractFile),
                e2eDir = s(sec("e2e"), "dir", d.e2eDir),
                smokeDir = s(sec("smoke"), "dir", d.smokeDir),
                specsDir = s(sec("specs"), "dir", d.specsDir),
                protected = (guards["protected"] as? List<String>) ?: d.protected,
                generated = (guards["generated"] as? List<String>) ?: d.generated,
            )
        }
    }
}

/**
 * A small port of keel v1's `lib/guards.js` classify + MATRIX. The matrix itself comes from the
 * shared fixture (`keel/keel_v1_rules.json`), the same file the engine's rules are tested against.
 */
@Component
class KeelRules(mapper: ObjectMapper) {
    private val fixture: JsonNode = ClassPathResource("keel/keel_v1_rules.json").inputStream.use { mapper.readTree(it) }

    val matrix: Map<String, Map<String, String>> = fixture.get("MATRIX").fields().asSequence().associate { (phase, rules) ->
        phase to rules.fields().asSequence().associate { (bucket, verdict) -> bucket to verdict.asText() }
    }

    /** Verdict for a bucket in a phase: allow | deny | new-only | delete-only | ... */
    fun verdict(phase: String, bucket: String): String {
        val rules = matrix[phase] ?: return "allow"
        return rules[bucket] ?: rules["*"] ?: "allow"
    }

    /** True when the phase does not let agents edit this path at all. */
    fun frozen(phase: String, cfg: ClassifyConfig, rel: String): Boolean = verdict(phase, classify(cfg, rel)) == "deny"

    companion object {
        private val TEST_PATTERNS = listOf(
            Regex("(^|/)(test|tests)/"), Regex("\\.(test|spec)\\.(ts|tsx|js|jsx)$"), Regex("Test\\.(kt|java|php)$"),
            Regex("Tests\\.(kt|java)$"), Regex("(^|/)src/test/"), Regex("(^|/)src/integrationTest/"),
        )

        fun classify(cfg: ClassifyConfig, path: String): String {
            val rel = path.removePrefix("./")
            fun inDir(dir: String) = dir.isNotEmpty() && (rel == dir || rel.startsWith(dir.trimEnd('/') + "/"))
            val isTest = TEST_PATTERNS.any { it.containsMatchIn(rel) }

            if (matchGlob(rel, cfg.protected) && !negated(rel, cfg.protected)) return "protected-env"
            if (matchGlob(rel, cfg.generated)) return "generated"
            if (cfg.contractFile.isNotEmpty() && rel == cfg.contractFile) return "contract"
            if (Regex("openapi\\.(ya?ml|json)$").containsMatchIn(rel)) return "contract"
            if (inDir(cfg.specsDir)) return "specs"
            if (inDir(cfg.e2eDir)) return "e2e"
            if (inDir(cfg.smokeDir)) return "smoke"
            if (inDir(cfg.backendDir)) {
                if (rel.contains(cfg.migrations)) return "migration"
                return if (isTest) "api-test" else "api-main"
            }
            if (inDir(cfg.frontendDir)) return if (isTest) "web-test" else "web-src"
            if (Regex("(^|/)db/migration/").containsMatchIn(rel)) return "migration"
            if (isTest) return "api-test"
            return "other"
        }

        fun glob2re(pattern: String): Regex {
            val re = StringBuilder()
            var i = 0
            while (i < pattern.length) {
                val c = pattern[i]
                when {
                    c == '*' -> if (i + 1 < pattern.length && pattern[i + 1] == '*') {
                        re.append(".*"); i++
                        if (i + 1 < pattern.length && pattern[i + 1] == '/') i++
                    } else re.append("[^/]*")
                    c == '?' -> re.append("[^/]")
                    ".+^\${}()|[]\\".contains(c) -> re.append('\\').append(c)
                    else -> re.append(c)
                }
                i++
            }
            return Regex("^$re$")
        }

        fun matchGlob(file: String, patterns: List<String>): Boolean {
            val f = file.removePrefix("./")
            return patterns.any { p ->
                if (p.startsWith("!")) false else glob2re(p).matches(f) || glob2re(p).matches("/$f")
            }
        }

        fun negated(file: String, patterns: List<String>): Boolean {
            val f = file.removePrefix("./")
            return patterns.any { it.startsWith("!") && glob2re(it.substring(1)).matches(f) }
        }
    }
}
