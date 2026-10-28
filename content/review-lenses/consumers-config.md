# Consumers and config

what breaks for a consumer that runs the new next to the old: shared keys and routes, public types, CI

<!-- keel 0.18.65: on by default in the ship review (runtime/ship.py DEFAULT_LENSES), off on the Review page until
Settings › Review turns it on. A field report: the last review passed a change that shared one signing key between an
old and a new route, used a call deprecated on the newest runtime, accepted more in a public type than its contract
said, and broke a consumer that upgraded. Format: engine runtime/lenses.py. -->

## Checklist

- Shared config keys, parameters and routes: list every key, secret, route, parameter and event name the new code uses, and check each against the old version (git grep it at the base). One value used by both for different things (a signing key shared by an old and a new route, one queue name, one cache key) is a finding.
- Each public type against its contract: read the documented contract (the API docs, the schema, the type's comment) and check that the type accepts exactly that. A field documented as a list must not accept an object; a required field must not have a default that hides its absence.
- Consumer upgrade: what breaks when a consumer adds the new version next to the old one (two registrations of one name, a config key both read, a changed default, a removed export)? Say it in one line per break.
- Runtime and dependency versions: a call this change makes that is deprecated or removed on the newest runtime or framework version the project supports (its CI matrix, its engines or requires field).
- CI config: read the project's CI files (.github/workflows/*, .gitlab-ci.yml, and the like) and name the commands they run. Say what this change needs there and does not have (a new job, a matrix entry, an env key, a service).
