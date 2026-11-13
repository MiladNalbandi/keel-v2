"""0.18.20 web research: the built-in web-researcher agent, the web tools its engine gets (Claude Code's WebSearch and
WebFetch, opencode's webfetch/websearch), and the guard letting a read-only `curl` through for the engines without
web tools. No model and no web call runs here."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from keel_engine import hook, rules
from keel_engine.models import fake
from keel_engine.models.base import AgentRequest
from keel_engine.runtime import guard_ctx, markers, prompts
from keel_engine.tools.agent_tools import ToolBox

CONTENT = Path(__file__).resolve().parents[2] / "content"
AGENT = CONTENT / "agents" / "web-researcher.md"

CURLS = [
    "curl -sL 'https://html.duckduckgo.com/html/?q=pricingengine+webhook+eta'",
    'curl -sL "https://developers.example.com/docs/webhooks?a=1&b=2" | head -200',
    "curl -sL https://raw.githubusercontent.com/acme/sdk/main/openapi.json | python3 -m json.tool | grep -n eta",
    "curl -sIL https://api.example.com/v2/events",
]


def front(f: Path) -> dict:
    return yaml.safe_load(re.match(r"\A---\n(.*?)\n---\n", f.read_text(), flags=re.S).group(1))


def test_the_web_researcher_is_a_read_only_agent_with_web_tools():
    fm = front(AGENT)
    assert fm["name"] == "web-researcher" and fm["description"]
    tools = [t.strip() for t in fm["tools"].split(",")]
    assert tools == ["Read", "Grep", "Glob", "Bash", "WebSearch", "WebFetch"]
    assert "Write" in fm["disallowedTools"] and "Edit" in fm["disallowedTools"]
    assert isinstance(fm["maxTurns"], int) and fm["maxTurns"] >= 30
    body = AGENT.read_text()
    # never stops after one failed URL: searches first, five sources, a table of what was tried, the curl fallback
    for must in ("WebSearch", "WebFetch", "five", "CONFIRMED", "curl -sL", "html.duckduckgo.com", "OpenAPI",
                 "changelog", "Postman", "Stack Overflow", "raw.githubusercontent.com", "CONFIDENCE: high | medium | low"):
        assert must in body, must


def test_web_tools_come_from_the_agent_file():
    assert prompts.web_tools("web-researcher") == ["WebSearch", "WebFetch"]
    assert prompts.web_tools("investigator") == []
    assert prompts.web_tools("no-such-agent") == []


def test_confidence_is_a_registered_marker():
    assert markers.REGISTRY["CONFIDENCE"] == ("high", "medium", "low")
    assert markers.parse("...\nCONFIDENCE: Medium — two sources agree", ["CONFIDENCE"]) == {"CONFIDENCE": "medium"}


def test_the_fake_web_researcher_answers_with_sources_and_confidence():
    req = AgentRequest(agent="web-researcher", system="", prompt="go", root="/tmp", phase="spec",
                       model={"provider": "fake"}, toolbox=ToolBox("/tmp", "spec"))
    _path, _content, text, _data = fake._canned(req)
    assert "https://" in text and markers.parse(text, ["CONFIDENCE"]) == {"CONFIDENCE": "medium"}


def fake_claude(tmp_path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    bin_ = tmp_path / "claude"
    res = {"type": "result", "result": "done", "usage": {}}
    bin_.write_text(f"#!/bin/sh\ncat > /dev/null\necho \"$@\" > {out}/argv\necho '{json.dumps(res)}'\n")
    bin_.chmod(0o755)
    return out


def flag(argv: list[str], name: str) -> list[str]:
    return argv[argv.index(name) + 1].split(",")


@pytest.mark.parametrize("cmd", CURLS)
def test_a_read_only_curl_passes_the_guard_in_the_spec_phase(cmd, tmp_path):
    assert rules.check_bash("spec", cmd, {}).ok
    ctx = {"root": str(tmp_path), "phase": "spec", "unlocks": []}
    assert hook.decide("Bash", {"command": cmd}, ctx) is None
    # and in a read-only run (no file changes, but reading the web is no change)
    assert hook.decide("Bash", {"command": cmd}, {**ctx, "readonly": True}) is None


@pytest.mark.parametrize("cmd", ["curl -sL -o page.html https://example.com", "curl -sLO https://example.com/a.zip",
                                 "curl -s --output x.json https://example.com", "curl -sL --remote-name https://e.com/f",
                                 "wget https://example.com/a.zip", "wget -q -O spec.yaml https://example.com/spec.yaml"])
def test_a_read_only_run_refuses_a_download_into_a_file(cmd):
    # 0.18.20 fix: run mode readonly let `curl -o` and `wget` write files (only redirects and rm/mv/... were caught)
    from keel_engine.runtime import run_mode
    assert run_mode.readonly_bash(cmd)


@pytest.mark.parametrize("cmd", [*CURLS, "wget -qO- https://example.com", "wget -O - https://example.com",
                                 "curl -sL -o /dev/null -w '%{http_code}' https://example.com"])
def test_a_read_only_run_still_reads_the_web(cmd):
    from keel_engine.runtime import run_mode
    assert run_mode.readonly_bash(cmd) is None


# ------------------------------------------------------------------ the spike flow (fake model)

def test_the_spikes_web_findings_reach_the_researchers_and_the_findings(client, repo, monkeypatch):
    from conftest import start, wait

    frame = ("Question: what is PricingEngine's ETA field called in the webhook?\nOutside the repo: the webhook payload.\n"
             "```json\n" + json.dumps([{"title": "map the field from the webhook"}, {"title": "poll the API"}]) + "\n```")
    web = ("CONFIRMED\n- `estimated_delivery_date`: https://developers.example.com/webhooks \"the latest ETA\"\n"
           "| tried | result |\n|---|---|\n| search: pricingengine webhook eta | 4 hits |\n\nCONFIDENCE: medium")
    real, seen = fake._plan, []

    def plan(req):
        seen.append(req)
        text = {"explorer": frame, "web-researcher": web}.get(req.agent)
        if req.agent == "investigator":
            text = "RECOMMEND: map the field" if req.step_name.startswith("findings") else "Measured.\nVERDICT: promising"
        return (None, "", text, {}) if text else real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine ETA"))
    assert s["waiting"]["step"] == "decide"
    assert [r.agent for r in seen][:2] == ["explorer", "web-researcher"]
    researcher = next(r for r in seen if r.agent == "web-researcher")
    assert "what is PricingEngine's ETA field called" in researcher.prompt      # it reads the frame's question and angles
    research = [r for r in seen if r.agent == "investigator" and r.item]
    assert len(research) == 2 and all("https://developers.example.com/webhooks" in r.prompt for r in research)
    findings = next(r for r in seen if r.step_name.startswith("findings"))
    assert "https://developers.example.com/webhooks" in findings.prompt and "VERDICT: promising" in findings.prompt


def test_a_step_that_keeps_its_answer_is_not_the_spec_writer(client, repo, monkeypatch):
    """0.18.21 fix: a `keep: true` step in the spec phase (the spike's frame and web_docs, feature's web steps) writes text
    for the next steps, not the spec (prompts.spec_writer): its prompt does not ask for a spec or questions, and keel does
    not read criteria from its answer, even when that answer lists some."""
    from conftest import start, wait
    from test_flows_base import values

    frame = ("Question: which ETA field?\n- **AC-1** [API] the ETA is shown\n"
             "```json\n" + json.dumps([{"title": "map the field"}]) + "\n```")
    real, seen = fake._plan, []

    def plan(req):
        seen.append(req)
        text = {"explorer": frame, "web-researcher": "Nothing outside.\nCONFIDENCE: high"}.get(req.agent)
        if req.agent == "investigator":
            text = "RECOMMEND: map it" if req.step_name.startswith("findings") else "VERDICT: promising"
        return (None, "", text, {}) if text else real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    tid = start(client, repo, workflow="spike", title="ETA field")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "decide"
    for r in seen[:2]:
        assert r.agent in ("explorer", "web-researcher")
        assert "Write the spec under" not in r.prompt and "keel-questions block (max 4" not in r.prompt, r.agent
    assert not values(client, tid).get("acs")


def test_the_spikes_agents_write_no_spec_and_its_gate_offers_spec_or_drop(client, repo, monkeypatch):
    """The spike's agents (frame, web research, researchers, findings) are not spec writers: they were told to write a
    spec under docs/specs/, and keel read criteria from the frame's answer, so the spike held criteria and a spec file
    of its own before the person chose "spec"."""
    from conftest import start, wait

    real, seen = fake._plan, []

    def plan(req):
        seen.append(req)
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine ETA"))
    assert s["waiting"]["step"] == "decide" and s["waiting"]["choices"] == ["spec", "drop"], s["waiting"]
    assert s["acs"] == [] and not s.get("spec")
    assert [p.name for p in (Path(repo) / "docs" / "specs").glob("*.md")] in ([], ["README.md"])
    assert seen and all("Write the spec under" not in r.prompt and "keel-questions block (max 4" not in r.prompt for r in seen)


def test_a_long_findings_answer_reaches_the_report_whole(client, repo, monkeypatch):
    """The report took the findings from last_answer, which is cut at 8 000 characters: a spike's findings lost their
    end (the recommendation, the sources) on the decide gate and in the evidence a feature starts from."""
    from conftest import start, wait

    real = fake._plan
    long = "## Findings (evidence)\n" + "\n".join(f"- claim {n}: src/app/Eta.kt:{n}" for n in range(900)) + "\nRECOMMEND: map the field"
    assert len(long) > 20_000

    def plan(req):
        if req.agent == "investigator" and req.step_name.startswith("findings"):
            return None, "", long, {}
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine ETA"))
    assert s["waiting"]["step"] == "decide"
    assert "claim 899: src/app/Eta.kt:899" in s["waiting"]["detail"] and "RECOMMEND: map the field" in s["waiting"]["detail"]


def test_a_spikes_evidence_reaches_the_features_spec_step_unshortened(client, repo, monkeypatch):
    """The spike's report (findings + each angle's answer, word for word) seeds the feature flow's evidence. It went into
    the child's request, which is cut at 8 000 characters: the spec step lost the angles' file:line evidence. The
    request still carries its start; the steps that write the spec get the whole evidence (up to EVIDENCE_MAX)."""
    import time
    from conftest import decide, start, wait

    real, seen = fake._plan, []
    angle = "\n".join(f"- inbound ETA path hop {n}: src/webhook/EtaHandler.kt:{n}" for n in range(250))

    def plan(req):
        seen.append(req)
        if req.agent == "investigator" and req.item:
            return None, "", f"## Findings (evidence)\n{angle}\nANGLE-END {req.item['title']}\nVERDICT: promising", {}
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine ETA"))
    decide(client, s["thread_id"], "approve", payload={"choice": "spec"})
    deadline = time.time() + 30
    while time.time() < deadline and not any(r.step_name == "spec" for r in seen):
        time.sleep(0.05)
    spec = next(r for r in seen if r.step_name == "spec")
    request = spec.prompt.split("What the user asked for:\n", 1)[1]
    assert "[cut at 8000 of" in request                     # every agent's request stays short
    assert spec.prompt.count("src/webhook/EtaHandler.kt:249") >= 2       # both angles, whole, in the evidence block
    assert all(f"ANGLE-END {t}" in spec.prompt for t in fake.SPIKE_ANGLES)        # 0.18.23 the fake frame's angles
    assert "Carry every claim with its file:line or URL into the spec's Findings (evidence)" in spec.prompt
    red = [r for r in seen if r.agent == "test-author"]
    assert all("Evidence handed over" not in r.prompt for r in red)


def test_the_spikes_frame_asks_about_the_decision_once_and_reframes_with_the_answers(client, repo, monkeypatch):
    from conftest import decide, start, wait

    # 0.18.23 a question about the decision (what evidence decides it), not how to build it
    qs = [{"id": "evidence", "question": "What evidence decides whether we build it?", "options": [
        {"label": "The vendor's documented fields", "recommended": True}, {"label": "A measured sample of real payloads"}]}]
    angles = "```json\n" + json.dumps([{"title": "map the field"}, {"title": "poll the API"}]) + "\n```"
    real, seen = fake._plan, []

    def plan(req):
        seen.append(req)
        if req.step_name == "frame the question":
            first = sum(r.step_name == "frame the question" for r in seen) == 1
            return None, "", f"Question: the ETA.\n{angles}" + (f"\n```keel-questions\n{json.dumps(qs)}\n```" if first else ""), {}
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    tid = start(client, repo, workflow="spike", title="PricingEngine ETA")
    s = wait(client, tid)
    assert s["waiting"]["step"] == "frame_questions" and s["waiting"]["kind"] == "clarify"
    assert [q["id"] for q in s["waiting"]["questions"]] == ["evidence"]
    s = decide(client, tid, "approve", payload={"answers": {"evidence": "The vendor's documented fields"}})
    frames = [r for r in seen if r.step_name == "frame the question"]
    assert len(frames) == 2 and "→ The vendor's documented fields" in frames[1].feedback
    assert s["waiting"]["step"] == "decide" and s["waiting"]["choices"] == ["spec", "drop"]


def test_the_spikes_frame_asks_about_the_decision_and_leaves_how_to_build_it_to_the_spec(client, repo, monkeypatch):
    # 0.18.23 a real frame asked "overwrite one column or keep history?" and "DB only, outbound or API?": the feature's
    # spec interview asks those; the frame asks what the decision needs
    from keel_engine.workflows.templates import get_template
    from conftest import start, wait

    frame = get_template("spike").step("frame")
    text = " ".join(frame.instructions.split())
    assert "Ask only about the DECISION the spike must support: do we need it at all, for whom, what certainty or what " \
           "evidence decides it, deadline or cost limits, which options to compare." in text
    assert "Never ask how to build it (which column or table, overwrite or keep history, which systems or APIs read it" in text
    assert 'those belong in the feature\'s spec interview, so list them under "Open questions for the spec"' in text
    findings = get_template("spike").step("findings")
    assert "the framing's open questions for the spec" in " ".join(findings.instructions.split())
    real, seen = fake._plan, []

    def plan(req):
        seen.append(req)
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine ETA"))
    assert s["waiting"]["step"] == "decide"                       # the fake asks nothing: straight to the result
    framed = next(r for r in seen if r.step_name == "frame the question")
    assert "Ask only about the DECISION" in framed.prompt
    said = fake._plan(framed)[2]
    assert "Decision: whether to build it" in said and "Open questions for the spec:" in said


# ------------------------------------------------------------------ the feature flow (fake model)

def test_web_is_a_registered_marker():
    assert markers.REGISTRY["WEB"] == ("yes", "no")
    assert markers.parse("needs the vendor's payload\nWEB: Yes — the webhook", ["WEB"]) == {"WEB": "yes"}


def _feature(client, repo, monkeypatch, title, request=""):
    from conftest import start, wait

    real, seen = fake._plan, []

    def plan(req):
        seen.append(req)
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    tid = start(client, repo, title=title, request=request)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "spec_gate", s
    skipped = {e["step"] for e in client.bus.of(tid, "step.finished") if e["data"].get("skipped")}
    return seen, skipped


def test_a_feature_on_a_vendor_webhook_researches_the_web_before_the_spec(client, repo, monkeypatch):
    seen, skipped = _feature(client, repo, monkeypatch, "Show the ETA",
                             "Show the ETA field that the carrier's vendor webhook sends on each shipment update.")
    assert [r.step_name for r in seen][:4] == ["outside docs needed?", "research the outside docs", "interview", "spec"]
    assert "web_docs" not in skipped
    check, web, interview, spec = seen[0], seen[1], seen[2], seen[3]
    assert check.agent == "explorer" and "Write the spec under" not in check.prompt
    assert web.agent == "web-researcher" and "Needs from outside: the payload fields" in web.prompt  # what web_check found
    # the interview asks only what the research could not confirm
    assert "https://docs.example.com/webhooks" in interview.prompt and "Write the spec under" not in interview.prompt
    # the confirmed facts reach the spec writer, with the rule for what is not confirmed
    assert "https://docs.example.com/webhooks" in spec.prompt and "CONFIDENCE: medium" in spec.prompt
    assert "open question" in spec.prompt and "Write the spec under" in spec.prompt


def test_a_plain_refactor_does_not_research_the_web(client, repo, monkeypatch):
    seen, skipped = _feature(client, repo, monkeypatch, "Split the scores module",
                             "Refactor: split src/scores/__init__.py into ranking.py and io.py.")
    assert [r.step_name for r in seen][:3] == ["outside docs needed?", "interview", "spec"]
    assert not any(r.agent == "web-researcher" for r in seen) and "web_docs" in skipped
    assert "https://docs.example.com/webhooks" not in seen[2].prompt


# ------------------------------------------------------------------ 0.18.23 the spike's report reads in a glance

def test_the_spike_report_starts_with_its_summary_and_strips_the_angles_working(client, repo):
    from conftest import start, wait

    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine webhook ETA"))
    assert s["waiting"]["step"] == "decide"
    report = s["waiting"]["detail"]
    lines = report.splitlines()
    assert lines[0] == "# Spike: PricingEngine webhook ETA" and lines[2] == "## Summary"
    summary = report.split("## Findings", 1)[0]
    assert len([x for x in summary.splitlines() if x.strip()]) <= 14
    heads = [x.split(":**")[0] for x in summary.splitlines() if x.startswith("- **")]
    assert heads == ["- **DECISION", "- **BEFORE BUILDING", "- **CONFLICTS", "- **COST", "- **NEXT"]
    assert "- **DECISION:** build it on the existing tracking_subscriptions column." in summary
    assert "  1. Does the vendor send the ETA on every update?" in summary and "- **NEXT:** spec" in summary
    # the summary comes first: before the findings and the angles
    assert report.index("## Summary") < report.index("## Findings") < report.index("## Angles")
    # the angles keep their ANGLE REPORT only: no tool-call log, no narration
    for noise in ("●", "│", "└", "/ Search", "I'll start with", "Now the entity"):
        assert noise not in report, noise
    assert "<details><summary>reuse the subscription column · VERDICT promising</summary>" in report    # folded
    assert "- where the ETA is stored: trackings.estimated_delivery_date — src/db/Tracking.kt:30" in report


def test_the_angles_conflict_shows_under_conflicts_and_one_verdict_line_is_counted(client, repo):
    from conftest import start, wait

    s = wait(client, start(client, repo, workflow="spike", title="PricingEngine webhook ETA"))
    summary = s["waiting"]["detail"].split("## Findings", 1)[0]
    conflicts = summary.split("**CONFLICTS:**", 1)[1].split("**COST:**", 1)[0]
    assert "where the ETA is stored" in conflicts and "tracking_subscriptions" in conflicts and "trackings" in conflicts
    verdicts = [x for x in s["waiting"]["detail"].splitlines() if x.startswith("VERDICT:")]
    assert verdicts == ["VERDICT: 1 of 2 angles promising (1 possible); recommendation: reuse the subscription column"]


def test_keel_finds_a_conflict_the_findings_missed(client, repo, monkeypatch):
    from conftest import start, wait

    real = fake._plan

    def plan(req):
        if req.agent == "investigator" and req.step_name.startswith("findings"):
            return None, "", "DECISION: build it.\nCONFLICTS: none\nCOST: small\nNEXT: spec\nRECOMMEND: build it", {}
        return real(req)
    monkeypatch.setattr(fake, "_plan", plan)
    s = wait(client, start(client, repo, workflow="spike", title="ETA"))
    conflicts = s["waiting"]["detail"].split("**CONFLICTS:**", 1)[1].split("**COST:**", 1)[0]
    assert "where the eta is stored:" in conflicts and "migration needed:" in conflicts
    assert "keel found it in the angles' facts" in conflicts


def test_the_report_helpers_strip_logs_count_verdicts_and_read_facts():
    from keel_engine.runtime import spike_report as sr

    raw = "I'll look.\n● cat -n a.kt\n│ 1 x\n└ 9 lines…\n/ Search (grep) eta\nLet me check.\nThe ETA is set at a.kt:1.\n```\n● kept in code\n```"
    assert sr.clean(raw) == "The ETA is set at a.kt:1.\n```\n● kept in code\n```"
    assert sr.angle_text("working\nANGLE REPORT\nAnswer: x\nVERDICT: no") == "Answer: x\nVERDICT: no"
    rs = [{"markers": {"VERDICT": v}} for v in ("promising", "promising", "possible", "promising")]
    assert sr.verdict_line(rs, "map it") == "VERDICT: 3 of 4 angles promising (1 possible); recommendation: map it"
    assert sr.verdict_line([{"markers": {}}], "") == "VERDICT: 0 of 1 angles with a verdict (1 without a verdict)"
    assert markers.parse("VERDICT: Promising — the column exists", ["VERDICT"]) == {"VERDICT": "promising"}
    assert markers.REGISTRY["CONFLICTS"] == ("none", "found")
