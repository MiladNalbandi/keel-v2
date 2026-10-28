"""keel 0.18.65 Settings › Review: the `review:` block of .keel/config.yml, the lens files and the final review's model.

A field report: keel's last whole-branch review passed a change on the flow's model (a Sonnet) in a few thousand tokens,
and a review on Opus found what it missed. The final review steps now run on a stronger model than the project's by
default; the Review page's reviewers keep the project's model until Settings › Review says otherwise."""

import yaml
import pytest

from keel_engine.runtime import lenses, review_config
from keel_engine.runtime.compiler import Compiler
from keel_engine.runtime.state import ThreadContext
from keel_engine.workflows.templates import get_template

SONNET = {"provider": "claude", "mode": "subscription", "model": "sonnet"}


def project(tmp_path, config="version: 4\n"):
    root = tmp_path / "p"
    (root / ".keel").mkdir(parents=True)
    (root / ".keel/config.yml").write_text(config)
    return root


# ------------------------------------------------------------------ the lens files

def test_one_format_for_keel_and_project_lenses(tmp_path):
    root = project(tmp_path)
    keel = lenses.load("data-migration")
    assert keel["source"] == "keel" and keel["title"] == "Data migration and backfill"
    assert keel["description"].startswith("defaults and NOT NULL") and keel["checklist"].startswith("Data migration")
    assert "Reference" not in keel["checklist"]
    names = lenses.names()
    for want in ("data-migration", "idempotency-retry", "error-handling-retry", "config-secrets", "test-quality",
                 "silent-failure", "contract-types", "consumers-config"):
        assert want in names
    # a project lens: the project's own wins over keel's of the same name; a hand-written brief with no sections is
    # all checklist (the hunt reads the same file)
    (root / ".keel/lenses").mkdir()
    (root / ".keel/lenses/data-migration.md").write_text("# Our migrations\n\nours\n\n## Checklist\n\n- never drop a column\n")
    (root / ".keel/lenses/money.md").write_text("Money\nprices are integers in cents\n")
    assert lenses.load("data-migration", root)["checklist"] == "- never drop a column"
    assert lenses.load("money", root)["checklist"] == "prices are integers in cents"
    assert lenses.names(root)[-1] == "money" and lenses.names(root).count("data-migration") == 1


def test_a_new_lens_is_a_file_name_never_a_path(tmp_path):
    root = project(tmp_path)
    assert lenses.slug("Money rounding!") == "money-rounding" and lenses.slug("../../etc/passwd") == "etc-passwd"
    for bad in ("../x", "a/b", "", ".hidden", "A"):
        with pytest.raises(lenses.LensError):
            lenses.write(root, bad, "x", "", "- a")
    out = lenses.write(root, "money-rounding", "Money rounding", "cents", "amounts are integers\n- one place rounds")
    text = (root / ".keel/lenses/money-rounding.md").read_text()
    assert text == "# Money rounding\n\ncents\n\n## Checklist\n\n- amounts are integers\n- one place rounds\n"
    assert out["path"] == ".keel/lenses/money-rounding.md"
    with pytest.raises(lenses.LensError) as e:
        lenses.write(root, "money-rounding", "Money rounding", "", "- a")
    assert e.value.status == 409
    with pytest.raises(lenses.LensError):
        lenses.save_text(root, "money-rounding", "# no checklist here\n")
    assert lenses.save_text(root, "money-rounding", text.replace("one place", "a single place"))["checklist"].endswith("a single place rounds")


# ------------------------------------------------------------------ the settings

def test_nothing_written_is_todays_two_reviewers(tmp_path):
    root = project(tmp_path)
    v = review_config.view(str(root), {"default": SONNET})
    revs = v["config"]["reviewers"]
    assert [r["name"] for r in revs] == ["correctness and security", "tests, the description and design"]
    assert revs[0]["lenses"] == ["data-migration", "idempotency-retry", "error-handling-retry"]
    assert revs[1]["lenses"] == ["config-secrets", "test-quality"]
    assert v["find_text"].startswith("Two keel reviewers read the change (A: correctness and security, B: tests, the")
    # the reviewers keep the project's model; the checker and the final review get a stronger one
    assert v["uses"]["reviewers"] == [SONNET, SONNET]
    assert v["uses"]["checker"] == {**SONNET, "model": "opus", "effort": "high"}
    assert v["uses"]["final"] == {**SONNET, "model": "opus", "effort": "high"}
    rows = {x["name"]: x for x in v["lenses"]}
    assert rows["data-migration"]["reviewer"] == "A" and rows["silent-failure"]["reviewer"] is None
    assert v["ship_lenses"][-1] == "consumers-config" and v["stored"] == {}


def test_stronger_is_opus_high_for_claude_and_high_effort_elsewhere():
    assert review_config.raised(SONNET) == {**SONNET, "model": "opus", "effort": "high"}
    assert review_config.raised({**SONNET, "model": "haiku", "effort": "low"})["model"] == "opus"
    assert review_config.raised({**SONNET, "model": "opus"})["effort"] == "high"
    codex = {"provider": "codex", "mode": "subscription", "model": "gpt-5", "effort": "medium"}
    assert review_config.raised(codex) == {**codex, "effort": "high"}
    fake = {"provider": "fake", "mode": "api", "model": "fake"}
    assert review_config.raised(fake) == fake


def test_writing_keeps_the_rest_of_the_file_and_checks_what_it_writes(tmp_path):
    root = project(tmp_path, "version: 4\n# the ship lenses\nreview:\n  lenses: [correctness]\nknowledge:\n  build: true\n")
    revs = review_config.effective(str(root))["reviewers"]
    three = [*revs, {"name": "money", "focus": "rounding, currency, totals that must add up", "model": "stronger",
                     "effort": "high", "agent": "branch-reviewer", "lenses": ["contract-types"]}]
    v = review_config.write(str(root), {"reviewers": three, "checker": {"should_fix": False}, "fix": {"flow": "ask"},
                                        "final": {"model": "default"}}, {"default": SONNET})
    raw = yaml.safe_load((root / ".keel/config.yml").read_text())
    assert raw["knowledge"] == {"build": True} and raw["review"]["lenses"] == ["correctness"]
    assert raw["review"]["reviewers"][2] == {"name": "money", "focus": "rounding, currency, totals that must add up",
                                             "model": "stronger", "effort": "high", "agent": "branch-reviewer",
                                             "lenses": ["contract-types"]}
    assert raw["review"]["checker"] == {"should_fix": False} and raw["review"]["fix"] == {"flow": "ask"}
    assert v["find_text"].startswith("Three keel reviewers read the change (A: correctness and security, B: tests, "
                                     "the description and design, C: money).")
    assert v["uses"]["final"] == SONNET and v["uses"]["reviewers"][2]["model"] == "opus"
    for bad, says in [({"reviewers": []}, "1 to 4"), ({"reviewers": [{"name": "a", "focus": "b", "lenses": ["nope"]}]}, "no lens"),
                      ({"reviewers": [{"name": "a", "focus": "b", "lenses": ["test-quality"]},
                                      {"name": "c", "focus": "d", "lenses": ["test-quality"]}]}, "one reviewer each"),
                      ({"fix": {"flow": "big"}}, "fix.flow"), ({"checker": {"effort": "huge"}}, "effort"),
                      ({"colour": 1}, "Unknown")]:
        with pytest.raises(review_config.ReviewConfigError) as e:
            review_config.write(str(root), bad)
        assert says in e.value.error
    # back to keel's default: Settings › Review's parts go, the ship's lenses stay
    review_config.write(str(root), {"reset": True})
    raw = yaml.safe_load((root / ".keel/config.yml").read_text())
    assert raw["review"] == {"lenses": ["correctness"]} and raw["knowledge"] == {"build": True}


def test_a_new_lens_goes_on_its_reviewer(tmp_path):
    root = project(tmp_path)
    out = review_config.new_lens(str(root), {"title": "Money rounding", "reviewer": "B",
                                             "checklist": "amounts are integers in cents\none place rounds"})
    assert out["lens"]["path"] == ".keel/lenses/money-rounding.md" and (root / ".keel/lenses/money-rounding.md").is_file()
    assert {x["name"]: x for x in out["view"]["lenses"]}["money-rounding"]["reviewer"] == "B"
    assert yaml.safe_load((root / ".keel/config.yml").read_text())["review"]["reviewers"][1]["lenses"][-1] == "money-rounding"
    with pytest.raises(review_config.ReviewConfigError):
        review_config.new_lens(str(root), {"title": "x", "reviewer": "D", "checklist": "a"})
    without = tmp_path / "bare"
    without.mkdir()
    with pytest.raises(review_config.ReviewConfigError) as e:
        review_config.write(str(without), {"fix": {"flow": "quick"}})
    assert e.value.status == 409


# ------------------------------------------------------------------ the final review's model

def compiler_for(root, models):
    wf = get_template("feature")
    return Compiler(ThreadContext(thread_id="t", project_id="p", root=str(root), workflow=wf, title="t", models=models)), wf


def test_a_project_on_sonnet_gets_opus_for_the_final_review_step(tmp_path, monkeypatch):
    monkeypatch.delenv("KEEL_FAKE", raising=False)
    root = project(tmp_path)
    c, wf = compiler_for(root, {"default": SONNET, "branch-reviewer": SONNET, "implementer": SONNET})
    step = {s.id: s for s in wf.steps}
    assert c._model({}, step["code_review"], "branch-reviewer") == {**SONNET, "model": "opus", "effort": "high"}
    assert c._model({}, step["green"], "implementer") == SONNET
    ship = {s.id: s for s in get_template("ship").steps}
    assert c._model({}, ship["review"], "lens-reviewer")["model"] == "opus"
    # the Flow page's model table says the same
    from keel_engine.runtime import thread_models
    table = {s["id"]: s for s in thread_models.view(c.ctx.models, {}, wf, str(root))["steps"]}
    assert table["code_review"]["model"]["model"] == "opus" and table["green"]["model"] == SONNET
    # the person's pick for the step, and a model the flow was switched to, win
    c.ctx.models["step:code_review"] = {**SONNET, "effort": "low"}
    assert c._model({}, step["code_review"], "branch-reviewer")["effort"] == "low"
    c.ctx.models.pop("step:code_review")
    assert c._model({"model_override": SONNET}, step["code_review"], "branch-reviewer") == SONNET
    # Settings › Review: the final review on the project's model, or on one it names
    (root / ".keel/config.yml").write_text("version: 4\nreview:\n  final: {model: default}\n")
    assert c._model({}, step["code_review"], "branch-reviewer") == SONNET
    (root / ".keel/config.yml").write_text("version: 4\nreview:\n  final:\n    model: {provider: codex, mode: subscription, model: gpt-5}\n    effort: high\n")
    assert c._model({}, step["code_review"], "branch-reviewer") == {"provider": "codex", "mode": "subscription", "model": "gpt-5", "effort": "high"}


def test_the_routes_the_api_calls(client, tmp_path):
    root = project(tmp_path)
    v = client.post("/review-config/view", json={"root": str(root), "models": {"default": SONNET}}).json()
    assert len(v["config"]["reviewers"]) == 2 and v["uses"]["checker"]["model"] == "opus"
    r = client.post("/review-config/lenses", json={"root": str(root), "lens": {"title": "Currency in every price",
                                                                              "reviewer": "A", "checklist": "- no amount without its currency"}})
    assert r.status_code == 200 and r.json()["lens"]["name"] == "currency-in-every-price"
    got = client.get("/review-config/lenses/currency-in-every-price", params={"root": str(root)}).json()
    assert got["source"] == "project" and got["checklist"] == "- no amount without its currency"
    assert client.get("/review-config/lenses/..%2F..%2Fx", params={"root": str(root)}).status_code in (400, 404)
    bad = client.put("/review-config", json={"root": str(root), "patch": {"fix": {"flow": "big"}}})
    assert bad.status_code == 400 and "fix.flow" in bad.json()["error"]
    ok = client.put("/review-config", json={"root": str(root), "patch": {"fix": {"flow": "quick"}}})
    assert ok.status_code == 200 and ok.json()["config"]["fix"]["flow"] == "quick"
    edit = client.put("/review-config/lenses/currency-in-every-price", json={"root": str(root), "text": "# x\n\n## Checklist\n\n- y\n"})
    assert edit.status_code == 200 and edit.json()["checklist"] == "- y"
