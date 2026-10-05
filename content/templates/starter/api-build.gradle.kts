plugins {
    kotlin("jvm") version "2.1.0"
    kotlin("plugin.spring") version "2.1.0"
    id("org.springframework.boot") version "3.4.1"
    id("io.spring.dependency-management") version "1.1.7"
    id("org.jetbrains.kotlinx.kover") version "0.9.1"
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("com.ninja-squad:springmockk:4.0.2")
    testImplementation("com.atlassian.oai:swagger-request-validator-mockmvc:2.44.1")
    testImplementation("org.testcontainers:postgresql:1.20.4")
}

tasks.test {
    useJUnitPlatform {
        // keel filters by acceptance-criterion tag: ./gradlew test -PacTag=AC-003
        (project.findProperty("acTag") as String?)?.let { includeTags(it) }
    }
}

kover {
    reports {
        filters { excludes { classes("*ApplicationKt", "*.generated.*") } }
        verify { rule { minBound(95) } }
    }
}
