# Import boundaries

Loading a skill cannot be enforced — the *absence* of a skill read is not a tool event anything can block. So the architecture choice is made binding on the output side instead: the imports in the code that came out.

## The config block

The project declares its boundaries under `boundaries:` in `.keel/config.yml`:

```yaml
boundaries:
  enforce: warn      # off | warn | block
  rules:
    - name: domain-framework-free
      from: 'apps/api/**/domain/**'
      deny_imports: ['org.springframework.**', 'jakarta.persistence.**', 'javax.persistence.**']
    - name: feature-isolation
      from: 'apps/web/src/features/*/**'
      deny_imports: ['@/features/**']
      allow_imports: ['@/shared/**', '@/entities/**']
```

| Field | Meaning |
|---|---|
| `from` | Glob of files the rule applies to. A file matching no rule is never checked. |
| `deny_imports` | Import targets this file may not reference. |
| `allow_imports` | Optional whitelist that wins over `deny_imports`, for "nothing sideways except these". |
| `enforce` | `off` skips the rule; `warn` reports and passes; `block` makes a crossing blocking. |

**Write the target in the language's own form.** Kotlin and Java keep dotted packages (`org.springframework.**`); TypeScript keeps the module path (`@/features/**`). Mixing them silently matches nothing.

## How a crossing is found

Read the **import lines only** of changed files, resolve each to its target, and test it against the rules whose `from` matches. No compiler, no dependency graph — a grep, deliberately.

The consequences of that are worth knowing:

- Look at **changed files**, not the whole repo. A pre-existing violation in a file this branch did not touch is not this branch's finding.
- It matches **import statements**, so a fully-qualified reference written inline in the body, or a reflective lookup by string, needs reading the body.
- An unused import still counts, which is correct: it should not be there.

The architecture review lens at ship reads the style and its reference, and reports **new** boundary crossings only.

## When a rule is wrong

A boundary that fights the codebase is a bad rule, not a bad codebase. Change it in `.keel/config.yml` under `boundaries.rules`. Do not work around a rule by restructuring code mid-AC, and do not silence the whole check by setting `enforce: off` when one rule is the problem.
