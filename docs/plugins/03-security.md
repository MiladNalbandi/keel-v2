# 3 · Security: trust, signatures, permissions

A plugin can add code to keel. keel runs agents on your code with your tokens. So a bad plugin is a real danger. This
page says how keel decides what to trust, what it shows before install, and what a plugin may never do.

We say this plainly: **a plugin that runs code inside keel is trusted like keel itself.** In the JVM and in Python
there is no cheap sandbox. So the main protection is **who** made the plugin (signatures, a reviewed catalog), plus
clear limits that keel checks wherever it can.

## 3.1 Three trust levels

```
   less risk ◀──────────────────────────────────────────────────────────────▶ more risk

   CONTENT ONLY                     ADDS PAGES                      RUNS CODE IN KEEL
   workflows, agents, skills,       + a web part (JavaScript         + engine (Python) or
   stacks, commands                 in your browser)                 api (Kotlin) code
   ─────────────────────────        ─────────────────────────        ─────────────────────────
   no code runs                     runs as you in the browser:      runs inside keel's
   agents still obey guard          it can call keel's api as you    processes with keel's rights
   and gates                                                         (files, secrets, network)

   anyone may publish               verified publishers, or          verified publishers;
                                    "unverified" warning             unverified = off by default
```

The marketplace and the install dialog show the level on every plugin. The rule "Allow unverified publishers" is
**off** by default. When it is off, keel installs only plugins whose publisher key is in a catalog keel trusts.

## 3.2 Signatures: is this file really from that publisher?

```
  publisher's repo (GitHub Actions, on a tag)                 keel (install)
  ─────────────────────────────────────────────               ───────────────────────────────────────────
  build db-1.4.0.kplug
  sha256 = 9c41…e07a
  sign with the publisher's private key ──▶ .sig              1 sha256 of the download == catalog's sha256?
     (the key is a GitHub Actions secret,                     2 .sig verifies with the publisher's public key?
      never in the repo, never in the image)                  3 that public key is listed for this publisher
  release: .kplug + .sig + keel-plugin.yml                       in a catalog index keel trusts?
                                                              4 the catalog index itself verifies with the
  marketplace repo (GitHub Actions)                              catalog key built into keel?
  ───────────────────────────────────                          all yes → unpack.   any no → stop, say which.
  reads the release, checks 1-3, runs lint,
  adds the version to index.json,
  signs index.json with the catalog key ──▶ index.json.sig
```

- **Algorithm:** Ed25519 (the minisign format). JDK 21 can verify it with no extra library (`Signature.getInstance("Ed25519")`).
- **Trust roots in the image:** only **public** keys: the official catalog key and the keel publisher key. Public keys
  are safe to ship.
- **Private keys and tokens never go into the image or a repo.** The publisher key lives in each plugin repo's GitHub
  Actions secrets. The catalog key lives in the marketplace repo's secrets. A private catalog or private plugin repo is
  read with a token from keel's **Connections** (stored encrypted in `/data`, as today).
- **The index expires.** `index.json` has an `expires` date (14 days). keel warns when its copy is too old. This stops
  an attacker from freezing you on an old, bad index.
- **Revoked versions.** The index has a `revoked` list (for example a version with a security bug). keel shows a red
  warning on that plugin and offers the fixed version. It does not remove it by itself.
- **Files are checked again at every start** (`files.sha256`). If someone changed a file in `/data/plugins`, keel leaves
  that plugin out and tells you.
- **Install from a file or URL** (for plugin authors) is allowed in Sources, shows "unsigned", and always asks again.
- An option for later: **Sigstore** keyless signing (GitHub's OIDC identity, no private key to keep). It is stronger but
  more work to verify. See the open questions.

## 3.3 Permissions: what the person sees before install

Each plugin lists its permissions in `keel-plugin.yml`. keel shows them with a level.

| permission              | example                              | level  | how keel enforces it                                                |
| ----------------------- | ------------------------------------ | ------ | ------------------------------------------------------------------- |
| `secrets: [kind]`       | read your Database connections       | high   | the SDK gives the plugin only secrets of the kinds it listed        |
| `network: [hosts]`      | `api.github.com`, `from-connections` | medium | the SDK HTTP client allows only these hosts; lint for other clients |
| `workspace: read/write` | read or change files in `/workspace` | medium | the SDK `Workspace` is read-only when it says `read`                |
| `agent_tools: read/act` | tools agents can call                | medium | `act` tools always go through keel's "ask first" gate               |
| `flows: start`          | start a flow by itself (CI watcher)  | medium | `Flows.start` in the SDK checks it                                  |
| `tables: [..]`          | its own tables                       | low    | migrations may touch only these names (checked at install)          |
| `pages`                 | adds pages or slots                  | low    | —                                                                   |

**Updates:** when a new version asks for **more** permissions, keel does not install it. It sends an Inbox card with
the difference ("+ use your GitLab connection").

## 3.4 What a plugin may never do

keel checks these at install and at start. The catalog checks them again with `keel-plugin lint` before a version is
listed.

1. Run anything at install time. Installing only unpacks files.
2. Replace or change a core file, a core workflow, agent, skill or route. Same names are refused.
3. Use names outside its own prefix (actions, events, MCP tools, tables, routes; see 02, 2.7).
4. Remove or weaken guard rules or gates. A plugin may **add** a rule, never remove one.
5. Read secrets of a kind it did not list, or core secrets (provider keys, `master.key`).
6. Write outside `/data/plugins/data/<name>/` and the project workspace (if it has `workspace: write`).
7. Install, turn on or update another plugin. Only a person does that.
8. Load code from the internet at run time. A web part may load only files from its own folder; the page's
   Content-Security-Policy allows scripts only from keel itself.
9. Give an agent an "install" tool. Agents can only **ask** (see 04).

Points 2, 3, 7 and 9 keel enforces in code. Points 5, 6 and 8 the SDK enforces for plugins that use it. A plugin that
runs code could still go around the SDK. So for **verified** plugins the catalog's lint also checks the code: Python
imports (no core internals outside the SDK), Kotlin bytecode (ArchUnit rules), and the web bundle (no remote
`import()`, no `eval`). An unverified code plugin gets none of these promises, and keel says so.

## 3.5 Who may approve

keel usually runs on one person's machine (`127.0.0.1`). So "the person using keel" approves. If keel gets user roles
later, the rule "Who can approve installs" (Sources and rules) becomes: owners only, or everyone. Every install,
update, removal and approval is written to the keel event log with who and when.

## 3.6 What this plan does not solve

- A verified publisher could still ship a bad version. Revocation and the daily index check limit the damage, but
  they come after the fact.
- Web parts of plugins run in the same page as keel. A later option is to run community web parts in a sandboxed
  `iframe` with a message-based SDK.
- Native Python libraries and npm tools (for example the CodeGraph CLI) come from PyPI or npm at install time. We pin
  them with hashes, but we trust those registries.
