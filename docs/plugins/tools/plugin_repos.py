"""Writes the starter files of keel's plugin repos and the marketplace repo into local folders.

    python3 plugin_repos.py <out dir> <LICENSE file>     prints "<repo>\t<description>" for each repo

create-plugin-repos.sh runs it, then creates the repos on GitHub.
"""

import json
import shutil
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
LICENSE = Path(sys.argv[2]).read_text()
OWNER = "MiladNalbandi"
PLAN = f"https://github.com/{OWNER}/keel-v2/tree/main/docs/plugins"
KEEL = f"https://github.com/{OWNER}/keel-v2"

# id: (title, category, summary, parts, needs, optional, adds, today, tags)
PLUGINS = {
    "code": ("Code", "code",
             "The Code page: files, search, an editor and the changes on your branch. It is a host: Git, Code Review, "
             "Database and CI/CD add their tabs to it.",
             ["api", "web"], [], [],
             ["the Code page (#/code)", "slots for its own plugins: `code.activity`, `code.tab`, `code.scm`"],
             ["web/src/pages/Repo.tsx", "web/src/pages/repo/*", "api keel.api.repo (the page's endpoints)"],
             ["ide", "editor"]),
    "git": ("Git", "code",
            "The Git panel, git actions for flows and git tools for agents. Agents read git; a person presses Commit and "
            "Push. keel's core keeps the git basics that flows need.",
            ["engine", "api", "web", "content"], ["code"], [],
            ["Code › Source control panel", "`git:*` actions for workflows", "`keel-git` tools for agents",
             "KeelBot commands /commit, /pr, /sync, /branch"],
            ["engine/keel_engine/plugins/git", "api keel.api.plugins.Git", "web/src/components/plugins/GitPanel.tsx",
             "content/plugins/git"],
            ["git", "pull-requests"]),
    "review": ("Code Review", "code",
               "Review GitHub and GitLab pull requests inside the Code page. AI comments wait for your OK before they "
               "are posted.",
               ["api", "web", "content", "migrations"], ["code"], ["keelbot"],
               ["Code › Review", "GitLab connection", "KeelBot commands /review-branch, /explain-pr"],
               ["api keel.api.review", "web/src/components/review/*", "web/src/reviewApi.ts", "content/plugins/review"],
               ["code-review", "github", "gitlab"]),
    "db": ("Database", "code",
           "Connect a database. You see its tables and an ER diagram. Agents get read-only SQL tools.",
           ["engine", "api", "web", "content", "migrations"], ["code"], ["map"],
           ["Code › Database tab", "Database connections", "`db:*` actions", "`keel-db` tools for agents",
            "the ER tab in Map (when Map is installed)"],
           ["engine/keel_engine/plugins/db", "api keel.api.plugins.Database", "web/src/components/plugins/DbTool.tsx",
            "content/plugins/db"],
           ["database", "sql", "er-diagram"]),
    "ci": ("CI/CD", "code",
           "Pipeline results in flows, Jobs and the Code page. A failed job can start a fix flow.",
           ["engine", "api", "web", "content", "migrations"], ["code", "git"], [],
           ["Jobs › Pipelines", "`ci:*` actions", "the `ci-fix` workflow", "the CI watcher"],
           ["engine/keel_engine/plugins/ci", "api keel.api.plugins.Ci", "web/src/components/plugins/Pipelines.tsx",
            "content/plugins/ci"],
           ["ci", "github-actions"]),
    "keelbot": ("KeelBot", "assist",
                "Chat with your project. Other plugins add slash commands and cards to it.",
                ["engine", "api", "web", "content"], [], [],
                ["the KeelBot page and the ⌘I panel", "the `assistant` slot", "its own slots `keelbot.command`, `keelbot.card`"],
                ["engine/keel_engine/runtime/helper.py", "engine/keel_engine/runtime/keelbot.py", "api keel.api.helper",
                 "web/src/components/helper/*"],
                ["assistant", "chat"]),
    "map": ("Map", "know",
            "A picture of the project: the system, its modules and the database (ER), drawn from folders, migrations and "
            "the API contract.",
            ["engine", "web"], [], ["db"],
            ["the Map page (#/map)"],
            ["engine/keel_engine/runtime/mapper.py", "engine/keel_engine/runtime/sqlschema.py", "web/src/pages/Map.tsx"],
            ["architecture", "diagram"]),
    "graph": ("Graph", "know",
              "The code graph page, and code-graph context for agents. Flows still run without it, with less context.",
              ["engine", "web"], [], [],
              ["the Graph page (#/graph)", "a context provider: the code-graph tools and hints for agents"],
              ["engine/keel_engine/tools/codegraph.py", "engine/keel_engine/runtime/codegraph_view.py",
               "engine/keel_engine/runtime/graph_hints.py", "web/src/pages/Graph.tsx"],
              ["code-graph", "context"]),
    "wiki": ("Wiki", "know",
             "The Wiki page for the project's knowledge base. The knowledge base itself stays in keel's core, because "
             "flows check it.",
             ["api", "web", "content"], [], [],
             ["the Wiki page (#/wiki)", "the Refresh button (the `knowledge-refresh` workflow)"],
             ["web/src/pages/Wiki.tsx", "api keel.api.knowledge.WikiRefresh", "content/workflows/knowledge-refresh.yaml"],
             ["knowledge", "docs"]),
    "tasks": ("Tasks", "tickets",
              "A list of work items. Start a flow from a task with one button.",
              ["api", "web", "migrations"], [], [],
              ["the Tasks page (#/tasks)", "task cards in the Inbox"],
              ["api keel.api.tasks", "web/src/pages/Tasks.tsx", "web/src/tasksApi.ts"],
              ["tasks", "tickets"]),
    "jira": ("Jira", "tickets",
             "Bring Jira issues into Tasks and keep their status in step with your flows.",
             ["api", "web", "migrations"], ["tasks"], [],
             ["Jira connection", "Jira sync for Tasks"],
             ["api keel.api.jira", "api keel.api.tasks.JiraSync", "web/src/components/JiraCard.tsx"],
             ["jira", "tickets"]),
    "product": ("keel Product", "product",
                "Initiatives, teams and product docs, from idea to the teams' stories, with their own workflows and agents. "
                "Beta.",
                ["engine", "api", "web", "content", "migrations"], ["tasks", "jira"], [],
                ["the Initiatives and Teams pages", "5 product workflows and 4 agents"],
                ["product/ in keel-v2 (already an add-on)"],
                ["product-management"]),
}

TRUST = {"content": "content only", "web": "adds pages", "code": "runs code in keel"}


def trust(parts: list[str]) -> str:
    if {"engine", "api"} & set(parts):
        return "code"
    return "web" if "web" in parts else "content"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n")


def manifest(pid: str) -> str:
    title, cat, summary, parts, needs, optional, *_ = PLUGINS[pid]
    lines = [
        "# The plugin's manifest. Draft: the code still lives in keel-v2, so nothing is released yet.",
        f"# Format: {PLAN}/02-plugin-package.md",
        "schema: 1",
        f"name: {pid}",
        f"title: {json.dumps(title)}",
        "version: 0.0.0",
        "publisher: keel",
        f"summary: {json.dumps(summary, ensure_ascii=False)}",
        f"repo: https://github.com/{OWNER}/keel-plugin-{pid}",
        "license: MIT",
        "",
        "requires:",
        "  sdk: 1",
    ]
    if needs:
        lines.append("  plugins:")
        lines += [f'    {n}: ">=0.1.0"' for n in needs]
    if optional:
        lines.append("optional:")
        lines += [f'  {n}: ">=0.1.0"' for n in optional]
    lines += ["", "parts:"]
    paths = {"engine": f"{{ path: engine, package: keel_plugin_{pid} }}", "api": f"{{ jars: [api/keel-plugin-{pid}.jar] }}",
             "web": "{ entry: web/index.js }", "content": "content", "migrations": "migrations"}
    lines += [f"  {p}: {paths[p]}" for p in parts]
    return "\n".join(lines)


def today_line(t: str) -> str:
    if t.startswith("api "):
        return f"- `{t[4:]}` (api)"
    return f"- {t}" if t.startswith("product/") else f"- `{t}`"


def plugin_readme(pid: str) -> str:
    title, _cat, summary, parts, needs, optional, adds, today, _tags = PLUGINS[pid]
    need_txt = ", ".join(["keel core (plugin SDK 1)"] + [PLUGINS[n][0] for n in needs])
    opt_txt = ", ".join(PLUGINS[n][0] for n in optional) or "—"
    return f"""# keel plugin: {title}

{summary}

> **Status: planned.** The code still lives in [keel-v2]({KEEL}). It moves here step by step, as
> [the plugin plan]({PLAN}) says. There is nothing to install yet.

| | |
| --- | --- |
| id | `{pid}` |
| needs | {need_txt} |
| works with | {opt_txt} |
| parts | {" · ".join(parts)} |
| trust level | {TRUST[trust(parts)]} |

**What it adds to keel**

{chr(10).join("- " + a for a in adds)}

**Where the code is today (keel-v2)**

{chr(10).join(today_line(t) for t in today)}

## Layout

```
keel-plugin.yml   the manifest
{chr(10).join(f"{p + '/':<17} " + {"engine": "Python package for keel's engine", "api": "Kotlin, a thin Spring Boot jar", "web": "React pages and slots (an ES module)", "content": "workflows, agents, skills, commands", "migrations": "its own database tables (own Flyway history)"}[p] for p in parts)}
```

## Install

When it is released: in keel, **Control › Plugins › Marketplace › {title} › Install**. keel checks the file's
signature, shows what the plugin may do, and asks you before it installs.

## License

MIT
"""


def template_files(root: Path) -> None:
    write(root / "README.md", f"""# keel plugin template

Start a new [keel]({KEEL}) plugin from this repo: press **Use this template** on GitHub.

> **Status: early.** The plugin SDK and the build come with keel 0.15–0.20 ([the plan]({PLAN})). This repo shows the
> layout and the manifest now, so plugin repos look the same from the start.

A plugin can have up to five parts. All are optional; keep only what you need.

```
keel-plugin.yml   the manifest: name, version, what it needs, its parts, its permissions
engine/           Python package for keel's engine: actions, agent tools (MCP), hooks
api/              Kotlin, a thin Spring Boot jar: endpoints, Inbox cards, connections
web/              React pages and slots, built as one ES module (index.js)
content/          workflows, agents, skills, stacks, KeelBot commands
migrations/       V1__init.sql …: its own tables, with its own history
```

## Rules

- Every name starts with your plugin's id: actions `example:do`, events `example.done`, agent tools `example_read`,
  tables `example_items`.
- Use only keel's plugin SDK, never keel's internal code.
- No install scripts. keel unpacks the files and loads them when it starts.
- Ask only for the permissions you need. keel shows them before install.

## Release

A tag `v0.1.0` builds `example-0.1.0.kplug`, signs it, and makes a GitHub release (the workflow comes with the SDK).
Then add your plugin to [keel-marketplace](https://github.com/{OWNER}/keel-marketplace).

## License

MIT
""")
    write(root / "keel-plugin.yml", f"""# The plugin's manifest. Full format: {PLAN}/02-plugin-package.md
schema: 1
name: example                  # your id: [a-z][a-z0-9-]{{0,31}}, the prefix of all your names
title: Example
version: 0.1.0
publisher: you                 # must match the key that signs your releases
summary: One sentence that says what it does for the person.
repo: https://github.com/you/keel-plugin-example
license: MIT

requires:
  sdk: 1                       # the plugin SDK major version
  # keel: ">=0.20.0,<1.0.0"    # optional extra limit on keel's version
  # plugins:
  #   code: ">=1.0.0,<2.0.0"   # other plugins it needs

parts:                         # remove the parts you do not have
  engine: {{ path: engine, package: keel_plugin_example }}
  api: {{ jars: [api/keel-plugin-example.jar] }}
  web: {{ entry: web/index.js }}
  content: content
  migrations: migrations

contributes:
  screens: [{{ id: example, label: Example, group: Project }}]
  actions: [example:do]

permissions:                   # shown to the person before install
  workspace: read
  tables: [example_items]
""")
    for part, text in {
        "engine": "Python package `keel_plugin_example` with a module-level `ADDON` dict (actions, router, mcp, hooks).",
        "api": "A thin Spring Boot jar with `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`.",
        "web": "`index.js` default-exports `definePlugin({ name, pages, slots })` from `@keel/web-sdk`.",
        "content": "`workflows/`, `agents/`, `skills/`, `stacks/`, and `plugin.yml` for KeelBot commands.",
        "migrations": "`V1__init.sql`, `V2__…`: Flyway migrations. New tables start with your id (`example_`).",
    }.items():
        write(root / part / "README.md", f"# {part}/\n\n{text}\n")
    write(root / ".gitignore", "__pycache__/\n*.pyc\nbuild/\ndist/\nnode_modules/\n.gradle/\n*.kplug\n")


def marketplace_files(root: Path) -> None:
    write(root / "README.md", f"""# keel marketplace

The list of plugins [keel]({KEEL}) can install. It holds **no plugin code**, only pointers to each plugin's repo
and releases.

> **Status: early.** The entries are here; the signed index and keel's Plugins page come with keel 0.20
> ([the plan]({PLAN}/04-marketplace.md)).

## How it works

```
 plugin repo (a tag)            this repo                               keel
 ───────────────────            ─────────                               ────
 builds db-1.4.0.kplug   ──▶    sync: checks sha256, signature, lint
 signs it, releases it          adds the version to v1/index.json  ──▶  reads the index (signed),
                                signs the index (GitHub Pages)          shows the Plugins page,
                                                                        installs after you say yes
```

```
publishers/   who publishes: name, public keys, verified or not
plugins/      one file per plugin: id, publisher, repo, title, summary, category, tags
revoked.yml   versions that must never be installed
tools/        check.py: the checks a pull request must pass
```

## Add your plugin

1. Make your plugin from [keel-plugin-template](https://github.com/{OWNER}/keel-plugin-template).
2. Open a pull request here with `publishers/<you>.yml` (your **public** key) and `plugins/<id>.yml`.
3. The checks pass and a maintainer merges. Your plugin shows as **community**. After a review it can become
   **verified**.

Private keys and tokens never go into this repo. Release signing keys live in GitHub Actions secrets.

## License

MIT
""")
    write(root / "publishers" / "keel.yml", """# keel's own plugins.
name: keel
title: keel
verified: true
contact: https://github.com/MiladNalbandi/keel-v2/issues
# Ed25519 public keys that sign keel's plugin releases. Added when signing starts (keel 0.20).
# Only public keys go here; the private key lives in GitHub Actions secrets.
keys: []
""")
    for pid, (title, cat, summary, _parts, needs, _opt, _adds, _today, tags) in PLUGINS.items():
        write(root / "plugins" / f"{pid}.yml", "\n".join([
            f"name: {pid}",
            f"title: {json.dumps(title)}",
            "publisher: keel",
            f"repo: https://github.com/{OWNER}/keel-plugin-{pid}",
            f"category: {cat}",
            f"summary: {json.dumps(summary, ensure_ascii=False)}",
            f"tags: [{', '.join(tags)}]",
            f"trust: {trust(_parts)}",
            "status: planned            # no release yet; the code still lives in keel-v2",
        ]))
    write(root / "revoked.yml", "# Versions keel must never install: - { name: x, version: 1.2.3, why: \"…\" }\nrevoked: []\n")
    write(root / "tools" / "check.py", '''"""Checks the catalog: every plugin and publisher file is valid. Run: python tools/check.py"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
CATEGORIES = {"code", "assist", "know", "tickets", "product", "community"}
TRUST = {"content", "web", "code"}
STATUS = {"planned", "listed"}


def main() -> int:
    errors: list[str] = []
    publishers = {}
    for f in sorted((ROOT / "publishers").glob("*.yml")):
        p = yaml.safe_load(f.read_text()) or {}
        if p.get("name") != f.stem or not ID.match(str(p.get("name"))):
            errors.append(f"{f.name}: name must be the file name and a valid id")
        if not isinstance(p.get("keys"), list) or not all(str(k).startswith("ed25519:") for k in p["keys"]):
            errors.append(f"{f.name}: keys must be a list of ed25519:<public key>")
        publishers[f.stem] = p
    for f in sorted((ROOT / "plugins").glob("*.yml")):
        p = yaml.safe_load(f.read_text()) or {}
        name = str(p.get("name"))
        if name != f.stem or not ID.match(name):
            errors.append(f"{f.name}: name must be the file name and a valid id")
        if p.get("publisher") not in publishers:
            errors.append(f"{f.name}: publisher {p.get('publisher')!r} has no file in publishers/")
        if not str(p.get("repo", "")).startswith("https://github.com/"):
            errors.append(f"{f.name}: repo must be a https://github.com/ URL")
        if p.get("category") not in CATEGORIES:
            errors.append(f"{f.name}: category must be one of {sorted(CATEGORIES)}")
        if p.get("trust") not in TRUST:
            errors.append(f"{f.name}: trust must be one of {sorted(TRUST)}")
        if p.get("status") not in STATUS:
            errors.append(f"{f.name}: status must be one of {sorted(STATUS)}")
        for key in ("title", "summary"):
            if not str(p.get(key) or "").strip():
                errors.append(f"{f.name}: {key} is missing")
    revoked = yaml.safe_load((ROOT / "revoked.yml").read_text()) or {}
    if not isinstance(revoked.get("revoked"), list):
        errors.append("revoked.yml: revoked must be a list")
    for e in errors:
        print("error:", e)
    print(f"{len(publishers)} publishers, {len(list((ROOT / 'plugins').glob('*.yml')))} plugins, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
''')
    write(root / ".github" / "workflows" / "check.yml", """name: check
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pyyaml==6.0.2
      - run: python tools/check.py
""")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for pid in PLUGINS:
        root = OUT / f"keel-plugin-{pid}"
        write(root / "README.md", plugin_readme(pid))
        write(root / "keel-plugin.yml", manifest(pid))
        write(root / "LICENSE", LICENSE)
    template_files(OUT / "keel-plugin-template")
    write(OUT / "keel-plugin-template" / "LICENSE", LICENSE)
    marketplace_files(OUT / "keel-marketplace")
    write(OUT / "keel-marketplace" / "LICENSE", LICENSE)
    print("keel-marketplace\tThe list of plugins keel can install: publishers, plugins and revoked versions. No plugin code.")
    print("keel-plugin-template\tStart a new keel plugin: the layout and the manifest (keel-plugin.yml).")
    for pid, (title, _c, summary, *_rest) in PLUGINS.items():
        print(f"keel-plugin-{pid}\tkeel plugin: {title}. {summary}")


if __name__ == "__main__":
    main()
