import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("org.springframework.boot") version "3.3.5"
    id("io.spring.dependency-management") version "1.1.6"
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.spring") version "2.0.21"
}

group = "keel"
// keel v2's version: GET /api/health reports it (from build-info). Keep it equal to KEEL2_VERSION in ../keel2.
version = "0.15.0"

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

// ---------- v0.13.0 keel Product, an add-on in its own folder (../product/api) ----------
// Compiled against keel's api but never part of keel's own jar: `bootJar` stays as it was. `productPluginJar` is the
// add-on's api part as a plugin jar (product/build-plugin.sh puts it in the .kplug). Its tests run in their own task
// (`productTest`) with keel's test support (ApiTest, StubEngine).
val product: SourceSet by sourceSets.creating {
    kotlin.srcDir("../product/api/src/main/kotlin")
    resources.srcDir("../product/api/src/main/resources")
    compileClasspath += sourceSets.main.get().output + sourceSets.main.get().compileClasspath
    runtimeClasspath += output + compileClasspath + sourceSets.main.get().runtimeClasspath
}
val productTest: SourceSet by sourceSets.creating {
    kotlin.srcDir("../product/api/src/test/kotlin")
    resources.srcDir("../product/api/src/test/resources")
    compileClasspath += product.output + sourceSets.main.get().output + sourceSets.test.get().output +
        sourceSets.test.get().compileClasspath + product.compileClasspath
    runtimeClasspath += output + compileClasspath + sourceSets.test.get().runtimeClasspath
}
configurations[productTest.runtimeOnlyConfigurationName].extendsFrom(configurations.testRuntimeOnly.get())

val productTestTask = tasks.register<Test>("productTest") {
    description = "keel Product's api tests"
    group = "verification"
    testClassesDirs = productTest.output.classesDirs
    classpath = productTest.runtimeClasspath
    useJUnitPlatform()
    testLogging {
        events("failed")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}

// A thin jar: only Product's classes and resources (META-INF/spring/...AutoConfiguration.imports,
// db/product/P1__product.sql). Product needs no library beyond keel's own. keel's plain jar loads it at start:
// java -Dloader.path=<jar> -cp keel-api.jar org.springframework.boot.loader.launch.PropertiesLauncher
tasks.register<Jar>("productPluginJar") {
    description = "keel Product's api part as a plugin jar (keel-plugin-product.jar)"
    group = "build"
    from(product.output)
    archiveFileName.set("keel-plugin-product.jar")
    destinationDirectory.set(layout.buildDirectory.dir("libs"))
    // the same input gives the same bytes, so files.sha256 only changes when Product changes
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}
