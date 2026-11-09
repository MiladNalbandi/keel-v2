# Rules: what agents may do

keel checks every action of every agent against its rules; the Rules page shows them in plain words, and you can add your own.

## How the rules work

```text
 an agent wants to act ──► keel's hook checks it ──► allowed ──► it runs
                              │                                     │
                              └─► refused: the agent reads why      ▼
                                                        after the step: keel compares the files
                                                        and puts back what the phase does not allow
```

A **hook** is a small check that runs before each action. A **phase** is a part of a flow (for example red:
write the failing test, or green: write the code).

## Open the Rules page

Open **Build › Safety & History**. The tab **Safety rules** shows every rule. In keel TUI type `/rules`.

## Safety tiers: what can not be undone

After keel's own rules say yes, keel asks one more question: how bad is the worst outcome of this call? It reads a
command the way a shell would, so a hidden command (base64, escapes, `eval`, `sh -c`) is judged by what it really runs.

```text
 T0  reads only           ls, git status, cat a file             runs
 T1  can be undone        an edit in the project, a commit        runs
 T2  hard to undo         git push, a deploy, a hidden command    KeelBot asks you first; in a flow it runs, and the History shows it
 T3  cannot be undone     rm -rf /, DROP TABLE, terraform destroy refused, always
```

The agent gets the reason back and is told to stop and ask you, not to try another way. The rule is called
`catastrophic`; you can switch it off for a project (keel's rules you can switch off).

## History

The tab **History** shows every tool call of this project's agents (flows and KeelBot), newest first, grouped by run:
its tier, whether it ran or was blocked, and why. Filter by decision, tier or kind, or search for a command or a file.

Each line of the record holds the hash of the line before it. If someone changes, removes or moves a line, the page
says **The record was changed** and names the line. keel writes the record in its own data folder. In keel's Docker
image agents run as their own user and can not read or change it. In native mode (`keel2 start --native`) agents run as
you, so they could; the hash check then shows a line changed by hand, but it is not a signature: someone who rewrites the
whole file can make a new chain.

## Always on

These rules never switch off:

- secret files (`.env`, SSH and TLS keys, `credentials.json`, `.npmrc` …),
- keel's own rule files (`.keel/config.yml`, `.keel/rules/`, `.keel/stacks/`),
- old migrations, force push, `--no-verify`,
- commits and history rewrites inside a flow,
- secrets and switched-off tests in an edit,
- keel's own sign-in keys,
- the repo's git settings: agents never change them (`git config` that writes, `git remote add`) and never write
  inside `.git/`. Reading them is fine.

## Secret files

An agent never reads, searches, prints, copies or changes a secret file:

```
 Read .env / cat .env / grep -r KEY .        refused
 ls -la .env, test -f .env, git status .env  fine: they only name it
```

Some honest uses are refused too (`source .env`, `docker compose --env-file .env`). Run those yourself. Add your own
secret files in `guards.protected`.

## You can switch off, or on

- **Off**: generated files, build output, `-x test`, new packages, reading a file twice, big-file reads, the push
  gate, MCP tools that change things. Saved in `.keel/config.yml` as `rules.off`.
- **On**: reading other projects (`other-projects`). It is off until you turn it on. Saved as `rules.on`.

## Your lists

The page edits your lists in `.keel/config.yml` in place: protected files, generated files, build output, shell
commands, `mcp.allow`, the big-file limit (`read_guard_max_lines`, default 2000 lines; 0 = off), the push gate, and
architecture boundaries.

## Rule packs

A **rule pack** is a set of your own rules in one YAML file in `.keel/rules/`. Commit it, and your team shares it.

- **Library** installs a ready pack: strict secrets, no new dependencies, migrations only in a flow, frontend
  can't touch the API, Spring, Node, Python.
- **New rule** writes one with a form, or as YAML.
- **Check and try it** says which of the project's recent tool calls the pack would have refused.
- **Export** and **Import** move a pack between projects. The Marketplace has rule packs too.

```yaml
schema: 1
name: no-deploys          # the id and the file name: .keel/rules/no-deploys.yaml
title: No deploys
version: 1.0.0
rules:
  - id: no-kubectl
    kind: bash            # bash | edit | read | mcp
    match: [kubectl apply]
    phases: all           # all | in-flow | none | [green, refactor]
    reason: Deploys are a person's job.
```

In keel TUI, `/create rule <what it should do>` lets KeelBot write one. keel checks it, and you save it.

## Which engine checks what

Each rule on the page shows one sign per engine:

| Sign | Means |
|---|---|
| ✓ | checked **before** the action: it never happens |
| ◐ | checked before for git, gh, the package managers and file printers only |
| ↺ | checked **after the step**: keel puts the file back |
| ✗ | not checked by this engine |

Claude, OpenCode, API-key models and Codex 0.150+ are checked before every action. Copilot's file edits are only
checked after each step.

## Rules now

**Rules now** on the Flow page shows, for the phase that runs: what agents may change (✓), what not (✗), what never
(🔒), your rule packs, which rules stopped something, and the last command an agent saw fail.

## What keel cannot stop

keel reads each command before it runs. It does not run agents as another user or in a sandbox. So a determined
agent can still get around a rule (for example with a file name built at run time). The strong fix: keep real
secrets out of the folders agents work in.

When keel cannot read the rules (an error in the hook), it fails closed: only reads pass, and `.env` files and
build output stay refused.
