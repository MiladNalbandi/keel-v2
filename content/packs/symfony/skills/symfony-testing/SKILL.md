---
name: symfony-testing
description: "Test patterns for a Symfony backend: choosing the layer, KernelTestCase and WebTestCase, body tests validated against the OpenAPI contract, Doctrine fixtures, tagging tests with acceptance-criteria IDs, and keeping runs fast. Load when writing or fixing backend tests."
user-invocable: false
---

# Symfony test patterns

## Pick the lowest layer that can express the AC

| Layer | Use | Base class |
|---|---|---|
| Unit | Pure PHP, no framework | `PHPUnit\Framework\TestCase` |
| Kernel | Service wiring, no HTTP | `KernelTestCase` |
| Web | Status codes, validation, auth rules | `WebTestCase` |
| Body / contract | Request and response bodies conform to the contract | `WebTestCase` + a contract-validating assertion |
| Integration | Cross-layer flows, a real (test) database | `KernelTestCase` + `DoctrineTestBundle` transaction rollback |

## Tag every test with its AC

```php
#[Group('AC-003')]
public function testRejectsABookmarkWithoutAUrl(): void { /* ... */ }
```

keel runs one criterion's test by that group, and traces the test back by the ID in the
method name or a leading comment — same convention as every other stack, just PHPUnit's own
attribute for it.

## Body test shape

```php
#[Group('AC-002')]
final class BookmarkControllerTest extends WebTestCase
{
    #[Group('AC-002')]
    public function testCreatedBookmarkBodyMatchesTheContract(): void
    {
        $client = static::createClient();
        $client->request('POST', '/bookmarks', server: ['CONTENT_TYPE' => 'application/json'],
            content: json_encode(['url' => 'https://x.dev']));

        self::assertResponseStatusCodeSame(201);
        $body = json_decode($client->getResponse()->getContent(), true);
        self::assertArrayHasKey('id', $body);
        self::assertSame('https://x.dev', $body['url']);
        // validate $body against contracts/openapi.yaml the same way every other stack does —
        // a schema-validating assertion here, not a hand-picked subset of fields.
    }
}
```

## Fixtures and the database

- Reset state between tests with `DoctrineTestBundle`'s transaction rollback (`ResetDatabase` +
  `Factories` traits), not a full schema drop — slow, and unnecessary per test.
- Load fixtures through Doctrine fixture classes, not raw SQL scattered across tests; one
  fixture set per test class keeps a failing test's setup readable in the failure output.
- A migration is real production code — see `architecture`'s migration guidance, which
  applies here exactly as it does to any other stack's schema change.

## Keep runs fast

- Prefer `KernelTestCase` over `WebTestCase` when the AC doesn't need HTTP semantics — booting
  the kernel without the full request/response cycle is cheaper.
- Share one kernel across a test class rather than rebooting it per test method.
- `--filter` (used by `api_test_ac`) runs one AC's tests without paying for the whole suite.

## Rules keel enforces

- In RED only test files may change; in GREEN only production code — identical to every other
  stack, enforced by the same guard matrix, not by anything PHP-specific.
- A failure from a missing autoloader entry, an unconfigured service or a database connection
  problem is not a red test; fix the setup first, the same rule `debugging` states for every
  stack.
- Never mark a test `skipped` or `incomplete` to make a suite pass — a parked test is a weakened
  test, the same as `@Disabled` on the Kotlin side, and review blocks it.
