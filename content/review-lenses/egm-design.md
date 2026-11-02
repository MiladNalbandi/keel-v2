# Design critic

a design that breaks under load, failure or growth, a risk with no answer, a cost nobody counted

<!-- keel 0.20.0: keel's wording, adapted for a code change from the design critic persona of Google's Elephant-Goldfish
Model (content/NOTICE.md). EGM's persona reviews a design document; this one reviews a diff. -->

## Checklist

Design critic: attack the design this change puts in place, not its style. What happens at ten times the data or traffic: is there an unbounded list, a query per row, a lock held across a slow call? What happens when each dependency it calls is slow or down? Two copies running at once: is any step unsafe (a race, a double write, no idempotency)? Which risk does it leave with no answer? Is state kept in a place that will disagree with another (two sources of truth)? Does it put new load or cost on something shared (a database, a paid API)? Say the concrete situation that breaks it.
