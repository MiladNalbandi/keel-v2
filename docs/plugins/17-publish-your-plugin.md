# 17 · Publish your plugin: a guide for other publishers

This guide is for you if you want to make a keel plugin and put it in the keel marketplace. You do not need to work
for keel. The steps are the same for everyone.

A **plugin** is one file, `<name>-<version>.kplug`. It can add workflows, agents, KeelBot commands, pages, or code to
keel. People find it in **Settings › Plugins › Marketplace** and install it with one click. The design is in
[02-plugin-package.md](02-plugin-package.md) (the package), [03-security.md](03-security.md) (trust) and
[04-marketplace.md](04-marketplace.md) (the catalog).

## The path in one picture

```
 your computer                          your GitHub repo                     keel-studio/keel-marketplace
 ─────────────                          ────────────────                     ───────────────────────────
 1 keel-plugin new my-notes
 2 write it
   keel-plugin dev . ──▶ your own keel
   (unsigned, only for you)
 3 keel-plugin lint .
 4 keel-plugin keygen you
     you.key ─────────────────────────▶ Actions secret
     you.pub ────────────────────────────────────────────────────────────▶ publishers/you.yml
 5 git tag v0.1.0, git push ─────────▶ release workflow:
                                        pack, lint, sign
                                        release: my-notes-0.1.0.kplug
                                                 my-notes-0.1.0.kplug.minisig
 6 pull request ─────────────────────────────────────────────────────────▶ plugins/my-notes.yml
                                                                           check-pr: lint, signature, trust
 7                                                                         a maintainer merges
                                                                           sync (every hour): index.json, signed
 people: Settings › Plugins › Marketplace ◀──────────────────────────────── index.json
```

## Before you start

- **The tool.** `keel-plugin` is a small Python program. Run it with [uv](https://docs.astral.sh/uv/):

  ```
  uvx --from "git+https://github.com/MiladNalbandi/keel-v2@plugin-base#subdirectory=tools/keel-plugin" keel-plugin --help
  ```

  Tip: make a short name for it, for example `alias keel-plugin='uvx --from "git+…" keel-plugin'`.

- **A keel to try it in.** Start your own keel with `keel2 start` (it needs Docker).
- **A name.** Your plugin's name and your publisher id use `a-z`, `0-9` and `-`. They start with a letter. They have
  32 characters at most. Every name your plugin adds starts with the plugin's name.

## 1 · Make it: `keel-plugin new`

```
keel-plugin new my-notes --publisher you                     engine, web and content
keel-plugin new my-notes --publisher you --parts content     content only: the easiest way
```

With `--parts content` you get a plugin with only content, plus an example workflow and an example agent. keel loads
them as they are, so you can try the plugin at once.

| part          | what it is                                                               | trust level it gives |
| ------------- | ------------------------------------------------------------------------ | -------------------- |
| `content/`    | workflows, agents, KeelBot commands (text only: no code runs)            | content              |
| `web/`        | an ES module that adds pages or pieces of pages, in the person's browser | web                  |
| `engine/`     | a Python package that runs inside keel's engine                          | code                 |
| `api/`        | a Kotlin jar that runs inside keel's api                                 | code                 |
| `migrations/` | its own database tables                                                  | (with an api part)   |

You can also start from the template repo `keel-studio/keel-plugin-template`. It has the same files plus the CI and
release workflows (its files are in keel's `docs/plugins/template-repo/`).

### The content keel loads

| file                                | what keel does with it                                          |
| ----------------------------------- | --------------------------------------------------------------- |
| `content/workflows/<id>.yaml`       | a workflow: every project's Workflows page lists it             |
| `content/agents/<name>.md`          | an agent: workflows can use it, and it shows on the Agents page |
| `content/plugins/<name>/plugin.yml` | KeelBot commands (`/name`) and context files: text only         |

- keel's own names always win. A workflow or agent with one of keel's names (for example `feature` or `explorer`)
  never loads. `keel-plugin lint` tells you. Start every name with your plugin's name: `my-notes-flow`,
  `my-notes-agent`.
- A `plugin.yml` in a plugin is **text only**: commands and context. Its `tools`, `actions` and `installable` are not
  used. A command never replaces one of keel's commands.
- Not yet: skills and stacks in a plugin do not load. They stay in keel's own content for now.

## 2 · Try it: `keel-plugin dev`

```
keel-plugin dev .
```

It puts your plugin into **your own** running keel:

```
 1 build    (only with --keel-src, for a web or api part that needs a build)
 2 pack     my-notes-0.1.0.kplug, UNSIGNED, and lint it
 3 copy     docker cp into the keel container: /data/plugins/dev/
 4 install  POST /api/plugins/install-file  (as "Install from file" in Settings › Plugins)
 5 restart  POST /api/plugins/restart, then it waits until keel is back
 6 report   what loaded: the version and the parts, or why keel left it out
```

```
keel-plugin dev: . → http://127.0.0.1:8080 (container keel-v2)
pack     my-notes-0.1.0.kplug (6 files, trust content)
         unsigned: this .kplug has no signature. keel installed it as a file (source "file") …
copy     my-notes-0.1.0.kplug → keel-v2:/data/plugins/dev/my-notes-0.1.0.kplug
install  My Notes 0.1.0: installed from the file, it loads after a restart
restart  keel restarts (the same container, about 10 to 30 seconds)
loaded   My Notes 0.1.0 · parts: content · from file
         its workflows: my-notes-flow
         its agents: my-notes-agent
```

- **Unsigned.** The file has no signature. keel installs it as a file, as a person can in **Sources and rules**. This
  is only for your own keel. People get your plugin from the marketplace, signed.
- **Which keel.** `--keel http://127.0.0.1:8080` is the address (this is the default). `--container` is the Docker
  container; without it, keel-plugin uses `$KEEL_NAME`, else `keel-v2` (keel2's names).
- **Change and try again.** Run `keel-plugin dev .` again. The same version replaces the one keel has.
- **A web or api part** must be built first. Or give `--keel-src <a keel checkout>`: keel-plugin builds it with keel's
  own build (`scripts/build-plugin.sh`). It copies your plugin into `plugins/<name>/` of that checkout while it
  builds, and removes the copy after.
- **Remove it** in Settings › Plugins › Installed › Remove, then restart keel.

## 3 · Check it: `keel-plugin lint`

```
keel-plugin lint .
keel-plugin lint dist/my-notes-0.1.0.kplug
```

The marketplace runs the same checks before it lists a version. An **error** means keel or the catalog would refuse
it. A **warning** is worth a look; nothing is refused for it. Lint checks:

- **The manifest** (`keel-plugin.yml`): schema 1; the name; a version like `1.2.3`; title, publisher and summary;
  `requires.sdk: 1`; version ranges like `>=0.15.0`; every part path is inside the folder; the permissions keel
  knows, written the right way.
- **Names stay in the plugin's box**: actions start with `my-notes:`, events with `my-notes.`, agent tools and tables
  with `my_notes_`.
- **Files**: every part the manifest names is there; `files.sha256` matches every file; no links, no special files, no
  absolute paths, no `..`.
- **No Python libraries**: keel does not install them for a plugin yet (`requirements.lock`, `requires.python`).
- **The web part**: no `import()` from the internet, no `import … from "https://…"`, no `eval()`, no `new Function()`.
- **Content**: no workflow or agent with one of keel's own names.
- **The trust level** it prints: `content`, `web` or `code`.

## 4 · Your key: `keel-plugin keygen`

```
keel-plugin keygen you          you.key (secret, mode 0600) and you.pub (public)
```

- **The secret key** signs your releases. Put it into your repo's **Actions secrets**, with the name your release
  workflow reads (`PUBLISHER_KEY` in the template; keel's own repos use keel's `KEEL_PUBLISHER_KEY`):
  `gh secret set PUBLISHER_KEY < you.key`. Then keep the file in a safe place, offline. Never commit it, never
  paste it anywhere. (The template's `.gitignore` has `*.key`.) Commit the public key as `.github/publisher.pub`, so
  each release checks it signs with the key the marketplace knows.
- **The public key** is the second line of `you.pub` (it starts with `RW`). It goes into `publishers/you.yml`.
- **A new key** (you lost the old one, or someone saw it): make a new pair, and open a pull request that changes
  `keys` in `publishers/you.yml`. A maintainer checks with you first.

## 5 · Release it: a tag

1. Write the new version in `keel-plugin.yml` (and in your engine part's `VERSION`, if it has one). Commit.
2. `git tag v0.1.0 && git push origin v0.1.0`
3. The release workflow builds, packs, lints and signs. It makes a GitHub release with two files:
   `my-notes-0.1.0.kplug` and `my-notes-0.1.0.kplug.minisig`. It refuses a tag that is not the manifest's version.

Without CI you can do it by hand:

```
keel-plugin pack . --out dist
keel-plugin sign dist/my-notes-0.1.0.kplug --key-file you.key
gh release create v0.1.0 dist/my-notes-0.1.0.kplug dist/my-notes-0.1.0.kplug.minisig
```

Your repo must be **public**: the marketplace reads its releases. Keep the old releases: people may roll back.

## 6 · Ask to be listed: a pull request

Open a pull request to [`keel-studio/keel-marketplace`](https://github.com/keel-studio/keel-marketplace) with two
files. The marketplace holds no code: only these pointers.

`publishers/you.yml` (once):

```yaml
name: you
title: Your Name
verified: false # a maintainer changes this, after a review (below)
contact: https://github.com/you
keys: [RWQ…] # the public key line of you.pub
```

`plugins/my-notes.yml` (once per plugin):

```yaml
name: my-notes
title: My notes
publisher: you
repo: https://github.com/you/keel-plugin-my-notes
category: knowledge # code, review, knowledge, tickets, product or other
summary: Writes release notes from the merged pull requests.
tags: [notes, release]
trust: content # content, web or code
```

The check `check-pr` runs on your pull request:

```
 the files are valid ─▶ download your newest release ─▶ lint it ─▶ check its signature with your key
   ─▶ build a test index (never signed) ─▶ say what keel will do with each plugin
```

- It **fails** when a file is wrong, there is no release yet, lint finds an error, or the signature is not from a key
  in `publishers/you.yml`.
- It writes a **notice** for each plugin: its trust level, and if the publisher is verified. For example:
  `my-notes: trust content, publisher you (community, not verified): keel installs it`.
- It **warns** the maintainer when a pull request sets `verified: true` or changes the keys of a publisher that is
  there already.
- If your package does more than the `trust` you wrote (for example `content`, but it has an engine part), the index
  lists the higher level. keel also checks every package it downloads: a package that does more than its listing is
  refused.

A maintainer reads your pull request and merges it. Within an hour the marketplace lists your plugin.

## Trust levels: what people see, what keel does

```
   less risk ◀────────────────────────────────────────────────────────────▶ more risk
   CONTENT                           WEB                                CODE
   workflows, agents, commands       + pages, in the person's browser   + engine or api code inside keel
   no code runs                      it can call keel's api as the       it has keel's rights: files,
                                     person                              secrets, network
```

| trust   | verified publisher | community publisher (not verified)                                                       |
| ------- | ------------------ | ---------------------------------------------------------------------------------------- |
| content | installs           | installs                                                                                 |
| web     | installs           | listed as **unverified**; keel installs it only when "Allow unverified publishers" is on |
| code    | installs           | listed as **unverified**; keel installs it only when "Allow unverified publishers" is on |

"Allow unverified publishers" is a rule in Settings › Plugins › Sources and rules. It is **off** by default. Every
install shows the trust level and the permissions first. A person always says yes; an agent can only ask.

## What a plugin may never do

keel checks these when it installs and when it starts. The marketplace checks them again (lint and review).

1. Run anything at install time. Installing only unpacks files.
2. Replace a core file, or a core workflow, agent, skill or route. A plugin with the same name does not win.
3. Use names outside its own box (actions, events, agent tools, tables, routes start with its name).
4. Remove or weaken guard rules or gates. A plugin may **add** a rule, never remove one.
5. Read secrets of a kind it did not list, or keel's own secrets (provider keys, `master.key`).
6. Write outside its own data folder (`/data/plugins/data/<name>/`) and the project (only with `workspace: write`).
7. Install, turn on or update another plugin. Only a person does that.
8. Load code from the internet at run time. A web part loads only files from its own folder.
9. Give an agent an "install" tool. Agents can only **ask** for a plugin.

keel enforces 1, 2, 3, 7 and 9 in its code. keel's SDK enforces 5, 6 and 8 for code that uses the SDK. Code can go
around the SDK, so for a code plugin the **review** (below) is the real check. Read [03-security.md](03-security.md)
for the details.

## Become verified

A verified publisher's web and code plugins install without the rule "Allow unverified publishers". So keel asks for
a review first. To ask, open an issue in `keel-studio/keel-marketplace`: "Verify <your publisher id>", with a link to
your plugin repo and one release.

**The review checklist** (a maintainer does it; you can check it yourself first):

1. **Who you are.** A real person or company, with a GitHub account that has a history, and a way to reach you
   (`contact`). You prove the key is yours: the maintainer sends you a short text, you sign it
   (`keel-plugin sign text.txt --key-file you.key`), you send back `text.txt.minisig`, and the maintainer checks it
   with the key in your `publishers/you.yml`: `keel-plugin verify text.txt --pub RWQ…`.
2. **A code read of one release.** The maintainer downloads one `.kplug` and reads every file: engine, api, web and
   content. The code uses only keel's plugin SDK, not keel's inside parts. It does nothing at install time. It calls
   only the hosts its permissions name. It reads only the secrets it lists. It writes only its own data folder (and
   the project, with `workspace: write`). Nothing is hidden: no packed or obfuscated code, no code from the internet.
3. **Lint is clean.** `keel-plugin lint` has no errors on that release, and each warning has a reason.
4. **The permissions match the code.** Every permission in `keel-plugin.yml` is used, and the code needs none
   that is not listed.
5. **Names stay in the box.** Actions, events, agent tools, tables and routes start with the plugin's name.

When all five are fine, a maintainer sets `verified: true` in `publishers/you.yml`, in a separate pull request. Later
versions are listed without a new review. But a maintainer can look at any version. A version that breaks the rules
is revoked, and the publisher can lose `verified`.

## New versions, and when something goes wrong

- **A new version:** change the version, tag it. The marketplace lists it within an hour. keel shows "Update"; a person
  says yes.
- **More permissions:** when a new version asks for more, keel does not update by itself. It asks the person in the
  Inbox ("Update My notes to 0.2.0? + secrets: gitlab").
- **A bad version:** tell the maintainers at once (an issue, or your `contact`). They add it to `revoked.yml`. keel
  never installs a revoked version, and it warns people who have it.

## Commands, short

| command                                                                  | what it does                                 |
| ------------------------------------------------------------------------ | -------------------------------------------- |
| `keel-plugin new <name> [--parts content]`                               | a new plugin folder                          |
| `keel-plugin dev <dir> [--keel URL] [--container NAME] [--keel-src DIR]` | try it in your own keel (unsigned)           |
| `keel-plugin lint <dir or .kplug>`                                       | the checks keel and the marketplace run      |
| `keel-plugin pack <dir> --out dist`                                      | make `<name>-<version>.kplug`                |
| `keel-plugin keygen <you>`                                               | a key pair: `you.key` (secret) and `you.pub` |
| `keel-plugin sign <file> --key-file you.key`                             | `<file>.minisig`                             |
| `keel-plugin verify <file> --pub <key>`                                  | check a signature                            |
