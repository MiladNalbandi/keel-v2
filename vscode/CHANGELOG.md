# Changelog

## 0.2.0

- Review mode: **Old | New (whole file)** opens a changed file side by side in VS Code's diff editor, the old file (the
  pull request's base) on the left and the new one on the right. From the editor's title bar, the hover over a changed
  block, a changed file in the Review view, or the command palette. A new file has an empty left side, a deleted one
  an empty right side; a renamed file shows its old path on the left.

## 0.1.0

- The keel sidebar: Flows, Inbox, Review, Changed files (in review mode) and KeelBot.
- A flow tab: steps, criteria, the gate form with exactly the offered choices, Stop, Resume, and the live activity
  (running agents, a feed of reads, edits, commands, tests, guard refusals and gates, with filters and auto-scroll).
- Start a flow (also from a selection).
- Review mode: changed lines marked in the normal editors, review threads with reply and resolve, Next change, Ask
  KeelBot what changed here.
- KeelBot chat with streaming answers; Ask KeelBot about selection (Ctrl+Alt+K / Cmd+Alt+K).
- The status bar and notifications for new questions.
- keel: Connect with keel2 on this computer, an API key or a pairing code (`platform: "vscode"`).
