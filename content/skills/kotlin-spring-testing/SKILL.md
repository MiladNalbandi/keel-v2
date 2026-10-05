---
name: kotlin-spring-testing
description: "Test patterns for a Kotlin Spring Boot backend: choosing the layer, slice tests, body tests validated against the OpenAPI contract, Testcontainers, MockK, tagging tests with acceptance-criteria IDs, and keeping runs fast. Load when writing or fixing backend tests."
user-invocable: false
---

# Kotlin + Spring Boot test patterns

## Pick the lowest layer that can express the AC

| Layer | Use | Annotation |
|---|---|---|
| Unit | Pure rules, no Spring | none, JUnit 5 + MockK + AssertJ |
| Web slice | Status codes, validation, auth rules | `@WebMvcTest` + MockMvc (+ `@MockkBean`) |
| Body / contract | Request and response bodies conform to the contract and carry the right values | MockMvc + `swagger-request-validator-mockmvc` |
| Data slice | Queries, constraints, migrations | `@DataJpaTest` + Testcontainers |
| Integration | Cross-layer flows, transactions | `@SpringBootTest` + `@ServiceConnection` |

## Tag every test with its AC

```kotlin
@Tag("AC-003")
@Test
fun `AC-003 rejects a bookmark without a url`() { }
```

keel runs one criterion's test by that tag, and traces the test back to the criterion by the ID in its name.

## Body test shape

```kotlin
@WebMvcTest(BookmarkController::class)
@Import(OpenApiValidationConfig::class)   // adds the swagger-request-validator filter
class BookmarkBodyTest(@Autowired val mvc: MockMvc) {

  @Tag("AC-002")
  @Test
  fun `AC-002 created bookmark body matches the contract`() {
    mvc.post("/bookmarks") { contentType = APPLICATION_JSON; content = """{"url":"https://x.dev"}""" }
      .andExpect { status { isCreated() } }
      .andExpect { jsonPath("$.id") { exists() } }
      .andExpect { jsonPath("$.url") { value("https://x.dev") } }
      // the validator filter fails the test when the body breaks openapi.yaml
  }
}
```

## Keep runs fast

- One shared test configuration with singleton containers; every different context configuration starts Spring again.
- Testcontainers reuse locally: `withReuse(true)` plus `testcontainers.reuse.enable=true`.
- Avoid `@DirtiesContext` and per-class mock variations.
- Prefer slice tests over `@SpringBootTest` unless the AC needs the whole app.

## Rules keel enforces

- In RED only test files may change; in GREEN only production code.
- Never add `@Disabled`, `@Ignore` or `assumeTrue(false)`: a parked test is a weakened test, and review blocks it.
- A failure from a compile error, a missing bean or Docker is not a red test; fix the setup first.
