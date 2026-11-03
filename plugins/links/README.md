# Links

Links keel to other systems your team uses. The first one is **Linear**. Agents and KeelBot can read its issues. A comment or a
new issue happens only after your OK.

```
 agent: links_search, links_issue ──────────────────▶ Linear (reads, with your key)

 agent: links_comment, links_create_issue
        │
        ▼
 keel's Inbox: "May keel comment on ENG-7?" + the exact words
        │ you: yes                    │ you: no, or nobody answers in 10 minutes
        ▼                             ▼
 Linear gets it                 nothing is written, the agent hears why
```

## Set it up

1. Settings › Connections › **Linear**: paste a personal API key. keel asks Linear who the key is, and keeps it only if Linear takes it.
2. Settings › Plugins: turn **Links** on for the project.

## What goes where

It says so on the page, too.

| | where it goes |
| --- | --- |
| Issue titles, descriptions and comments an agent reads | **your model company** |
| People's names | not asked for, so they do not go, unless somebody wrote them in the text |
| Your key | only to Linear, over https. It is encrypted in keel and in the engine's memory only for an agent call |
| A comment or a new issue | **Linear, after your OK** |

Text in an issue was written by other people, and it can try to tell an agent what to do. Agents are told it is data. Your OK is the
real protection for writes. Reads have no OK, so do not link a Linear workspace whose text may not go to your model company.

## What it does not do

- Only Linear so far. Slack, Drive and others are not built. Each system takes its own client and tests (`linear.py` is the example).
- Only a comment and a new issue. It does not change an issue's state, or close or delete anything.
- The Inbox card is keel's shared one, so it also offers "Always for this command" and names KeelBot. Here Always means yes this once: keel
  remembers nothing and the next write asks again.
- One key for this keel, because keel has one person. Linear sees all writes as that key's owner, not as who asked.
- An agent gets the tools only with the plugin on for the project, and when the agent is allowed them (KeelBot always; others on the Tools
  page).
- **It has never run against the real Linear.** The tests use a stand-in. The queries follow Linear's public API from memory of its schema;
  the first real use may need a fix.
