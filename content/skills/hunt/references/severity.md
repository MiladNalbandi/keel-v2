# Severity rubric

Severity is a measurement, so only a **proven** finding has one. Judge by impact and reachability, never by how bad
the code looks. A project can change the wording in `.keel/config.yml` under `hunt.severity_rubric`.

| Severity | Means |
|---|---|
| **critical** | data loss or corruption · cross-tenant exposure · authentication or authorization bypass · a silent wrong write |
| **high** | a 5xx on a documented path · a lost update under ordinary concurrency · a retry that creates duplicates |
| **moderate** | a wrong status code with otherwise correct behaviour · missing validation with no exploit path |
| **low** | cosmetic · unreachable in the current code |

**A 5xx is never below `high`.** keel refuses a proven verdict whose evidence or recipe shows a 5xx (or "internal
server error") filed below `hunt.severity_floor_5xx` (default `high`). If the 5xx is not on a documented path, say so
in the evidence; do not quietly downgrade it.

Reachability still sorts within a row: an alarming function behind an endpoint nobody can call is low; a plain one
that hands a caller another tenant's data is critical.

`unproven` carries no severity and is not a polite `false`: it means nobody could measure it yet. `false` means the
claim is wrong, with the check that is really there at `file:line`.
