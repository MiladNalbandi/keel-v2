"""0.18.64 a spec handed over is kept, a send-back patches the last draft, spec numbers do not collide.

A real feature flow (after a clarify flow, with the spec pasted into its request) wrote the spec again and dropped a
risk the request named; a new spec got 001 while 001-… and 002-… existed (docs/plans/field-report-clarify.md, bugs 8
and 11).
"""

from pathlib import Path

from conftest import start, wait
from keel_engine.runtime import prompts, spec_check

HANDED = """---
id: clarify-2026-10-20
source: clarify
---
# Prices in euros

## Acceptance criteria
- **AC-1** [API] Given a shop order in dollars, when it is saved, then its price is stored in euros
- **AC-2** [API] Given an order with no price, when it is saved, then the api answers 400

## Risks and cost
- Old clients still send the price as a string; the parser must accept both for one release.
- The exchange rate table is filled once a day, so an order at midnight may use yesterday's rate.

## Out of scope
- Refunds in other currencies.
"""

KEPT = """# Prices in euros

## Acceptance criteria
- **AC-1** [API] Given a shop order in dollars, when it is saved, then its price is stored in euros
- **AC-2** [API] Given an order with no price, when it is saved, then the api answers 400

## Risks and cost
- Old clients still send the price as a string: the parser accepts both for one release.
- The exchange rate table is filled once a day; an order at midnight may use yesterday's rate.

## Out of scope
- Refunds in other currencies
"""
RATE = "- The exchange rate table is filled once a day; an order at midnight may use yesterday's rate.\n"


def test_a_handed_over_spec_names_its_risks_criteria_and_out_of_scope_items():
    kinds = [k for k, _t in spec_check.kept_items(HANDED)]
    assert kinds.count("risk") == 2 and kinds.count("out-of-scope item") == 1 and kinds.count("criterion") == 2
    # a request names them as a "Risks:" label too; a spike's findings are evidence, not a spec
    assert spec_check.kept_items("Add euros.\n\nRisks:\n- refunds confuse the shop\n") == [("risk", "refunds confuse the shop")]
    assert spec_check.seeded("", {"seed": {"evidence": "# Spike\n## Findings\n- x\n## Risks\n- slow cache warmup"}}) == []
    assert [w for w, _d in spec_check.seeded("", {"seed": {"evidence": HANDED}})] == ["the handed-over document"]
    assert [w for w, _d in spec_check.seeded("", {"spec_at_start": HANDED})] == ["the spec this flow started from"]


def test_a_spec_that_keeps_everything_in_close_words_passes_and_a_lost_risk_is_a_fix():
    data = {"seed": {"evidence": HANDED}}
    assert spec_check.lost(KEPT, "", data) == []
    found = spec_check.lost(KEPT.replace(RATE, "").replace("- Refunds in other currencies\n", ""), "", data)
    assert [f["level"] for f in found] == ["fix", "fix"]
    assert "the risk “The exchange rate table is filled once a day" in found[0]["text"]
    assert "the out-of-scope item “Refunds in other currencies.”" in found[1]["text"]
    assert found[0]["text"].startswith("Lost from the handed-over document")
    # the person's own request: what it names is kept too; nothing handed over, nothing to check
    asked = "Prices in euros.\n\n## Risks\n- midnight orders may use yesterday's exchange rate\n"
    assert "Lost from the request" in spec_check.lost(KEPT.replace(RATE, ""), asked, {})[0]["text"]
    assert spec_check.lost(KEPT.replace(RATE, ""), "Prices in euros.", {}) == []
    assert spec_check.lost("", asked, {}) == []


def test_many_lost_items_are_counted_after_the_first_ones():
    risks = "\n".join(f"- risk number {n} about the orders table and prices" for n in range(12))
    found = spec_check.lost("# Spec\n\n## Risks\n- none\n", f"## Risks\n{risks}\n", {})
    assert len(found) == spec_check.LOST_SHOWN + 1 and found[-1]["text"].startswith("4 more items")


def test_the_spec_writer_keeps_a_handed_over_spec_and_a_send_back_gets_the_draft(tmp_path):
    root = tmp_path / "repo"
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "001-prices.md").write_text(KEPT)
    block = prompts.seeded_block("what the user asked for")
    assert block.startswith("What the user asked for holds a spec") and "every risk" in block
    p = prompts.task_prompt(agent="explorer", phase="spec", step_name="spec", title="t", root=str(root), ac=None, acs=[],
                            feedback="keel's spec check found problems", spec="docs/specs/001-prices.md")
    assert "The spec docs/specs/001-prices.md exists from the last try" in p and "patch it, do not write it again" in p
    assert "The draft as it is now:\n````markdown\n# Prices in euros" in p and "yesterday's rate" in p
    # the draft is patched: no second file with the next number
    assert "Keep the spec in docs/specs/001-prices.md" in p and "as 002-<kebab-name>.md" not in p
    # answers to the interview's questions are no send-back
    q = prompts.task_prompt(agent="explorer", phase="spec", step_name="spec", title="t", root=str(root), ac=None, acs=[],
                            feedback="Answers to your questions:\n- q1: yes", spec="docs/specs/001-prices.md")
    assert "The draft as it is now" not in q


def test_spec_numbers_are_free_across_every_spec_folder_keel_knows(tmp_path):
    root = tmp_path / "repo"
    (root / ".keel" / "specs").mkdir(parents=True)
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "001-login.md").write_text("x")      # keel v1's folder next to the layout's
    (root / "specs").mkdir()
    (root / "specs" / "002-rank").mkdir()                             # a v1 spec folder of its own
    assert prompts.next_spec_number(root, ".keel/specs") == "003"
    # handed-over criteria still get the number
    p = prompts.task_prompt(agent="explorer", phase="spec", step_name="spec", title="t", root=str(root), ac=None,
                            acs=[{"id": "AC-1", "layer": "API", "title": "x"}], feedback=None)
    assert "as 003-<kebab-name>.md (its id 003" in p


def test_a_new_spec_with_a_taken_number_moves_to_the_next_free_one(tmp_path):
    root = tmp_path / "repo"
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "001-login.md").write_text("x")
    (root / "docs" / "specs" / "002-rank.md").write_text("x")
    (root / "docs" / "specs" / "001-prices.md").write_text("---\nid: 001\nslug: prices\n---\n# Prices\n")
    assert prompts.renumber_spec(root, "docs/specs/001-prices.md") == "docs/specs/003-prices.md"
    assert not (root / "docs" / "specs" / "001-prices.md").exists()
    assert (root / "docs" / "specs" / "003-prices.md").read_text().startswith("---\nid: 003\nslug: prices\n")
    assert prompts.renumber_spec(root, "docs/specs/003-prices.md") == "docs/specs/003-prices.md"     # free: kept
    assert prompts.renumber_spec(root, "docs/specs/prices.md") == "docs/specs/prices.md"             # no number


def _spy(monkeypatch, first_text):
    """The fake explorer's spec step: its first spec is `first_text`, the next one the fake's own; every prompt and
    feedback it got."""
    from keel_engine.models import fake as fake_mod
    real = fake_mod._plan
    seen = []

    def plan(req):
        if req.agent == "explorer" and req.phase == "spec" and req.step_name == "spec":
            seen.append((req.prompt, req.feedback))
            path, content, answer, data = real(req)
            if len(seen) == 1:
                acs = [{"id": "AC-1", "layer": "API", "title": "Given a shop order in dollars, when it is saved, then its "
                                                             "price is stored in euros"}]
                return path, first_text, answer, {"acs": acs, "spec": path}
            return path, content, answer, data
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    return seen


def test_a_spec_that_lost_a_risk_of_the_request_is_sent_back_once_with_its_draft(client, repo, monkeypatch):
    seen = _spy(monkeypatch, KEPT.replace(RATE, ""))
    request = "Prices in euros.\n\n" + HANDED.split("---\n", 2)[2]
    tid = start(client, repo, acs=None, request=request,
                settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "spec_check": True})
    s = wait(client, tid)
    assert len(seen) == 2
    first, second = seen
    assert "What the user asked for holds a spec (or part of one) already" in first[0]
    assert "Lost from the request: the risk “The exchange rate table is filled once a day" in second[1]
    assert "The draft as it is now:" in second[0] and "Old clients still send the price as a string" in second[0]
    assert s["waiting"]["step"] == "spec_gate"


def test_a_flow_started_from_a_spec_keeps_its_text_for_the_check(client, repo, monkeypatch):
    from keel_engine.models import fake as fake_mod
    real, prompts_seen = fake_mod._plan, []

    def plan(req):
        if req.agent == "explorer" and req.step_name == "spec":
            prompts_seen.append(req.prompt)
        return real(req)
    monkeypatch.setattr(fake_mod, "_plan", plan)
    spec = Path(repo) / "docs" / "specs" / "prices.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(HANDED)
    tid = start(client, repo, acs=None, data={"spec": "docs/specs/prices.md"})
    s = wait(client, tid)
    assert "The spec this flow started from holds a spec (or part of one) already" in prompts_seen[0]
    assert s["waiting"]["step"] == "spec_gate"
