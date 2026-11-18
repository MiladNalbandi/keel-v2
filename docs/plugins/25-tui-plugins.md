# 25 · A plugin in keel TUI (`contributes.tui`)

> **Parked (0.37.1):** keel TUI keeps working and gets its fixes, but no new features: they land in the web app (and keel desktop) and the IDE plugins (#194). A new plugin does not need a `contributes.tui` part; the existing ones stay and keep working.

keel 0.18.3. A plugin can add **commands**, one **panel** and (0.18.6) **modes** to keel TUI (keel's terminal app, `keel2 tui`). They are
written as data in the plugin's `keel-plugin.yml`, under `contributes.tui`. keel TUI never runs a plugin's code: each
command calls the plugin's own api routes and shows the answer the way the manifest says.

```
keel-plugin.yml                     keel's api                                   keel TUI
contributes.tui ──▶ GET /api/tui/plugins?project=<id>  (checked again here) ──▶  /git status · /git log · /git diff
                                                                                 Ctrl+G: the Branch panel
/git status ─────▶ GET /api/projects/<id>/git/status  (the plugin's own route) ──▶ "keel/health · base main · ahead 2"
                                                                                  "M  src/app.ts"
```

## The Git plugin's (plugins/git/keel-plugin.yml)

```yaml
contributes:
  tui:
    commands:
      - name: git
        args: "<status|log|diff [path]>"
        summary: the branch and its changes, its last commits, or the uncommitted diff
        sub:
          status:
            summary: the branch, how far from its base, and the files that changed
            call: { path: "/projects/{pid}/git/status" }
            show: { title: "{branch} · base {base} · ahead {ahead} · behind {behind}", items: changes, line: "{status}  {path}", empty: "nothing changed" }
          log:
            summary: the last commits on this branch
            call: { path: "/projects/{pid}/git/log" }
            show: { items: ".", line: "{sha}  {date}  {author}  {subject}", max: 20 }
          diff:
            summary: the uncommitted changes as a diff (one file with a path)
            call: { path: "/projects/{pid}/git/diff?path={arg}" }
            show: { diff: diff, empty: "no uncommitted change" }
    panel:
      title: Branch
      key: g
      every: 10
      call: { path: "/projects/{pid}/git/status" }
      show: { title: "{branch} · base {base} · ↑{ahead} ↓{behind}", items: changes, line: "{status}  {path}", empty: "clean", max: 6 }
```

## The fields

| Field | What it is |
|-------|------------|
| `commands[].name` | the command: the plugin's name (`git`) or `<name>-<word>` (`git-pr`). keel's own commands win: a plugin cannot replace `/status` |
| `args`, `summary` | what `/help` and Tab show (help lists plugins' commands under **Plugins**) |
| `sub` | subcommands by their first word (`/git status`); or, instead, one `call` (+ `show`, `confirm`) for the whole command |
| `call.path` | an api path under `/api`: **only the plugin's own routes**, `/projects/{pid}/<name>/…` (or `/<name>/…`). `{pid}` is the project, `{arg}` the person's words after the (sub)command; both are URL-encoded |
| `call.method` | `GET` (the default) or `POST` with `body`. A `POST` changes something, so it **must** have `confirm`: keel TUI asks that question (amber, yes/no) before it calls |
| `show.title` | a template for the first line: `{field}` of the answer, `{a.b}` for a nested one |
| `show.items`, `show.line` | the answer's list (a field, or `.` when the answer is the list) and the template of each line; `max` cuts it, `empty` is said when it is empty |
| `show.diff` | a field holding a unified diff: shown with line numbers, green and red |
| `panel` | one panel: `title`, a `key` (Ctrl+<key>: one letter, not keel TUI's own a, b, c, f, u), `every` (5–3600 seconds), a `GET` call and a `show` |

Without `show`, keel TUI prints the answer as JSON.

## Modes (keel 0.18.6, `contributes.tui.modes`)

A **mode** is a full-screen view of a plugin's own in keel TUI. `/<name> [words]` opens it; it takes the screen between
keel's header and keel's status line until **Esc**. It is data too: a list, a detail for each item, and keys. The Code
Review plugin's review mode is the example (plugins/review/keel-plugin.yml):

```
 [ keel ] v2 0.18.6 · shop · feat/paging                           ← keel's header stays
 ────────────────────────────────────────────────────────
  Code Review   src/Prefs.kt  +6 −1  (1/2)                         ← the mode's title, where you are
       @@ -1,3 +1,8 @@
     1  class Prefs {                                              ← the detail: the item's diff with line numbers
 ▸   2 -    fun pageSize(user: String) = 20                        ← the cursor (↑↓)
     2 +    private val cache = mutableMapOf<String, Int>()
     3 +    fun savePageSize(user: String, size: Int) {
       │ bo: Should 20 come from config? (1 comment(s), …)         ← notes under their lines (threads, drafts, findings)
 Save the page size · feat/paging → main · +9 −1 · 1 draft(s)      ← the mode's status line (a template)
 ↑↓ line · ][ file · }{ change · c r x v e f o F s p g · ? keys   ← its keys (? lists them with their words)
 Your comment on src/Prefs.kt:3: █                                 ← what it asks, or what happened
  ● 1 waits · KeelBot idle · …                                    ← keel's status line stays
```

```yaml
contributes:
  tui:
    commands:                       # a command with the mode's name has sub: its words run outside the mode
      - name: review
        summary: …
        sub: { prs: { summary: …, call: { path: "/projects/{pid}/review/prs?filter=all" }, show: { … } } }
    modes:
      - name: review                # /review [words]: other words than the command's open the mode
        title: Code Review
        args: "[<pr number>|<branch>]"
        summary: review the branch you are on or a pull request, in keel TUI's whole screen
        state:                      # GET calls, read in order when the mode opens (and after `reload`)
          view: { path: "/projects/{pid}/review/open?ref={arg}" }
          ai: { path: "/projects/{pid}/review/ai?key={view.key}" }      # a later call may use an earlier answer
        status: "{view.title} · {view.branch} → {view.base} · {view.drafts.length} draft(s)"
        list:
          title: "{view.files.length} file(s) changed"
          items: view.files
          line: "{status}  {path}  +{added} −{removed}"
          mark: { list: view.viewed, field: path, text: "✓ viewed" }
        detail:
          title: "{item.path}"
          call: { path: "/projects/{pid}/review/diff?key={view.key}&path={item.path}" }
          diff: diff                # or text: "<a template>"
          notes:
            - { items: view.threads, side: side, text: "{comments.0.author}: {comments.0.body}", tone: accent }
        keys:
          - key: c
            label: comment
            view: detail
            ask: "Your comment on {item.path}:{line}"
            call: { method: POST, path: "/projects/{pid}/review/drafts", body: { key: "{view.key}", path: "{item.path}", line: "{line}", side: "{side}", body: "{text}" } }
            reload: true
            done: "draft saved"
          - key: s
            label: submit
            pick: { title: "Submit your review as", choices: [{ label: Approve, value: APPROVE }, { label: Comment, value: COMMENT }] }
            ask: "A comment with your review (Enter for none)"
            confirm: "Send \"{choice.label}\" on #{view.number}?"
            call: { method: POST, path: "/projects/{pid}/review/submit", body: { key: "{view.key}", event: "{choice.value}", body: "{text}" } }
          - key: p
            label: pull requests
            pick: { title: "Open which pull request?", call: { path: "/projects/{pid}/review/prs?filter=all" }, items: prs, line: "#{number}  {title}" }
            open: "{choice.number}"
```

### What a template can read

| Name | What it is |
|------|------------|
| `{pid}`, `{arg}` | the project, the words after `/<name>` |
| `{<state>.…}` | a state call's answer (`{view.key}`, `{view.files.length}`, `{comments.0.body}`: a list's item by its number) |
| `{item.…}` | the item the cursor is on (in the list and in its detail) |
| `{line}`, `{side}` | in a diff: the line's number and `RIGHT` (the new file) or `LEFT` (a removed line, the old file's number) |
| `{choice.…}` | what the person picked (a choice is `{label, value}`) |
| `{text}` | what the person typed for `ask` |
| `{answer.…}` | the key's call's answer (in `done`) |

In a path every value is URL-encoded, and keel TUI refuses a value `.` or `..` and a path that is no longer under the
plugin's own routes. In a body a text that is exactly one `{name}` keeps its value as it is (a number, `null`, a list), so
`line: "{line}"` sends a number.

### A key, step by step

```
 press c ──▶ pick? ──▶ ask? ──▶ confirm? ──▶ call ──▶ show? / reload? / open? ──▶ done
             (a list    (a line   (y / n)     (GET or   (a text  (the state (the mode   (a line in the
              to choose  of text)              POST)     view)    again)     anew)      mode's message)
              from)
```

- `pick`: the items at `items` of its GET `call`'s answer, or (without a call) of the mode's state, kept when every
  `match` field equals its template (`match: { path: "{item.path}" }`), one `line` each; or fixed `choices`.
- `view`: where the key works: `list`, `detail` or `both` (the default). A key that uses `{line}` waits until the cursor
  is on a line of the diff.
- `show` (a `show` as for commands, plus `text`): the answer in a text view (↑↓ scroll it, Esc goes back).

### The mode's own keys

↑↓ (and j / k) move, Enter (or →) opens the item, ← or Esc goes back (Esc in the list leaves the mode), `]` `[` (and Tab)
the next and previous item while one is open, `}` `{` the next and previous change in a diff, PgUp / PgDn a page, Home /
End, `?` every key with its words, `q` leaves. The wheel scrolls and a click opens an item or moves to a line (the full
screen with the mouse on). A question KeelBot asks meanwhile shows under the mode and is answered first.

### The rules (checked like the commands')

| Rule | Why |
|------|-----|
| the name is the plugin's (`review`) or `<plugin>-<word>`; at most 4 modes | like commands |
| `title` (≤ 40) and `summary` (≤ 120) | `/help` and Tab show them |
| `state`: 1 to 5 GET calls; a state's name is a word and not one of the template names above | the mode reads, a key acts |
| `list` has `items` and `line`; `detail` is a GET call with `diff` or `text`; at most 5 kinds of `notes`, `tone` one of accent, dim, warn, bad, ok | |
| every call is one of the plugin's own routes | the same as a command's |
| a key's POST has `confirm` (a yes/no) or `ask` (the person's own words, Esc cancels) | nothing changes without the person |
| a key is one letter or digit, not `j`, `k` or `q`, never twice; at most 20 keys; `label` ≤ 24 | the mode's own keys stay its own |
| a command with the mode's name has `sub` | its words run as they are; any other words open the mode |

## Trust

`contributes.tui` is data, so it needs no more trust than the plugin already has: the routes it calls are the plugin's
own, served by keel's api with keel's checks (the plugin must be on for the project, the person's sign-in key). The rules
are checked three times: `keel-plugin lint` (tools/keel-plugin, `_tui`), keel's api when it lists them
(`keel.api.tui.TuiPluginService`: a wrong entry is left out, `problems` says why), and keel TUI (a name keel has wins).
Code in the TUI process from a plugin would need keel's signed, trusted-publisher rules (03-security.md) and is not
supported.

## Where it is

- keel TUI's modes (0.18.6): `cli/src/core/modes.ts` (`ModeSession`: the state, the keys, the lines; no UI) and
  `cli/src/ui/ModeView.tsx`; the api's check `TuiPluginService.mode`, lint's `_tui_mode`; tests `cli/test/modes.test.tsx`
  (on `cli/test/review-tui.json`, the review plugin's `contributes.tui`, kept equal by its engine tests), `TuiPluginsApiTest`,
  `tools/keel-plugin/tests/test_lint_tui.py`

- api: `GET /api/tui/plugins?project=<id>` → `{plugins: [{name, title, version, commands, panel}], problems: [{plugin, error}]}`
  (a per-project plugin only where it is on). `api/src/main/kotlin/keel/api/tui/TuiPlugins.kt`, `TuiPluginsApiTest`
- lint: `tools/keel-plugin/keel_plugin/manifest.py` `_tui`, `tests/test_lint_tui.py`
- keel TUI: `cli/src/core/plugins.ts` (the commands, the templates, the panel), `cli/src/ui/parts.tsx` `PluginPanel`
- the Git plugin's routes it uses: `/git/status`, `/git/log` and (new in 0.18.3) `/git/diff?path=`
