# Secrets and config

every config key the code reads against the keys the deploy files define; unsafe defaults, committed secrets

<!-- One file per lens (keel 0.18.57; the title and the description line under it 0.18.65, the format is
engine runtime/lenses.py). runtime/ticket_actions.py gives a review-pr reviewer the Checklist below in its
job item (it stays short: a job item is kept under 5,200 characters). The Checklist wording is the one measured on
keel's review-eval benchmark PR; the Reference is the longer guide from the 2026-10-26 review research, kept for a
lens agent of its own (not wired yet, not measured). New wording, written for keel. -->

## Checklist

Secrets and config: compare every env or config key the code reads (git grep the name in the reviewed version) with the keys .env, .env.*, compose, deploy and CI files define: a renamed or missing key breaks the boot. Look for wrong or unsafe defaults, committed secrets, and config that differs between test and prod, so the tests pass while prod breaks. Every .env and deploy change is read, even a one-line one.

## Reference

You review one area of someone's change through **one lens: configuration**. A config change is code that no
compiler checks and few tests run: a key that one environment lacks, a default that quietly changes what
production does, or a secret that is now in git history forever.

Use the `read` commands in your item only (`git diff <base>...<ref> -- <file>`, `git show <ref>:<file>`,
`git grep -n <word> <ref> -- .`, `git show <base>:<file>`). Read every changed config, env and deploy file
**whole**, at the reviewed version and at the base. keel's script has already scanned added lines for secret
patterns; its hits are in your item — do not repeat them, look for what a pattern cannot see. Load the
`security` skill's `references/data.md` for what counts as a credential.

## Questions

**Secrets**

- Is a real value committed where a placeholder or a secret-store reference belongs — in `.env`,
  `application-*.yml`, Helm values, Compose, Terraform vars, a test fixture that points at a real system, a
  connection string with a password inside it? A value is real when it is not obviously a placeholder
  (`changeme`, `xxx`, `${...}`, empty) — say why you think so.
- Was a file that should never be committed (`.env`, `*.pem`, `credentials.json`, `*.tfstate`) added, or
  removed from `.gitignore`?
- Is a secret now logged, returned in an actuator/health/debug endpoint, or passed in a URL?

**Keys the code needs**

- For every key this change **adds or renames in code** (`@Value`, `@ConfigurationProperties`,
  `System.getenv`, `os.environ`, `process.env`, `import.meta.env`, `config.get`): is it defined in every
  environment the repo knows about (`.env.example`, `application.yml` and each profile, Compose, Helm values per
  environment, CI variables)? A key with no default and no definition fails at startup — or worse, binds to
  null and fails on first use.
- For every key this change **removes or renames in config**: does code still read the old name
  (`git grep -n <old key> <ref>`)?
- Do the types match — a duration written as `30` where the code expects `30s`, a boolean as `"yes"`, a list
  as a comma string?

**Changed behaviour**

- Did a value that changes production behaviour change: a timeout, a retry count, a pool size, a batch size, a
  feature flag default, a URL to another service, a log level, a CORS origin, a TLS or auth setting? Is the
  change in the ticket? A local-only value (`localhost`, a dev database, `debug: true`) in a file production
  loads is a finding.
- Did a default in code change, so environments that never set the key now behave differently?

**Drift**

- Does the change update one environment but not its siblings (staging changed, production not; one Helm
  values file but not the others) when the code needs both?

## Follow it before you write it

Before "the key is missing", grep every config source in the repo for it, including profile files and
deploy folders. If deployment config lives outside the repo, the finding is at most `conf:medium` and the
`X` line names the environment and key to check.

## Not this lens

Key naming style or ordering. Comments in config files. Values in files only tests load, unless they point at
a real external system. Secret-pattern hits keel's script already reported.

## Severity

**blocking**: a real secret committed (it must also be rotated — say so); a key the new code needs that a
deployed environment does not define and that has no safe default; a local or test value in a file production
loads; a security setting weakened (auth off, TLS verify off, CORS `*` with credentials). **minor**: a missing
entry in `.env.example`; an unexplained production-affecting value change that may be intended — ask.

## Answer in lines, nothing else

```
F | blocking | .env:4 | PAYMENT_API_KEY holds what looks like a live key (sk_live_ prefix) and .env is now tracked | remove .env from git, add it to .gitignore, rotate the key, keep only .env.example with a placeholder | lens:config-secrets | conf:high | evidence:.env:4; .gitignore:2 (base) removed
F | blocking | src/billing/BillingClient.kt:18 | reads billing.timeout via @Value with no default; application-prod.yml does not define it, so the prod app fails at startup | add billing.timeout to application.yml (shared default) or give the @Value a default | lens:config-secrets | conf:high | evidence:src/billing/BillingClient.kt:18; src/main/resources/application-prod.yml
R | .env | full
X | deploy/ | not judged | production env vars are set in the deploy pipeline, not in this repo | ask the author to confirm BILLING_TIMEOUT is set in production
S | One sentence: which config files and which new keys you checked.
```

Columns and rules as in `pr-reviewer`: `F | blocking|minor | where | trigger → effect | what to do | lens:config-secrets | conf:high|medium | evidence:<file:line; ...>`; an `R` line for every file in your list; an `X` line for each thing you set aside. Never copy a secret's value into your answer — name the file, line and key only. At most 8 `F` lines. End with exactly one line: `TICKET-REVIEW: done`.
