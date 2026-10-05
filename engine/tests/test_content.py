"""content/ is keel v2's own: every agent, skill, stack and pack there is well formed, and none of it tells an agent
to use keel v1 (its slash commands, its bin/keel or its state file)."""

import re
from pathlib import Path

import pytest
import yaml

from keel_engine import config

CONTENT = Path(__file__).resolve().parents[2] / "content"


def front_matter(f: Path) -> dict:
    m = re.match(r"\A---\n(.*?)\n---\n", f.read_text(), flags=re.S)
    assert m, f"{f} has no front matter"
    return yaml.safe_load(m.group(1)) or {}


def test_the_dev_checkout_finds_its_own_content(monkeypatch):
    monkeypatch.delenv("KEEL_CONTENT", raising=False)
    if not Path("/opt/keel-v2/content").is_dir():
        assert config.content_dir() == CONTENT
    monkeypatch.setenv("KEEL_CONTENT", "/somewhere/else")
    assert config.content_dir() == Path("/somewhere/else")
    assert config.v2_skills() == Path("/somewhere/else/skills")


def test_every_agent_has_a_name_and_max_turns():
    agents = sorted((CONTENT / "agents").glob("*.md"))
    assert len(agents) >= 18
    for f in agents:
        fm = front_matter(f)
        assert fm.get("name") == f.stem, f
        assert isinstance(fm.get("maxTurns"), int) and fm["maxTurns"] > 0, f


SECTIONS = {"architecture", "domain", "conventions", "data", "integrations", "journeys"}


def test_every_agent_has_a_valid_knowledge_block():
    for f in sorted((CONTENT / "agents").glob("*.md")):
        k = front_matter(f).get("knowledge")
        assert isinstance(k, dict), f"{f.name}: no knowledge block"
        assert set(k) == {"sections", "code_graph", "memory", "strict"}, f.name
        assert isinstance(k["sections"], list) and set(k["sections"]) <= SECTIONS, f.name
        assert len(set(k["sections"])) == len(k["sections"]), f.name
        assert all(isinstance(k[b], bool) for b in ("code_graph", "memory", "strict")), f.name
    # the librarian writes the sections; it is given none to read
    assert front_matter(CONTENT / "agents" / "librarian.md")["knowledge"]["sections"] == []


def skill_dirs():
    return sorted([*(CONTENT / "skills").iterdir(), *(CONTENT / "packs").glob("*/skills/*")])


def test_every_skill_has_a_skill_md_with_name_and_description():
    dirs = skill_dirs()
    assert {"spec-clarify", "spec-writing", "architecture", "kotlin-spring-testing"} <= {d.name for d in dirs}
    for d in dirs:
        fm = front_matter(d / "SKILL.md")
        assert fm.get("name") == d.name, d
        assert fm.get("description"), d


def test_flow_skills_are_not_content():
    # the flows are keel's graph, not a skill an agent follows
    names = {d.name for d in skill_dirs()}
    assert not names & {"feature", "fix", "change", "hunt", "hunt-next", "init", "ship", "cover", "status",
                        "diagnose", "spec-authoring"}


@pytest.mark.parametrize("f", sorted([*(CONTENT / "stacks").glob("*.yml"), *(CONTENT / "packs").glob("*/stack.yml")]),
                         ids=lambda f: f"{f.parent.name}/{f.name}")
def test_every_stack_parses_and_has_a_name(f):
    doc = yaml.safe_load(f.read_text())
    assert doc.get("name"), f
    assert doc.get("lane") in ("api", "web"), f


def test_every_pack_is_one_folder_with_its_files():
    packs = {p.name: p for p in (CONTENT / "packs").iterdir() if p.is_dir()}
    assert set(packs) == {"django", "react-js", "symfony"}
    for p in packs.values():
        doc = yaml.safe_load((p / "stack.yml").read_text())
        assert doc["name"] == p.name
        # paths in a pack are relative to its folder
        for rel in (doc.get("skill_files") or {}).values():
            assert (p / rel).is_file(), f"{p.name}: {rel}"
        for s in doc.get("starter") or []:
            assert (p / s["from"]).is_file(), f"{p.name}: {s['from']}"
        if doc.get("arch_refs"):
            assert (p / doc["arch_refs"]).is_dir()


def test_the_librarian_templates_are_there():
    names = {f.stem for f in (CONTENT / "templates" / "knowledge").glob("*.md")}
    assert names == {"index", "architecture", "domain", "conventions", "data", "integrations", "journeys"}


FORBIDDEN = re.compile(r"/keel:|bin/keel|\.keel/state\.json|AskUserQuestion")


def test_no_content_tells_an_agent_to_use_keel_v1():
    hits = []
    for f in sorted(CONTENT.rglob("*")):
        if f.is_file():
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if FORBIDDEN.search(line):
                    hits.append(f"{f.relative_to(CONTENT)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_notice_names_keel_v1_and_its_license():
    text = (CONTENT / "NOTICE.md").read_text()
    assert "https://github.com/MiladNalbandi/keel" in text and "a9ed9e3" in text
    assert "MIT License" in text and "Copyright (c) 2026 Milad Nalbandi" in text
