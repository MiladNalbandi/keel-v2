---
name: architecture
description: "Where new code belongs in this codebase, for the architecture style the project actually uses: hexagonal, DDD, layered, MVC or feature-sliced. Load in GREEN before placing a new endpoint, rule, query or component, and at ship when reviewing placement."
user-invocable: false
---

# Where the code goes

This answers one question: **the test drives some new code — which package does it belong in?**

It never answers "what abstraction should I add". The minimum-code rule stands: no field, endpoint, abstraction or branch the test does not drive. Placement and restraint are separate concerns, and this skill only covers placement.

## Load one reference, for this project's style

The style is in `docs/knowledge/architecture.md` (or named in your prompt). Then read exactly one file:

| `architecture.style` | Read |
|---|---|
| `hexagonal` | `references/hexagonal-kotlin.md` |
| `ddd` | `references/ddd-kotlin.md` |
| `layered` | `references/layered-kotlin.md` |
| `mvc` | `references/mvc-kotlin.md` |
| `feature-sliced` | `references/feature-sliced-web.md` |
| `unknown` | Nothing. Copy the nearest neighbour file and say so. |

A monorepo usually has two styles, one for the backend and one for the frontend. Read the one for the module you are editing.

When `hybrid_with` is set, the codebase mixes two styles. Follow the **prevailing** one for the directory you are in — read the neighbours — and do not migrate anything on the way past.

## Boundaries are reviewed, not trusted

`references/boundaries.md` covers the `boundaries:` block: which imports a style refuses. The architecture review lens reads it and reports every new crossing.

## Two rules that override any style

1. **Copy the neighbours.** The style reference describes the intent; the directory you are editing describes the practice. Where they differ, match the practice and mention the difference once.
2. **Never restructure while implementing an AC.** Moving a file is a change with its own acceptance criterion. If placement is genuinely wrong, say so at the gate.
