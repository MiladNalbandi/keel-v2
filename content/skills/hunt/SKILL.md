---
name: hunt
description: The bug hunt's lens briefs and severity rubric. Hunters read the one brief keel hands them; provers judge severity against the rubric. Load in the hunt flow's sweep and prove steps.
---

# Hunt: lenses and severity

The hunt flow is keel's graph, not something an agent drives. This skill holds the two texts its agents are judged
against:

- `references/lenses.md`: one brief per lens. A `hunter` gets exactly one brief and one lane in its item (keel
  copies the brief in, so do not go looking for the others). A project's own `.keel/lenses/<lens>.md` replaces the
  brief of the same name.
- `references/severity.md`: the rubric a `prover` files a proven finding against, and the one clause keel checks by
  itself (a 5xx is never below `high`).
- `references/reachability-probe.sh`: the `reachability` lens's whole technique.

## Two rules the whole flow rests on

- **A finding is a claim until something runs.** Hunters propose candidates; only a prover's recipe, run twice,
  makes one proven, and only a proven finding gets a severity. keel drops any severity a hunter offers.
- **Findings share causes.** One defect often shows up as several symptoms across lenses. After the provers, keel
  groups proven findings that share a cause and hands the group over as one fix, with the symptoms as regression
  criteria.

## What travels where

| Who | Gets | Never gets |
|---|---|---|
| hunter | its lens brief, its lane, the scope, the cap | the other lenses, a severity to fill in |
| prover | the candidate's symptom and where | the hunter's `claim` (a prover told the theory confirms it) |
| the fix flow's reproducer | the lead's recipe and every symptom | the claim |
