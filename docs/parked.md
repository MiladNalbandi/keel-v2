# Parked parts of keel

keel focuses on one job: **run coding agents on any model, and make what they did easy to read before anything lands.**
The parts below do not serve that job directly, so they are **parked** (0.37.1 and 0.38, #194 and #196).

**Parked means:**

- no new features;
- off until a person turns it on (a new keel, or a new project for a per-project plugin);
- still in every keel, still built and tested in CI, and a bug that breaks it or a security problem is still fixed;
- nothing is deleted: its data stays, and one click turns it on again.

```
 parked plugin:  new keel / new project ──▶ off        Settings › Plugins ──▶ [ turn on ] ──▶ works as before
                 new features ──▶ none                  bugs / security ──▶ still fixed
```

## Parked plugins, off for the whole keel

They start off in every keel (`starts_off: true` in their `keel-plugin.yml`). A keel that had one turned on by hand
keeps it on; otherwise it is off after the update. Turn one on in **Settings › Plugins** (keel restarts once).

| Plugin | What it does |
|---|---|
| `org` | an org of agents, missions for a role |
| `goals` | one goal split into flows |
| `answers` | answers to a customer's or auditor's questions |
| `desk` | support requests sorted into flows or tasks |
| `map` | the project's structure, level by level |
| `record` | one page and export per flow |
| `slack` | keel in Slack |
| `notify` | chat or mail messages when a flow waits |
| `product` | keel Product (beta) |

## Parked plugins, off per project

They were already off for every project until turned on there; that stays.

| Plugin | What it does |
|---|---|
| `library` | company policies agents can search |
| `links` | Linear |
| `supervisor` | a model that answers gates for you |

## Parked front ends

keel TUI (`keel2`, `keel2 tui`), the VS Code extension, the Android app, the xal plugin and the Muxy hooks keep working,
with no new features. New features land in the web app (and keel desktop) and the JetBrains plugin.

## The core

Flows with gates, the harness and its model providers, rules, guard and cost limits, KeelBot, Code Review, the Inbox,
Tasks with Jira and GitHub Issues, Git, Code, the code graph, the Wiki, **Database**, **CI/CD** and **Cluster**.
