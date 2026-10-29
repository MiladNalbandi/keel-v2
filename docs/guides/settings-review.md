# Settings › Review

Decide who reviews a change, what they check, which model they use, and how keel fixes what they find.

## What this tab does

Since keel 0.18.65 each project has a **Review** tab in Settings. It decides two things:

- what **Find problems** on the Review page does,
- how keel's last review of a flow runs.

keel saves it in the project's `.keel/config.yml` under `review:`. So a teammate's keel uses the same settings.
When nothing is written there, keel uses its defaults.

## How a review runs (keel 0.18.67)

Every review is one flow: **Find problems** on the Review page, a PR review and your uncommitted changes all run the
`review-pr` flow. You can watch each step on the Flow page. No agent starts in the background.

```text
 Find problems ─┐
 review a PR ───┼─ prepare ─ build + lint ─ reviewers (by area, with lenses) ─ second look at each claim ─ report
 uncommitted ───┘     │                                                                                     │
                 your standards                                                                   Problems tab
```

- **Depth** says how hard the review looks:
  - **Quick** (about 20k tokens): one reviewer and at most two lenses. A few claims get a second look.
  - **Standard** (about 80k tokens, the default): a large change gets one reviewer per area, with the lenses its files
    need. Checklist and quality reviewers join them, and every claim gets a second look.
  - **Full** (about 250k tokens): one reviewer per area even for a small change, and every lens that fits. The second
    look runs on the stronger model.
- **Your standards**: every reviewer reads the project's own rules first. These are AGENTS.md, CLAUDE.md, CONTRIBUTING,
  `.github/copilot-instructions.md`, `.cursor/rules/` and the style docs, plus your lenses (`.keel/lenses/`) and the
  stack. keel reads them from the change's base branch, so a change cannot rewrite its own rules. A quality note quotes
  the rule it breaks.
- **Classic** runs the reviewers below as KeelBot runs, as before 0.18.67.

The tokens are first guesses for a change of a few hundred lines.

In `.keel/config.yml`:

```yaml
review:
  depth: full          # quick | standard | full
  find_runs: classic   # flow (default) | classic
```

To review your uncommitted changes, start `review-pr` with `uncommitted: true`. keel makes a snapshot commit in a
throwaway index (`refs/keel/review/uncommitted`). Your index, files and branch stay as they are.

## Reviewers (classic)

```text
 Find problems ─ reviewers A, B (1 to 4) ─ each goes through its lenses ─ the checker
               ─ confirmed findings ─ Fix it
```

Choose how many reviewers read the change: 1 to 4. keel's default is 2:

- **A**: correctness and security,
- **B**: tests, the description and the design.

For each reviewer you set:

- a name and its focus,
- a model: the project's default, *stronger*, or one you pick,
- an effort (how hard the model thinks),
- an agent. The agent gives the reviewer its role and skills. No agent means keel's review prompt only.

The Review page then says how many reviewers read the change and what each one looks at.

## Lenses

A **lens** is a checklist that a reviewer goes through.

- keel's own lenses are in `content/review-lenses/`.
- Your project's lenses are `.keel/lenses/<name>.md`. The bug hunt reads the same files.
- Turn a lens on or off, and give it to a reviewer.
- **+ New lens** writes `.keel/lenses/<name>.md`. Commit it to share it with your team.
- **Edit** opens a project lens. **View** shows one of keel's.

A lens file is a title, one short line under it, and a `## Checklist` section:

```markdown
# Money rounding

cents as integers, one rounding place, totals equal the sum of their lines

## Checklist

- amounts are integers in cents, never floats
- an order total equals the sum of its lines after discounts
```

## The checker

The checker asks "is the reviewer right?" for each finding. Set its own model and effort, and whether it checks
should-fix findings too (on by default). A should-fix finding it does not check is listed as "not checked", never as
a finding.

## Fixing a finding

A confirmed finding has **Fix it**. It runs a flow on the reviewed branch, in the project folder. It commits there
and pushes nothing. Pick the flow:

- **quick**,
- **small** (keel's default),
- **change**,
- **ask me each time** (the finding then shows the three buttons).

You also set the fix model (the flow's own by default), the run mode, and "several findings in one file: one flow
for all of them".

## The last review of a flow

This is the whole-branch review at the end of a feature, small or plugin flow, and the ship review's lens
reviewers. By default they run on a **stronger** model than the project's:

- the project uses a Claude model below Opus (Sonnet, Haiku): they run on **Opus at high effort**,
- on Codex or Copilot: at their high effort.

The tab shows it, for example: "project default (sonnet) → the final review uses claude · opus · high". Pick the
project's default or another model to change it. A model you pick for one step of one flow still wins.

## The ship review

Its lenses are correctness, security, performance and **consumers-config**. Consumers-config checks what breaks for
a user of your code that runs the new version next to the old one: shared keys and routes, public types against
their contract, the CI config. Or set your own list in `review.lenses`.

"Skip the ship's correctness lens…" turns the old behaviour back on (skip it after a passed full-diff review).

## Back to keel's default

**Back to keel's default** takes this tab's settings out of `.keel/config.yml`. The ship's `review.lenses` stays.

See also [Review a pull request](#/help/review-a-pull-request) and
[Choose models per step](#/help/models-per-step).
