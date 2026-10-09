"""keel-plugin new <name>: a plugin folder from a small template (manifest, content/, engine/, web/, README).

The result passes `keel-plugin lint` as it is, and `keel-plugin pack` makes a package of it.
"""

from __future__ import annotations

from pathlib import Path

from . import PluginToolError
from .manifest import NAME


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def files(name: str, publisher: str) -> dict[str, str]:
    package = "keel_plugin_" + name.replace("-", "_")
    title = _title(name)
    return {
        "keel-plugin.yml": f"""\
# The plugin's manifest (keel's docs/plugins/02-plugin-package.md). Check it with: keel-plugin lint .
schema: 1
name: {name}                   # the id: a-z, 0-9 and '-'; every name the plugin adds starts with it
title: {title}
version: 0.1.0
publisher: {publisher}                  # must match the key that signs your releases
summary: One sentence that says what {title} does for the person.
license: MIT

requires:
  sdk: 1                       # the plugin SDK major version
  # keel: ">=0.15.4"           # an extra limit on keel's version
  # plugins: {{ code: ">=1.0.0" }}   # other plugins it needs (on one line)

# Write each part on one line. Remove the parts you do not use.
parts:
  engine: {{ path: engine, package: {package} }}
  web: {{ entry: web/index.js }}
  content: content

per_project: false

permissions: {{}}                # what it may do; keel shows it before install (docs/plugins/03-security.md)
""",
        "README.md": f"""\
# {title}

A keel plugin. {title} does … (say it in one or two sentences).

```
keel-plugin.yml   the manifest: name, version, what it needs, its parts, its permissions
engine/           {package}: the Python part (actions, agent tools, hooks)
web/              index.js: the web part (pages and slots), an ES module
content/          workflows, agents, skills
```

## Check, pack and sign

```
keel-plugin lint .                                   the checks keel and the catalog run
keel-plugin pack . --out dist                        dist/{name}-0.1.0.kplug
keel-plugin sign dist/{name}-0.1.0.kplug --key-file you.key
```

Names stay in the plugin's box: actions `{name}:do`, events `{name}.done`, agent tools `{package[12:]}_read`,
tables `{package[12:]}_items`. Use only keel's plugin SDK, never keel's internal code. Nothing runs at install time.
""",
        f"engine/{package}/__init__.py": f'''\
"""{title}: the engine part. keel imports this package when it starts and reads ADDON."""

VERSION = "0.1.0"   # the same version as keel-plugin.yml

ADDON = {{
    "name": "{name}",
    "title": "{title}",
    "version": VERSION,
    "actions": {{}},     # "{name}:do": a function, for workflow steps
}}
''',
        "web/index.js": f"""\
// {title}: the web part. keel loads this ES module at start and calls setup() once.
// Build a bigger web part with Vite as an ES library; react and @keel/web-sdk come from keel's page.

export default {{
  name: "{name}",
  setup(sdk) {{
    // sdk.registerPage({{ id: "{name}", label: "{title}", group: "project", order: 90, component: MyPage }});
  }},
}};
""",
        "content/README.md": f"""\
# content/

Workflows (`workflows/*.yaml`), agents (`agents/*.md`) and skills (`skills/<name>/SKILL.md`) of {title}. keel finds
them after its own; a plugin cannot replace one of keel's with the same name.
""",
        ".gitignore": "__pycache__/\n*.pyc\ndist/\n*.kplug\n*.minisig\n*.key\n",
    }


def new(name: str, where: Path, publisher: str = "you") -> Path:
    """Write the template into <where>/<name>; refuses a folder that is not empty."""
    if not NAME.match(name):
        raise PluginToolError(f"'{name}' is not a plugin name: a-z, 0-9 and '-', starts with a letter, 32 at most")
    if not NAME.match(publisher):
        raise PluginToolError(f"'{publisher}' is not a publisher id: a-z, 0-9 and '-', starts with a letter")
    root = where / name
    if root.exists() and any(root.iterdir()):
        raise PluginToolError(f"{root} is there already and not empty")
    for rel, text in files(name, publisher).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root
