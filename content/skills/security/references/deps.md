# Dependency CVE triage

The scan collects the machine output — `npm audit --json` and the Gradle/OSV
report — before triage starts. It does not judge. The judgment
below is the only part a model should be doing, because a CVE id invented by a model is
worse than no report at all: **never write a CVE number you did not read from the scan
output.**

## The four questions, in order

**1. Is the vulnerable code path reachable from ours?**

A CVE is in a function, not in a package. Find the affected symbol in the advisory, then
grep for it.

```
grep -rn "deserialize\|readValue" apps/api/src/main   # the affected jackson entry points
grep -rn "from 'lodash" apps/web/src                  # is it even imported?
```

Reachable and called with data an attacker controls → treat at the advisory's severity.
Present but never called → note it, do not block.

**2. Is it transitive, and is it dev-only?**

```
npm ls <package>                 # who pulls it in
./gradlew -q dependencyInsight --dependency <name> --configuration runtimeClasspath
```

A dev-only dependency — a test runner, a bundler, a linter — is not in the shipped artefact.
It still matters for supply-chain attacks on the build, but it is rarely blocking. Say which
it is; "found in `devDependencies`" changes the decision.

Check the configuration, not the section name: something in `devDependencies` that gets
bundled into the client is a runtime dependency in practice.

**3. Does a fixed version exist, and what does it cost?**

| Situation | Answer |
|---|---|
| Patch release available, same major | Upgrade. The whole finding is a one-line change |
| Fix only in the next major | Say so, with the breaking change it implies. That is a spec-flow decision, not a coverage fix |
| No fix published | Mitigation or allowlist — see below |

A dependency upgrade is a behaviour change: it goes through the normal flow, and the module
suite is what proves it.

**4. Is an allowlist entry the honest answer?**

Sometimes yes: no fix exists, the path is unreachable, and blocking the push buys nothing.
Then say so explicitly, with an expiry, so it comes back:

```yaml
security:
  deps:
    fail_on: high
    allowlist:
      - id: CVE-2024-12345
        reason: only reachable through the XML parser, which we never call
        expires: 2026-12-31
```

An entry without a reason and an expiry is not a decision, it is a silence. Three rules:
never allowlist something reachable, never allowlist without an expiry, and never allowlist
to unblock a push you are in a hurry to make — that is what `--force` conversations are for,
and they should be uncomfortable.

## What to report

For each advisory above the threshold, one line:

> `jackson-databind 2.13.0` — CVE from the scan output, high. Reachable: yes,
> `ObjectMapper.readValue` on the request body in `BookmarkController.kt:41`. Fixed in
> 2.13.4.2, same major. Blocking.

And for the rest, one line each saying why not: unreachable, dev-only, or no fix with a
mitigation in place.

## What not to do

- Do not re-run the scan yourself; read the output you were given.
- Do not report a count ("14 vulnerabilities") as a finding. A count is not a decision.
- Do not upgrade anything as part of the triage. Report; the upgrade is its own change.
