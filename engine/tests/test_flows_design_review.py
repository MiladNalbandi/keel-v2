"""keel 0.20.0 the design-review flow (content/workflows/design-review.yaml): Google's Elephant-Goldfish Model. Goldfish
reviewers in a fresh session each read a design document in EGM's five stages; keel merges their findings; the person
resolves them. On the fake model in a real git repo."""

from pathlib import Path

from conftest import decide, start, wait
from test_flows_base import answers
from keel_engine.runtime import compiler as compiler_mod, design_actions as da

DOC = """# Webhook delivery

## Goal

Deliver events to customers.

## Detailed Implementation Plan

1. Add a table.
"""
GOLDFISH = "egm-zoom-out egm-comprehension egm-design egm-structural egm-style egm-implementation-readiness egm-codebase-details".split()


def clean(req) -> str:
    return "S | Read it.\nGOLDFISH: done"


def said(found: dict | None = None) -> dict:
    return {g: (lambda r, t=(found or {}).get(g): (t or clean(r))) for g in GOLDFISH}


def write_doc(repo, name="docs/designs/hooks.md", text=DOC) -> str:
    f = Path(repo) / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return name


def test_the_five_stages_run_in_order_each_goldfish_alone_and_the_report_merges_them(client, repo, monkeypatch):
    same = "F | major | ## Goal | Nothing says what a failed delivery does | Name the final state"
    seen = answers(monkeypatch, said({
        "egm-zoom-out": f"{same}\nS | Read it.\nGOLDFISH: done",
        "egm-comprehension": "**What this proposes**\nIt sends events.\n\nF | blocker | line 3 | \"customers\" is not defined | Define it\nGOLDFISH: done",
        "egm-design": f"F | blocker | ## Goal | Nothing says what a failed delivery does | Name the final state\nGOLDFISH: done",
        "egm-style": "F | nit | line 1 | A title | Fine\nGOLDFISH: done"}))
    tid = start(client, repo, workflow="design-review", title="Review hooks", data={"doc": write_doc(repo)})
    s = wait(client, tid)
    assert s["waiting"]["step"] == "report_gate", s
    who = [r.agent for r in seen]                                       # the stages in order; inside one they run at once
    assert who[:2] == ["egm-zoom-out", "egm-comprehension"] and who[5] == "egm-zoom-out"
    assert sorted(who[2:5]) == ["egm-design", "egm-structural", "egm-style"]
    assert sorted(who[6:]) == ["egm-codebase-details", "egm-implementation-readiness", "egm-style"]
    assert len({r.session or id(r) for r in seen}) == len(seen)         # a fresh session each: no goldfish continues another
    assert not any(r.resume for r in seen)
    assert all("docs/designs/hooks.md" in r.prompt and "    1  # Webhook delivery" in r.prompt for r in seen)
    # the critique reads the feature part, readiness the plan; stage 4 sees what stage 1 found
    one = {r.agent: r for r in seen}
    assert '"lines": "1-6"' in one["egm-design"].prompt and '"lines": "7-9"' in one["egm-implementation-readiness"].prompt
    assert "failed delivery" in seen[5].prompt and "stage1" in seen[5].prompt
    shown = s["waiting"].get("detail") or s.get("show") or ""
    assert "🔴 Blocker" in shown and "🟢 Nit" in shown and "It sends events." in shown
    # the same finding from two reviewers is one, at the higher severity, with both names
    assert shown.count("Nothing says what a failed delivery does") == 1
    row = shown[shown.index("Nothing says what a failed delivery does"):]
    assert "`egm-design`" in row and "`egm-zoom-out`" in row
    assert shown.index("Nothing says what a failed") < shown.index("## 🟢 Nit")


def test_presets_pick_the_stages(client, repo, monkeypatch):
    seen = answers(monkeypatch, said())
    wait(client, start(client, repo, workflow="design-review", title="x", data={"doc": write_doc(repo), "preset": "feature"}))
    assert "egm-implementation-readiness" not in [r.agent for r in seen]
    assert [r.agent for r in seen].count("egm-zoom-out") == 2
    assert '"lines": "1-9' in next(r for r in seen if r.agent == "egm-design").prompt      # feature only: the whole document
    seen.clear()
    wait(client, start(client, repo, workflow="design-review", title="y", data={"doc": write_doc(repo), "preset": "implementation"}))
    assert sorted(r.agent for r in seen) == ["egm-codebase-details", "egm-implementation-readiness", "egm-style"]


def test_a_document_without_a_plan_skips_readiness_and_says_so(client, repo, monkeypatch):
    seen = answers(monkeypatch, said())
    s = wait(client, start(client, repo, workflow="design-review", title="x",
                           data={"doc": write_doc(repo, text="# Only a spec\n\nWords.\n")}))
    assert s["waiting"]["step"] == "report_gate", s
    assert "egm-implementation-readiness" not in [r.agent for r in seen]


def test_the_document_is_found_in_the_request_and_a_missing_one_stops_the_flow(client, repo, monkeypatch):
    seen = answers(monkeypatch, said())
    write_doc(repo)
    s = wait(client, start(client, repo, workflow="design-review", title="x", request="Please review docs/designs/hooks.md first"))
    assert s["waiting"]["step"] == "report_gate" and seen
    seen.clear()
    s = wait(client, start(client, repo, workflow="design-review", title="y", data={"doc": "docs/none.md"}))
    assert not seen and "docs/none.md" in str(s)


def test_resolve_edits_the_document_with_the_persons_note_then_a_fresh_review(client, repo, monkeypatch, tmp_path):
    def resolve(r):
        (Path(r.root) / "docs/designs/hooks.md").write_text(DOC + "\nFailed deliveries are dead-lettered.\n")
        return "G1 | FIXED | named the final state"
    seen = answers(monkeypatch, {**said({"egm-zoom-out": "F | major | ## Goal | No final state | Name it\nGOLDFISH: done"}), "egm-resolver": resolve})
    name = write_doc(repo)
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "doc"], cwd=repo, check=True)
    tid = start(client, repo, workflow="design-review", title="x", data={"doc": name})
    wait(client, tid)
    s = decide(client, tid, why="fix G1", payload={"choice": "resolve"})
    assert s["waiting"]["step"] == "resolve_gate", s
    resolver = next(r for r in seen if r.agent == "egm-resolver")
    assert "fix G1" in resolver.prompt and "No final state" in resolver.prompt
    shown = s["waiting"].get("detail") or s.get("show") or ""
    assert "dead-lettered" in shown and "G1 | FIXED" in shown
    assert "dead-lettered" in (Path(repo) / name).read_text()
    # again: fresh goldfish read the changed document
    seen.clear()
    s = decide(client, tid, payload={"choice": "again"})
    assert s["waiting"]["step"] == "report_gate", s
    assert all("dead-lettered" in r.prompt for r in seen if r.agent.startswith("egm-") and r.agent != "egm-resolver")
    s = decide(client, tid, payload={"choice": "done"})
    assert s["status"] == "done", s


def test_the_goldfish_change_nothing(client, repo, monkeypatch):
    def writes(r):
        (Path(r.root) / "docs/designs/hooks.md").write_text("changed by a reviewer")
        return clean(r)
    answers(monkeypatch, {**said(), "egm-design": writes})
    name = write_doc(repo)
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "doc"], cwd=repo, check=True)
    wait(client, start(client, repo, workflow="design-review", title="x", data={"doc": name}))
    assert (Path(repo) / name).read_text() == DOC                       # the diff guard puts it back (the review phase)


def test_a_goldfish_that_gives_no_lines_is_named_not_counted_as_clean():
    findings, reviewers = da.merge([("Stage 1", "egm-zoom-out", "I looked and it seems fine."),
                                    ("Stage 3", "egm-design", "S | Read it.\nGOLDFISH: done")])
    assert not findings
    assert [r["status"] for r in reviewers] == ["no answer", "completed"]


def test_the_merge_keeps_the_highest_severity_and_orders_blockers_first():
    findings, _ = da.merge([("1", "a", "F | nit | line 2 | small | x\nF | major | line 9 | The Same Thing | y"),
                            ("2", "b", "F | blocker | LINE 9 | the same thing! | z\nF | blocker | line 1 | other | w")])
    assert [(f["severity"], f["where"], f["by"]) for f in findings] == [
        ("blocker", "line 9", ["a", "b"]), ("blocker", "line 1", ["b"]), ("nit", "line 2", ["a"])]
    assert [f["id"] for f in findings] == ["G1", "G2", "G3"]


def test_the_synthesizer_only_groups_it_cannot_lose_a_finding_or_lower_a_severity():
    raw, _ = da.merge([("1", "a", "F | blocker | line 9 | seamless is vague | cut it\nF | nit | line 2 | seamless says nothing | cut\n"
                                  "F | major | line 4 | no owner | name one"),
                       ("2", "b", "F | nit | line 7 | a title | fine")])
    ids = {f["finding"]: f["id"] for f in raw}
    a, b = ids["seamless is vague"], ids["seamless says nothing"]
    answer = (f"M | {a},{b} | \"seamless\" says nothing testable | Remove it\n"
              f"M | {b},G99 | a second group with an id already used and one unknown | x\nsome other words")
    got = da.group(raw, answer)
    assert [f["finding"] for f in got] == ['"seamless" says nothing testable', "no owner", "a title"]
    assert got[0]["severity"] == "blocker" and got[0]["where"] == "line 9; line 2" and got[0]["change"] == "Remove it"
    assert [f["id"] for f in got] == ["G1", "G2", "G3"]
    assert [f["finding"] for f in da.group(raw, "no groups at all")] == [f["finding"] for f in raw]


def test_many_findings_go_to_the_synthesizer_and_its_groups_reach_the_report(client, repo, monkeypatch):
    lines = "\n".join(f"F | major | line {n} | seamless is vague {n} | cut it" for n in range(1, 6))
    seen = answers(monkeypatch, {**said({"egm-style": f"{lines}\nGOLDFISH: done"}),
                                 "egm-synthesizer": lambda r: "M | G1,G2,G3,G4,G5 | \"seamless\" says nothing | Remove it"})
    s = wait(client, start(client, repo, workflow="design-review", title="x", data={"doc": write_doc(repo), "preset": "implementation"}))
    synth = next(r for r in seen if r.agent == "egm-synthesizer")
    assert "G5 | major | line 5 | seamless is vague 5" in synth.prompt
    shown = s["waiting"].get("detail") or s.get("show") or ""
    assert '"seamless" says nothing' in shown and "seamless is vague" not in shown
    assert "line 1; line 2; line 3; line 4; line 5" in shown


def test_the_split_finds_the_plan_heading_outside_code_fences():
    assert da.split_at("# A\n```\n## Implementation Plan\n```\n## Work Breakdown\nx") == 5
    assert da.split_at("# A\n### Implementation Plan\n") is None


def test_harness_plan_gate_starts_a_goldfish_review_of_the_plan_and_waits(client, repo, monkeypatch):
    plan = "## Goal\nShow the owner.\nPLAN: ready"
    seen = answers(monkeypatch, {**said(), "harness-planner": lambda r: plan})
    tid = start(client, repo, workflow="harness", title="Owner")
    wait(client, tid)
    s = decide(client, tid, payload={"choice": "goldfish"})
    assert s["waiting"]["step"] == "plan_gate", s                       # the plan gate waits; the review is its own flow
    child = next(c for c in s["children"] if c["workflow"] == "design-review")
    r = wait(client, child["thread_id"])
    assert r["waiting"]["step"] == "report_gate", r
    fish = [q for q in seen if q.agent.startswith("egm-")]
    assert "egm-implementation-readiness" not in [q.agent for q in fish]        # a plan's preset: feature
    assert all("    2  Show the owner." in q.prompt for q in fish)
    shown = r["waiting"].get("detail") or r.get("show") or ""
    assert "no file here" in shown
    # resolve has nothing to edit: no agent runs, the gate says where to go
    r = decide(client, child["thread_id"], payload={"choice": "resolve"})
    assert r["waiting"]["step"] == "resolve_gate", r
    assert "egm-resolver" not in [q.agent for q in seen]
    assert "Nothing to resolve here" in (r["waiting"].get("detail") or r.get("show") or "")


def test_the_egm_content_is_whole_read_only_and_credited():
    import re

    from keel_engine.runtime import lenses
    from test_content import CONTENT, front_matter

    skills = {d.name for d in (CONTENT / "skills").glob("egm-*")}
    assert skills == {"egm-bootstrap", "egm-brainstorm", "egm-draft", "egm-drift-report", "egm-resolve", "egm-review", "egm-review-loop"}
    for s in skills:
        assert "**In keel.**" in (CONTENT / "skills" / s / "SKILL.md").read_text()
        assert front_matter(CONTENT / "skills" / s / "SKILL.md")["license"] == "Apache-2.0"
    assert {f.name for f in (CONTENT / "skills/egm-review/references").glob("*-standard.md")} >= {
        "zoom-out-standard.md", "feature-spec-standard.md", "implementation-readiness-standard.md"}
    assert "Apache License" in (CONTENT / "skills/egm-review/LICENSE").read_text()
    for name in GOLDFISH:                                   # a goldfish cannot write, has no memory, and answers in lines
        f = CONTENT / "agents" / f"{name}.md"
        fm = front_matter(f)
        assert "Write" in fm["disallowedTools"] and "Edit" in fm["disallowedTools"]
        assert fm["knowledge"] == {"sections": [], "code_graph": False, "memory": False, "strict": False}
        assert "GOLDFISH: done" in f.read_text() and "Core Mandates" in f.read_text()
    assert "Write" in front_matter(CONTENT / "agents/egm-resolver.md")["tools"]
    assert re.search(r"Elephant-Goldfish.*403d8881", (CONTENT / "NOTICE.md").read_text(), re.S)
    for lens in ("egm-zoom-out", "egm-design", "egm-codebase-details"):     # the code-review lenses: a short checklist
        got = lenses.load(lens)
        assert got["source"] == "keel" and got["description"] and 200 < len(got["checklist"]) < 1000
