# Answers

A customer or an auditor sends you a list of questions: how you store passwords, who can read the logs, how you do
backups. Answers drafts a reply to each question from **your own code and docs**, shows the source, and lets you decide.

## How it works

```
 you paste the questions when you start the flow
        |
        v
  answers-reader     splits them into one question each
        |
        v
  answers-drafter    one agent per question: reads the repo, writes a draft, names the file and line it used
        |
        v
  answers-checker    another agent opens each source and says if it really says that
        |
        v
  YOU, one gate per answer:   approve  |  change (your note says how)  |  drop (say why)
        |
        v
  answers-writer     puts the approved answer in docs/answers/<name>.md
        |
        v
  YOU, one last look at the whole document (the real diff), then keel commits it on the flow's own branch
```

Nothing goes to the customer from keel. keel never pushes this branch and never sends mail. You take the document and
send it yourself.

## Rules it keeps

- **A person approves each answer.** Every approval gate has `person: true`: keel's run modes (`important`, `auto`) and
  the Supervisor plugin never answer it. This is a small change in keel's core (0.20.2), so the plugin needs that keel: an
  older keel leaves the plugin out and says why, so it never runs without the rule.
- **An MCP client cannot answer it either**, unless you gave it the admin level (`keel2 mcp --admin`). A client with the write level is refused.
- **Each answer shows its source.** A draft names a file and line for each fact (or `library:<id>`, with the Library plugin on). When the repo does not answer a
  question, the draft says so and does not guess.
- **The check does not decide.** The checker's result is shown to you. Its "look closer" only warns you.

## What it does not do

- It is not legal advice and it does not make you compliant with anything. A wrong answer can become a promise to a
  customer, which is why you read each one.
- It reads only the project: what is in the repo goes to your model company as agents read it. Do not start it on a repo
  with data that may not go there.
- keel has one person (see the guide, "Signing in"). "A person" here means you, whoever signs in to this keel. keel does
  not record a different name for each approver, and it has no way to require two people.

## Use it

Workflows › **answer a questionnaire** › Start. Paste the questions into the request, or name a file in the repo that
holds them.
