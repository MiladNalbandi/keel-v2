# Tests that assert nothing

always-true assertions, a test that only checks a service exists, a mock of the thing under test

<!-- One file per lens (keel 0.18.57; the title and the description line under it 0.18.65, the format is
engine runtime/lenses.py). runtime/ticket_actions.py gives a review-pr reviewer the Checklist below in its
job item (it stays short: a job item is kept under 5,200 characters). The Checklist wording is the one measured on
keel's review-eval benchmark PR; the Reference is the longer guide from the 2026-10-26 review research, kept for a
lens agent of its own (not wired yet, not measured). New wording, written for keel. -->

## Checklist

Test quality: would each new or changed test fail if the code it covers broke? Look for assertions that are always true (>= 0, not null on a service), tests of the happy path only where the ticket names a failure case, mocks that replace the thing under test, a test schema or config that differs from production so the test passes while prod breaks, and weakened or deleted assertions.

## Reference

You review one area of someone's change through **one lens: do the tests protect this change?** Not
coverage numbers — whether a test would go red if the new behaviour were wrong. Read the tests **first**,
then the production code they claim to cover.

Use the `read` commands in your item only (`git diff <base>...<ref> -- <file>`, `git show <ref>:<file>`,
`git grep -n <word> <ref> -- <test folders>`). Load the testing skill that matches the files
(`kotlin-spring-testing`, `web-testing`, `playwright`, or the stack's own) for this codebase's test patterns,
and the `hunt` skill's `references/lenses.md` section `test-integrity`.

## Questions

**Risky behaviour with no test** — rank by what breaks if it is wrong, not by line count:

- A new branch that handles failure, a boundary (empty, zero, max, null, duplicate), money, permissions,
  a state change, a migration's effect on old data, a message handled twice. Find its test with `git grep -n`
  on the function name, the error type and the case — tests may live in another module or an integration
  suite. Only when none exists is it a finding; name the case and what the test should assert.
- A bug fix with no test that fails without the fix.

**Tests that would stay green while the code is wrong**

- No meaningful assertion: the test calls the code and checks nothing, or only that no exception was thrown.
- A `verify(mock).called()` as the only check, proving a call happened, not what it did.
- The expected value computed with the same logic as the code under test, or copied from its output.
- A mock that returns a value or error the real dependency never produces (a typed error the adapter never
  throws, a response shape the API does not have) — the test passes, production fails.
- The assertion covers the happy path while the test's name claims the edge case.
- A test that reads source code as text and matches its shape, instead of running it.

**Tests weakened by this change**

- A test disabled, skipped, deleted or `@Ignore`d; an assertion loosened (exact → contains, equals → not
  null); a tolerance widened; a retry or sleep added to make it pass; an expected value changed to match new
  output without the ticket asking for new behaviour. Compare with the base (`git show <base>:<test file>`).

## Not this lens

Line-coverage percentages. Test naming style or structure preferences, unless the repo's standards document
them. Tests for trivial getters, mappers or framework wiring. A missing test for a branch nobody can reach —
say it once as minor if the branch itself looks dead.

## Severity

**blocking**: a test disabled or weakened to make the change pass; a test that claims to cover a risky
behaviour (failure path, money, permissions, data migration, duplicate delivery) but cannot fail; a bug fix
with no regression test when the ticket is a bug. **minor**: a risky new branch with no test; a brittle test
tied to implementation details.

## Answer in lines, nothing else

```
F | blocking | src/test/kotlin/order/OrderServiceTest.kt:88 | "rejects duplicate order" mocks repository.save to throw DuplicateOrder, which JpaOrderRepository never throws (it raises DataIntegrityViolationException at commit), so the test passes while the endpoint returns 500 | test through the real repository (Testcontainers) and assert the 409 | lens:test-quality | conf:high | evidence:src/test/kotlin/order/OrderServiceTest.kt:80-95; src/main/kotlin/order/JpaOrderRepository.kt:40
F | minor | src/main/kotlin/order/OrderService.kt:71 | the new refund-over-total branch has no test | add a test: refund 101 on a total of 100 returns 422 and changes nothing | lens:test-quality | conf:high | evidence:src/main/kotlin/order/OrderService.kt:71; (no match for "refund" in src/test)
R | src/test/kotlin/order/OrderServiceTest.kt | full
X | e2e/ | not judged | the e2e suite may cover refunds but runs on another repo | ask the author whether an e2e test covers the refund limit
S | One sentence: which tests you read and which risky branches they cover.
```

Columns and rules as in `pr-reviewer`: `F | blocking|minor | where | trigger → effect | what to do | lens:test-quality | conf:high|medium | evidence:<file:line; ...>`; an `R` line for every file in your list; an `X` line for each thing you set aside. At most 8 `F` lines. End with exactly one line: `TICKET-REVIEW: done`.
