import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("org.springframework.boot") version "3.3.5"
    id("io.spring.dependency-management") version "1.1.6"
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.spring") version "2.0.21"
}

group = "keel"
// keel v2's version: GET /api/health reports it (from build-info). Keep it equal to KEEL2_VERSION in ../keel2.
version = "0.15.1"

java {
    toolchain { languageVersion.set(JavaLanguageVersion.of(21)) }
}

repositories { mavenCentral() }

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.flywaydb:flyway-core")
    implementation("org.xerial:sqlite-jdbc:3.46.1.3")
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin")
    implementation("com.fasterxml.jackson.dataformat:jackson-dataformat-yaml")
    implementation("org.jetbrains.kotlin:kotlin-reflect")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.jetbrains.kotlin:kotlin-test-junit5")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

kotlin {
    compilerOptions {
        freeCompilerArgs.addAll("-Xjsr305=strict")
        jvmTarget.set(JvmTarget.JVM_21)
    }
}

tasks.withType<Test> {
    useJUnitPlatform()
    testLogging {
        events("passed", "failed", "skipped")
        showStandardStreams = false
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}

springBoot {
    // META-INF/build-info.properties, read by the health endpoint; no build time, so the task stays up to date
    buildInfo { excludes.set(setOf("time")) }
}

tasks.named<org.springframework.boot.gradle.tasks.bundling.BootJar>("bootJar") {
    archiveFileName.set("keel-api.jar")
}
tasks.named<Jar>("jar") { enabled = false }

// ---------- plugins: each plugins/<name>/api, and keel Product (../product/api) ----------
// A plugin's api part is compiled against keel's api but is never part of keel's own jar: `bootJar` stays as it was.
// For each one (docs/plugins/11-step3-contract.md, wave 0):
//   source sets <name> and <name>Test        (a name with dashes is written in camel case: code-review -> codeReview)
//   <name>PluginJar  build/libs/keel-plugin-<name>.jar, a thin jar of its classes and resources only (it needs no
//                    library beyond keel's own). keel's plain jar loads it at start:
//                    java -Dloader.path=<jar> -cp keel-api.jar org.springframework.boot.loader.launch.PropertiesLauncher
//   <name>Test       its tests, with keel's test support (ApiTest, StubEngine)
// `pluginJars` and `pluginTest` run them for every plugin in ../plugins. keel Product keeps its own tasks
// (productPluginJar, productTest), made the same way.

/** A plugin's tasks. */
class PluginTasks(val jar: TaskProvider<Jar>, val test: TaskProvider<Test>)

fun camelName(name: String): String =
    name.split('-').mapIndexed { i, s -> if (i == 0) s else s.replaceFirstChar { it.uppercase() } }.joinToString("")

fun pluginApi(name: String, dir: File): PluginTasks {
    val id = camelName(name)
    val main = sourceSets.create(id) {
        kotlin.srcDir(dir.resolve("src/main/kotlin"))
        resources.srcDir(dir.resolve("src/main/resources"))
        compileClasspath += sourceSets.main.get().output + sourceSets.main.get().compileClasspath
        runtimeClasspath += output + compileClasspath + sourceSets.main.get().runtimeClasspath
    }
    val tests = sourceSets.create("${id}Test") {
        kotlin.srcDir(dir.resolve("src/test/kotlin"))
        resources.srcDir(dir.resolve("src/test/resources"))
        compileClasspath += main.output + sourceSets.main.get().output + sourceSets.test.get().output +
            sourceSets.test.get().compileClasspath + main.compileClasspath
        runtimeClasspath += output + compileClasspath + sourceSets.test.get().runtimeClasspath
    }
    configurations[tests.runtimeOnlyConfigurationName].extendsFrom(configurations.testRuntimeOnly.get())

    val test = tasks.register<Test>("${id}Test") {
        description = "the $name plugin's api tests"
        group = "verification"
        testClassesDirs = tests.output.classesDirs
        classpath = tests.runtimeClasspath
        useJUnitPlatform()
        testLogging {
            events("failed")
            exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
        }
    }
    val jar = tasks.register<Jar>("${id}PluginJar") {
        description = "the $name plugin's api part as a plugin jar (keel-plugin-$name.jar)"
        group = "build"
        from(main.output)
        archiveFileName.set("keel-plugin-$name.jar")
        destinationDirectory.set(layout.buildDirectory.dir("libs"))
        // the same input gives the same bytes, so files.sha256 only changes when the plugin changes
        isPreserveFileTimestamps = false
        isReproducibleFileOrder = true
    }
    return PluginTasks(jar, test)
}

val pluginName = Regex("^[a-z][a-z0-9-]{0,31}$")
val plugins: List<PluginTasks> = (file("../plugins").listFiles() ?: emptyArray())
    .filter { it.resolve("api").isDirectory }
    .sortedBy { it.name }
    .map {
        require(pluginName.matches(it.name)) { "plugins/${it.name}: a plugin's name is a-z, 0-9 and '-' (32 at most)" }
        pluginApi(it.name, it.resolve("api"))
    }

tasks.register("pluginJars") {
    description = "every plugin's api jar (plugins/*/api): build/libs/keel-plugin-<name>.jar"
    group = "build"
    dependsOn(plugins.map { it.jar })
}
tasks.register("pluginTest") {
    description = "every plugin's api tests (plugins/*/api)"
    group = "verification"
    dependsOn(plugins.map { it.test })
}

// keel Product (beta) keeps its folder: productPluginJar (Product's classes and resources: META-INF/spring/
// ...AutoConfiguration.imports, db/product/P1__product.sql; product/build-plugin.sh puts it in the .kplug) and
// productTest.
pluginApi("product", file("../product/api"))
