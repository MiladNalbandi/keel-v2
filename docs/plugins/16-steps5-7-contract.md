# 16 · Steps 5–7 contract: own repos, a core image, other publishers

The last three steps of the plan ([05-migration.md](05-migration.md)), on the plugin track (`plugin-base`, never
merged into `main`, never tagged). Step 4 (the marketplace) is done before them. The rule stays: for people keel is
the same as the latest release; these steps only add.

Everything here is built and tested **locally first**. The outward actions (pushing code into the keel-studio repos,
making them public, publishing an image) are listed at the end; they happen only when Milad says so.

## Step 5 · Each plugin can live in its own repo

**The main choice: a plugin repo builds against keel's source at a fixed commit.** No SDK package has to be published
to PyPI, npm or Maven (public and hard to take back). A plugin repo's CI checks out `MiladNalbandi/keel-v2` at the
commit in its `keel.ref` file, puts the plugin into `keel/plugins/<name>/`, and runs keel's own build machinery for that
one plugin (the same as today: `scripts/test-plugins.sh <name>`, `./gradlew <name>Test <name>PluginJar`,
`npm run build:plugin -- <name>`, `scripts/build-plugin.sh`). Publishing SDK packages stays an option for later.

```
keel-studio/keel-plugin-<name>             its CI (push, pull request)          on a tag v<version>
───────────────────────────────            ───────────────────────────          ───────────────────
keel-plugin.yml  engine/  api/  web/       1 checkout keel-v2 @ keel.ref        the same build, then
content/  migrations/  README.md           2 copy the plugin into keel/plugins/  keel-plugin pack, lint,
keel.ref       (a keel-v2 commit sha)      3 keel's build and tests for it        sign (KEEL_PUBLISHER_KEY),
.github/workflows/ci.yml, release.yml      4 keel-plugin pack + lint              GitHub release: .kplug +
                                                                                   .kplug.minisig
```

- **`docs/plugins/plugin-repo/`**: the files every plugin repo gets: `.github/workflows/ci.yml` and `release.yml` (as
  above; the release refuses a tag that is not the manifest's version), `keel.ref` (filled by the split tool), a
  README template. The publisher key is an **organisation secret** `KEEL_PUBLISHER_KEY` in keel-studio (one secret for
  all first-party repos; Milad sets it with the setup script of step 4, extended with `--org`).
- **`docs/plugins/tools/split-plugin.sh <name> <out-dir>`**: makes a local git repo for one plugin **with its history**
  (`git subtree split --prefix=plugins/<name>`, or `product` for Product), adds the files of `plugin-repo/` and
  `keel.ref` (the current commit), and commits. It never pushes. `--push` prints the commands to push it to
  `keel-studio/keel-plugin-<name>` (Milad runs them, or says yes).
- **keel-v2 reads released plugins from a lock file.** `plugins.lock` (a new file at the top): one line per plugin,
  `<name> <version> <url> sha256:<hex>`. The Dockerfile's plugin stage gets `ARG PLUGINS=source` (today's way: build
  `plugins/*`) and `PLUGINS=lock` (download each locked `.kplug` and its `.minisig`, check sha256 and the signature with
  `content/trust/keel.pub` using `keel-plugin verify`, unpack into `/opt/keel-v2/plugins`). Default: `source`, until
  every plugin has a release; then Milad decides to switch (and later to remove `plugins/` from keel-v2).
  `scripts/update-plugins-lock.sh [name…]` writes the lines from the plugins' latest releases.
- **Plugin to plugin:** Jira uses Tasks' classes, Review uses KeelBot's, CI/CD needs Git, Product needs Tasks and Jira.
  In its own repo a plugin's CI also checks out the plugins it `requires` (their repos, at the version the manifest
  asks for, or keel-v2's copy while they are not split) into `keel/plugins/`. The build already compiles a plugin
  against the plugins it requires.
- **Proof (local, no network):** split `wiki` (small) and `jira` (needs tasks) into temp repos, run their CI steps
  locally against a local keel-v2 checkout, make signed `.kplug` files with a test key, serve them over a local HTTP
  server, and build an image with `PLUGINS=lock` (pointing at the local server, test key) that loads them. A test in
  CI checks `plugins.lock`'s format and that `PLUGINS=lock` refuses a wrong sha256 and a bad signature.

## Step 6 · A core image

- `EDITION=core` exists (step 3). New: `keel2 start --core` starts the `:core` image (`KEEL_IMAGE` still wins); `keel2
status` says which edition runs. `docker.yml` also builds and pushes `:core` (and `:<version>-core`) next to
  `:latest` when it runs (it runs on release tags of `main`: nothing changes until the track is released).
- **Core only must be clean:** on a core image (or `KEEL_PLUGINS=off`) no core page links to a page that is not there,
  no core page calls a plugin's endpoint and shows an error, the launcher has no plugin results, and the Project group
  shows "Start with a set" (step 4). Known places: "Add a task" (Tasks), the Wiki links in Flow, the Code page links,
  the Graph/index tag, ⌘K's code preview and symbols, KeelBot's "Ask" in the launcher, Inbox "Open KeelBot". A page
  shows a link to another page only when the page registry has it (`hasPage(id)` or the like in the SDK).
- **e2e `e2e/core/e2e.py`**: a throw-away `keel-core` (port 8097) from the core image: health, `/api/plugin-host` empty,
  every core menu page opens with no console error and no failed request to a plugin route, no link to a missing page,
  "Start with a set" shows. Plus: the same image with `KEEL_PLUGINS=off`.
- The report says how much smaller the core image is.

## Step 7 · Other publishers

- **`keel-plugin dev <dir> [--keel http://127.0.0.1:8080]`**: builds and packs the plugin (unsigned), copies the file
  into the running keel's `/data` (`docker cp` into the keel container, name from `KEEL_NAME` / `keel2`'s default),
  installs it with `POST /api/plugins/install-file`, and restarts keel. It says clearly that the file is unsigned.
- **`docs/plugins/template-repo/`**: the content of `keel-studio/keel-plugin-template`: what `keel-plugin new` makes,
  plus `plugin-repo/`'s CI and release workflows, set up for a publisher's **own** key (a repo secret), and a README
  that walks through new → dev → lint → keygen → tag → pull request to the marketplace.
- **`docs/plugins/17-publish-your-plugin.md`**: the guide for other publishers (simple English): the steps of
  04-marketplace.md §4.5, the trust levels, what lint checks, how the marketplace pull request works, how a publisher
  becomes **verified** (a review checklist: who they are, a code read of one release, the lint, the permissions match
  the code).
- The marketplace's `check-pr.yml` (step 4) already checks a community plugin's newest release; add a test that a
  community publisher's content-only plugin passes and its code plugin is listed as unverified (`verified: false`).

## Outward actions (only when Milad says so)

1. Push each split repo to `keel-studio/keel-plugin-<name>` (they exist, with a README) and the template to
   `keel-studio/keel-plugin-template`; make them public.
2. Set the organisation secret `KEEL_PUBLISHER_KEY` in keel-studio (the setup script; Milad runs it).
3. Tag the first releases in the plugin repos (their CI signs and releases).
4. Switch keel-v2 to `PLUGINS=lock`; later remove `plugins/` from keel-v2.
5. Publish `:core` (with keel's next release from `main`, when the track becomes the release).
