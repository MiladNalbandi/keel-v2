"""lint: a plugin's workflow or agent with one of keel's own names is an error (keel keeps its own, so it never loads;
03-security.md 3.4 rule 2). The lists in lint.py are keel-v2's content/ and the agents the engine has without a file."""

from __future__ import annotations

import re

from conftest import REPO, write

from keel_plugin import template
from keel_plugin.lint import KEEL_AGENTS, KEEL_RENAMED_AGENTS, KEEL_WORKFLOWS, Report, _content_names, lint
from keel_plugin.manifest import read


def test_the_lists_are_keels_content():
    workflows = REPO / "content/workflows"
    ids = {m.group(1) for f in workflows.glob("*.yaml") if (m := re.search(r"^id:\s*(\S+)", f.read_text(), re.M))}
    assert KEEL_WORKFLOWS == {f.stem for f in workflows.glob("*.yaml")} == ids
    prompts = (REPO / "engine/keel_engine/runtime/prompts.py").read_text()
    builtin = re.search(r"^BUILTIN_ROLES = \{(.*?)^\}", prompts, re.M | re.S).group(1)
    no_file = set(re.findall(r'^    "([a-z0-9-]+)":', builtin, re.M))
    assert no_file == {"contract-author"}
    assert KEEL_AGENTS == {f.stem for f in (REPO / "content/agents").glob("*.md")} | no_file | KEEL_RENAMED_AGENTS
    names = (REPO / "engine/keel_engine/agent_names.py").read_text()
    renamed = re.search(r"^RENAMED: dict\[str, str\] = \{(.*?)^\}", names, re.M | re.S).group(1)
    assert KEEL_RENAMED_AGENTS == set(re.findall(r'^    "([a-z0-9-]+)":', renamed, re.M))


def test_a_content_name_of_keels_is_an_error(tmp_path, run):
    root = template.new("notes", tmp_path, parts=template.parts_of("content"))
    write(root / "content/workflows/feature.yaml", "id: notes-feature\nname: x\nsteps: []\n")
    write(root / "content/workflows/sneaky.yaml", "id: fix\nname: x\nsteps: []\n")
    write(root / "content/agents/explorer.md", "---\nname: notes-explorer\n---\nx\n")
    write(root / "content/agents/quiet.md", "---\nname: 'implementer'\n---\nx\n")
    write(root / "content/agents/contract-author.md", "x\n")
    code, out, _ = run("lint", root)
    assert code == 1
    for why in ["content/workflows/feature.yaml: keel has a workflow called feature; keel keeps its own, so this one "
                "never loads: give it another name (start it with 'notes-')",
                "content/workflows/sneaky.yaml: keel has a workflow called fix",
                "content/agents/explorer.md: keel has an agent called explorer",
                "content/agents/quiet.md: keel has an agent called implementer",
                "content/agents/contract-author.md: keel has an agent called contract-author"]:
        assert why in out, why
    assert "notes-flow" not in out and "notes-agent" not in out          # the example's own names are fine


def test_keels_own_plugins_use_none_of_keels_names():
    folders = [p for p in sorted((REPO / "plugins").iterdir()) if (p / "keel-plugin.yml").is_file()] + [REPO / "product"]
    assert len(folders) == 21
    for folder in folders:
        report = Report(str(folder))
        _content_names(folder, read(folder), report)
        assert report.errors == [], folder.name
    assert lint(REPO / "e2e/marketplace/fixtures/hello").errors == []
