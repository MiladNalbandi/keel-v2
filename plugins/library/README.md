# Library

Your company's rules live in many places. Put the ones agents should follow here. Agents search them, quote the words and
name the source. You decide what they may read.

```
 you add a document ──▶ Library (keel's database)
                          │
       the page's search  │  stays on this computer
                          │
       library_search ────┴─▶ an agent (only if the plugin is on for this project,
       library_read            and only documents marked "agents may read")
                                   │
                                   ▼ what it reads goes to your model company
```

## What goes to the model company

It says so on the page, too.

| | goes to the model company |
| --- | --- |
| A document marked "agents may read", when an agent's search finds it or it reads it | **yes** |
| A document marked "people only" | **never**: no agent tool returns it |
| Anything, in a project where the Library is off | **never** |
| A search on the page | **never**: it runs on this computer |

Do not put staff or customer data here unless your model company may have it. keel does not look for it.

## Who can read it

The plugin is off in each project until you turn it on (Settings › Plugins). Then these get its tools:

- **KeelBot** (try `/policy backups`),
- the **Answers** flow's drafter and checker, when that plugin is installed too,
- any other agent you give the tools on the Tools page.

Other flows' agents do not get it by themselves.

## What it is

- Plain text and Markdown. You paste it, or choose a text file. No PDF, Word or Drive.
- A document is for every project, or for one.
- A search matches words, on this computer, with no model. It does not understand other words for the same thing.
- A change of the words makes a new version number, and a quote names its version. Old versions are not kept.

## What it does not do

- It does not check that a document is true, or still valid. That is yours.
- It does not make an agent obey. An agent is told to quote it, and a gate is still where a person checks.
- keel has one person, so a document has no owner and no approver. Anyone signed in to this keel can change it.
