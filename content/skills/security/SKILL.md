---
name: security
description: "Security review patterns for a Kotlin/Spring backend and TypeScript frontend: endpoint authorization, input validation, data exposure, logic flaws that need intent to see, and dependency CVE triage. Load in the security phase and when reviewing a diff for the security lens."
user-invocable: false
---

# Security review patterns

Read **only** the references the change actually calls for. Loading all five wastes the
context the split exists to save.

## Which reference to load

Classify each changed file with the same buckets `keel` already uses (`guards.classify`),
then load the union of what matches:

| Changed file | Load |
|---|---|
| `api-main` — a controller, endpoint, service | `references/api-kotlin.md` + `references/logic.md` |
| `migration`, or an entity/repository under `api-main` | `references/data.md` + `references/logic.md` |
| `web-src` — frontend source | `references/web.md` |
| A lockfile or build file (`package-lock.json`, `pnpm-lock.yaml`, `build.gradle.kts`, `pom.xml`) | `references/deps.md` |
| Anything matching `change.auth_paths` (`**/security/**`, `**/auth/**`) | `references/logic.md`, always — and the human gate is forced |

`logic.md` appears three times on purpose. It is the only one that needs the spec, and the
only one a scanner cannot replace.

## What to report

One finding per issue, each with `file:line`, each marked **blocking** or not.

- **Blocking**: a missing authorization check, an authorization check that can be bypassed,
  unvalidated input reaching a query or the filesystem, secrets or PII in a response or a
  log, a CVE reachable from our own code above `security.deps.fail_on`.
- **Non-blocking**: hardening worth doing that no current requirement demands — a tighter
  rate limit, a header, a defence in depth behind an already-correct check.

Say what an attacker does, not what is theoretically unsound. "A user with a valid token
can read another tenant's order by changing the path id" is a finding; "authorization
could be stronger" is not.

## What not to report

- Pre-existing issues outside the diff. Note them once in the non-blocking list and move on.
- Style, naming, or a preference for a different library.
- Anything already covered by a passing test — check before claiming it.

## The honest limit

Reading a diff against a spec finds real authorization and logic flaws. It also misses
some. The parts that hold every time are the deterministic ones: the secret scan at the
edit, the dependency gate at the push, and the 100% coverage bar on `security.coverage_paths`.
Do not report "no issues found" as proof there are none — report what you checked.
