# keel for JetBrains IDEs

The keel plugin for IntelliJ IDEA, PyCharm, WebStorm, GoLand and the other IntelliJ-based IDEs (2024.3 and newer):
review pull request threads inside the editor, answer the questions your flows wait at, start a flow from a
selection, and chat with KeelBot. Kotlin, Swing and the IntelliJ Platform's own components (Kotlin UI DSL, JBColor:
light and dark themes follow the IDE).

## Connect

Settings › Tools › keel:

- **keel address**: `http://localhost:8088` by default (a Tailscale or tunnel address works too).
- **Use keel2 on this computer**: the plugin runs `keel2 auth key` (from the PATH, `~/.local/bin`, `/usr/local/bin` or
  `/opt/homebrew/bin`) and sends the key as `Authorization: Bearer <key>`. The key stays in memory; it is read again
  when keel stops taking it (after `keel2 auth reset`).
- **Pair with a code**: in keel, Settings › Devices › Pair a phone shows a one-time code. The plugin calls
  `POST /api/devices/pair {code, name: "<IDE> on <host>", platform: "jetbrains"}` and keeps the device token in the
  IDE's PasswordSafe (the OS keychain or KeePass), never in settings files or logs. Removing the device in keel signs
  the plugin out.
- **keel project**: keel usually runs in Docker, so its project root and the IDE's folder can differ. The plugin
  matches the whole path, then the folder name, then the project name; pick the project by hand when none matches.

## What is where

| Where | What | keel api |
|---|---|---|
| Tool window › Review | PRs, one PR (Submit review / Approve / Check out), Changes / Threads / Pending / Overview | `GET …/review/prs`, `…/review/view`, `POST …/review/submit`, `DELETE …/review/drafts/{id}` |
| A PR file (editor) | the diff with the threads between the lines; Reply, ✦ Is the reviewer right?, ✦ Address it, Resolve; editor menu › keel: Comment on This Line | `GET …/review/diff`, `POST …/threads/{id}/reply`, `…/resolve`, `…/judge` + `GET …/review/ai`, `…/address`, `POST …/review/drafts` |
| Tool window › Flows | flows; a flow's question (on top), steps, live activity (Now: the step and each running agent with model, effort, time, tokens/cost; a feed of reads, edits with +/−, commands with exit code and output, test counts, guard refusals, gates; filter All / Edits / Commands / Tests, pause auto-scroll, Copy log), criteria | `GET …/runs`, `…/flows/{tid}`, `GET /api/jobs?project=` + `/api/jobs/{call}/steps` (a flow opened mid-run), the live stream's `agent.*`, `step.*`, `tool.ran`, `guard.refused`, `gate.*`, `POST /api/inbox/{tid}/act` (409: it asks something else now), `/api/threads/{tid}/stop`, `…/history` + `…/rewind` |
| Balloon, status bar | "<flow> waits for you" (Answer / Open / Later), KeelBot's command approvals, "keel · N questions" | `GET /api/inbox`, `POST /api/approvals/{id}/decide`, `GET /api/events` (SSE; a 20 s poll when it is down) |
| Start a flow (Tools › keel, Flows tab, editor menu, Alt+Enter on a selection) | workflow, title, request, where, model + effort, run mode, cap | `GET …/workflows`, `GET /api/providers/models`, `POST …/flows` |
| Tool window › KeelBot | Ask / Fix / Edit, model chip, tool log, file:line links, changed files with Diff / Undo / Done | `…/helper/sessions` (`GET`, `POST`, `PATCH {mode}` / `{model}`), `…/turn`, `…/stop`, `…/changes`, `…/undo`, `…/done`, `GET /api/jobs/{call}/steps` |
| Editor | Ask KeelBot on a selection (**Ctrl+Alt+K**; rebind it in Keymap › Plugins › keel), "KeelBot changed lines a–b · Keep / Undo / Diff" | the same, plus `git diff -U0 HEAD` for the lines |

## Review mode

Choosing a PR in the Review tab (or Check out) turns review mode on for that PR, per project; it ends with Exit review
mode or when another PR is chosen, and it stays while files are closed and opened again.

- **Toolbar pill and status bar**: "Reviewing #7 · main ← feat/paging"; "Reviewing #7 · 5 files · 1 thread waits ·
  Exit review mode" (a click: Exit review mode, Next change ↓, the Review tab). Each changed file has a banner with
  Next change ↓ and Exit review mode.
- **Project view**: ✚ new (green), ● changed (blue) after the file name, which keeps the IDE's own VCS colour, plus the
  file's thread count and ✓ once viewed. Deleted files are in the Review tab's list (✕, struck through).
- **Editors**: the PR's changed lines vs its base in a gutter layer of their own, beside the IDE's local-change marks:
  a green bar for added lines, a blue bar for modified ones, a red ▶ where lines were removed ("changed in PR #7").
  A click shows the old text with **Ask KeelBot what changed here** (the answer appears in a small card under the
  block, with Open in KeelBot), **Comment on this change** (a pending comment), **Show old** (the PR diff) and
  **Old | New** (0.4.0: the whole file side by side in the IDE's diff viewer, the base on the left; also Tools › keel ›
  PR File: Old | New). The
  same two actions are in the editor menu on a changed block. Review threads sit between the lines (folded); outdated
  ones and comments on removed lines are a note at the top of the file.
- **Review tab**: the PR's +/−, files and threads; Changed files (N) (a click opens the normal file, a deleted one its
  diff); Mark a file viewed when I open it; Next change ↓; Submit review.

The changed blocks come from keel's review diff of each file; when keel's diff fails or is cut short and the PR
branch is checked out, from `git diff origin/<base>...HEAD -- <file>`. When the branch is not checked out, the marks
follow the PR's head lines, which can differ from the file on disk: check the PR out to review it in place.

Model and effort in Start a flow go along as flow options (`options.provider/mode/model/effort`); keel's start route
has no model field yet, so the workflow's agents keep their own models until a workflow reads them.

## Build and test

```sh
cd jetbrains
./gradlew test           # JVM tests (pairing, Bearer, inbox, gate answers + 409, review threads, KeelBot, mapping, keel2 key) and headless IDE tests (panels, gate, editor threads, dialog, settings)
./gradlew buildPlugin    # build/distributions/keel-jetbrains-<version>.zip
./gradlew verifyPlugin   # the JetBrains plugin verifier against IntelliJ IDEA Community 2024.3
```

Install the zip with Settings › Plugins › ⚙ › Install Plugin from Disk. The first build downloads IntelliJ IDEA
Community 2024.3 (about 1 GB) into Gradle's cache.

## Code

```
src/main/kotlin/dev/keel/jetbrains/
  api/       KeelApi (OkHttp + kotlinx.serialization), Models, GateForm, LiveHub (SSE) and Logic: the Android app's
             client without Android; ProjectMapper, LocalKey (keel2 auth key), IdeLogic. No IDE classes: unit-tested.
  core/      KeelConnection (sign-in), KeelProjectService (mapping, inbox, live stream), ReviewSession, LocalGit
  settings/  KeelSettings, KeelProjectSettings, KeelCredentials (PasswordSafe), KeelConfigurable
  ui/        the tool window (ReviewPanel, FlowsPanel + GatePanel, KeelBotPanel), StartFlowDialog, balloons, status bar
  editor/    PrDiffEditor, ThreadComponent, ComponentInlays (Swing between editor lines), KeelBotMarks
  actions/   Tools menu, editor menu, intentions
```
