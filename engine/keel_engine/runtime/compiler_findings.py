"""The Compiler's review steps: reviewer rounds, findings and the spec folders they read."""
from __future__ import annotations

import asyncio
import copy

from . import finding_decisions, init_gates, md, prompts, security_actions
from .. import rules
from ..tools import git, guard
from ..workflows.model import Step
from .actions import ActionInput
from .findings import key as finding_key, same as same_finding
from .gate_state import deep_gates
from .state import FlowState
from pathlib import Path

from . import compiler as _c      # the compiler module's own names (looked up when called: tests patch some)


class FindingsMixin:

    def _spec_put_back(self, state: FlowState, review: Step) -> list[str]:
        """The spec files keel's phase rule put back after this review's fix step (data.put_back)."""
        spec = str(state.get("spec") or "")
        fixers = [review.back] if review.back else [_c.fix_id(review.id)]
        paths = [p for f in fixers for p in ((state.get("data") or {}).get("put_back") or {}).get(f) or []]
        return list(dict.fromkeys(p for p in paths if p == spec or (p.endswith(".md") and "spec" in p.lower())))

    async def findings_step(self, i: int, fix: Step, state: FlowState):
        """After a review with blocking findings: ask; fix and review again, or go on with the user's reason (the
        findings are kept as dismissed). Nothing runs before the question, so answering it never re-runs anything.

        The fix is routed by payload.route: review-fix (default: implementer, tests, fix commit), coverage-fix
        (test-author, tests, coverage commit) or red (test-author, red commit). After it the flow goes on from the
        review's `redo` step (default the review itself). A review with `back` sends its findings to that step by
        itself; this question only comes once its `rounds` are used up, and approving sends them back once more.
        """
        ctx = self.ctx
        review = self.wf.steps[i]
        item = self._item(state, review)
        found = list(state.get("findings") or [])
        rounds = dict(state.get("review_rounds") or {})
        key = self._rounds_key(review, item)
        done_rounds = rounds.get(key, 0)
        gates = deep_gates(state)
        data = dict(state.get("data") or {})
        # 0.18.22 a finding the person already accepted or called not a finding for this review is not asked again
        gone = [t for d in data.get("dismissed_findings") or []
                if d.get("step") == review.id and d.get("item") == (item or {}).get("id") for t in d.get("findings") or []]
        if gone and found:
            # 0.18.71 the same problem said in other words is the same finding (same_finding), not a new one
            fresh = [f for f in found if not any(same_finding(f["text"], g) for g in gone)]
            if not fresh:
                gates["log"].append(f"{review.name}: {len(found)} finding(s) dismissed before, not asked again")
                if (review.agent or "") == "security-auditor":
                    await asyncio.to_thread(security_actions.accepted, ctx.project_id or ctx.root, ctx.root,
                                            [{"text": f["text"], "decision": "dismissed before", "reason": ""} for f in found])
                return {"gates": gates, "findings": [],
                        "note": f"{review.name}: {len(found)} finding(s) dismissed before"}, self.nav.after(i)
            found = fresh
        listed = "\n".join(f"- **[{f['lens']}]** {f['text']}" for f in found)
        again = f" (fix round {done_rounds + 1})" if done_rounds else ""
        limit = review.rounds or (2 if review.back else None)
        past = f"\n\n{done_rounds} fix round(s) done, the limit is {limit}: the check and the change disagree, and " \
               f"repeating may not settle it. Deciding is yours." if limit and done_rounds >= limit else ""
        # 0.18.71 why sending back again would not help: the same findings, a fix round that changed nothing, a spec file
        # the fix phase may not change (keel put it back each round)
        trail = (data.get("review_trail") or {}).get(key) or {}
        why_stuck = []
        if trail.get("same") and done_rounds:
            why_stuck.append("These are the same findings as the last round: the fix did not settle them.")
        if trail.get("no_change") and done_rounds:
            why_stuck.append("The last fix round changed nothing (no new commit): another round of the same will not either.")
        spec_put_back = self._spec_put_back(state, review)
        if spec_put_back:
            why_stuck.append(f"keel put back {', '.join(md.code(p) for p in spec_put_back)} after the fix round: the fix phase "
                             "may not change the spec. Choose **amend spec** for a finding about the spec (the spec "
                             "phase changes it), or say it is not a finding.")
        if why_stuck:
            past = ("\n\n" + " ".join(why_stuck) + past).rstrip()
        fix_txt = (f"{md.code(review.back)} gets these findings once more, then {review.name} runs again." if review.back else
                   f"the implementer fixes these findings (payload route: review-fix, coverage-fix or red picks "
                   f"who and how), the tests run, the fix is committed, and the flow goes on from "
                   f"{review.redo or review.name}.")
        # keel v1: the person decides each finding (fix / accept / not a finding / amend the spec), as questions
        can_amend = "amend_entry" in self.nav.index and bool(state.get("spec"))
        asked = finding_decisions.questions(found, can_amend, spec_put_back if can_amend else None)
        each = ("Or decide each finding below: fix, accept (with a reason), not a finding (with a reason)"
                + (", or amend spec (a spec amendment starts)" if can_amend else "")
                + f". A finding you do not mark follows the button: {'Send back' if review.back else 'Fix them'} fixes it, "
                  "Go on anyway accepts it with your reason.")
        if len(found) > len(asked):
            each += f" Only the first {len(asked)} are asked one by one."
        answer, extra = self._ask(state, {
            "step": fix.id, "kind": "gate", "title": f"{review.name}: {len(found)} blocking finding(s){again}" + (f" · {item['id']}" if item else ""),
            "detail": md.doc(md.section(f"Blocking findings ({len(found)})", listed),
                             md.section("What the buttons do", md.bullets([
                                 f"**{'Send back' if review.back else 'Fix them'}**: {fix_txt}",
                                 "**Go on anyway**: say why; the findings are kept as dismissed and shown at the final review."]),
                                 each, past.strip())),
            "options": _c.OPTIONS, "labels": {"approve": "Send back" if review.back else "Fix them", "reject": "Go on anyway"},
            "questions": asked},
            gate="findings" if done_rounds < (review.rounds or 2) else "rounds")
        why = (answer.get("why") or "").strip()
        decided = finding_decisions.read(asked, found, answer)
        fixes = [d for d in decided if d["decision"] == finding_decisions.FIX]
        amends = [d for d in decided if d["decision"] == finding_decisions.AMEND]
        dropped = [d for d in decided if d["decision"] in finding_decisions.DISMISSED]
        reasons = ""
        if dropped:
            data["dismissed_findings"] = list(data.get("dismissed_findings") or []) + [{
                "step": review.id, "name": review.name, "item": (item or {}).get("id"), "lens": d["lens"],
                "findings": [d["text"]], "decision": d["decision"], "why": d["reason"]} for d in dropped]
            reasons = "; ".join(dict.fromkeys(f"{d['decision']}: {d['reason']}" if d["reason"] else d["decision"] for d in dropped))
            plain = all(d["decision"] == finding_decisions.ACCEPT and d["reason"] == why for d in dropped)
            gates["log"].append(f"{review.name} findings accepted: {why}" if plain and not (fixes or amends)
                                else f"{review.name}: {len(dropped)} finding(s) accepted: {reasons}"[:600])
        shown = [{"text": d["text"], "decision": d["decision"], "reason": d["reason"]} for d in decided]
        if not fixes and not amends:
            if (review.agent or "") == "security-auditor":
                await asyncio.to_thread(security_actions.accepted, ctx.project_id or ctx.root, ctx.root, dropped)
            ctx.emit("gate.decided", step=fix.id, data={"gate": f"{review.name} findings", "decision": "reject", "why": why,
                                                        "findings": [f["text"] for f in found], "decisions": shown})
            return {**extra, "gates": gates, "findings": [], "data": data,
                    "note": f"{len(found)} finding(s) accepted: {why or reasons}"[:300]}, self.nav.after(i)
        ctx.emit("gate.decided", step=fix.id, data={"gate": f"{review.name} findings", "decision": "approve", "why": why,
                                                    "decisions": shown})
        if amends:
            # the spec is wrong here: the amendment loop (amend_start reads the AMEND marker), the review comes again later
            said = "; ".join(f"{d['text']}" + (f" ({d['reason']})" if d["reason"] else "") for d in amends)
            mk = {k: dict(v) for k, v in (state.get("markers") or {}).items() if isinstance(v, dict)}
            mk[review.id] = {**(mk.get(review.id) or {}), "AMEND": f"{review.name} finding: {said}"[:1800]}
            gates["log"].append(f"{review.name}: {len(amends)} finding(s) to a spec amendment: {said}"[:600])
            astate = {**state, **extra, "markers": mk, "data": data, "gates": gates}
            r = await _c.run_action("amend_start", self._action_input(astate, None))
            return {**extra, "gates": gates, "findings": [], "data": data, "markers": mk, **r.update,
                    "note": f"{review.name}: spec amendment for {len(amends)} finding(s)"
                            + (f", {len(fixes)} to fix after it (the review runs again)" if fixes else "")}, \
                self.nav.jump("amend_entry", i)
        if answer.get("auto"):
            gates["log"].append(f"{review.name} findings sent to fix: {why}")
        listed = "\n".join(f"- [{d['lens']}] {d['text']}" + (f"\n  the user: {d['reason']}" if d["reason"] and d["reason"] != why else "")
                           for d in fixes)
        feedback = f"Fix these blocking findings from {review.name}:\n{listed}" + (f"\n\nFrom the user: {why}" if why and not answer.get("auto") else "")
        if dropped:
            feedback += "\n\nLeave these as they are (the user decided): " + "; ".join(
                f"{d['text']} ({d['decision']}" + (f": {d['reason']}" if d["reason"] else "") + ")" for d in dropped)
        found = [{"lens": d["lens"], "text": d["text"]} for d in fixes]
        rounds[key] = done_rounds + 1
        if review.back:
            return {**extra, "gates": gates, "review_rounds": rounds, "findings": [], "feedback": feedback, "data": data,
                    "note": f"sent back to {review.back} (round {rounds[key]})"}, self.nav.jump(review.back, i)
        route = str((answer.get("payload") or {}).get("route") or "review-fix")
        if route not in _c.ROUTES:
            route = "review-fix"
        agent = "implementer" if route == "review-fix" else "test-author"
        fix = fix.model_copy(update={"phase": route, "agent": agent})
        state = {**state, **extra, "phase": route, "feedback": feedback, "data": data}
        before = await asyncio.to_thread(guard.snapshot, ctx.root)
        res, model, _tb = await self._run_agent(state, fix, agent, 0)
        refused = await asyncio.to_thread(guard.guard_diff, ctx.root, route, before, None, None, state.get("unlocks") or [],
                                          self._readonly())
        for r in refused:
            ctx.emit("guard.refused", step=fix.id, data={"tool": "diff-guard", "phase": route, **r})
        usage = dict(state.get("usage") or {})
        for k in ("tokens_in", "tokens_out", "tokens_cached", "premium_requests"):
            usage[k] = usage.get(k, 0) + getattr(res, k)
        usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + res.cost_usd, 6)
        step_tokens = dict(state.get("step_tokens") or {})
        step_tokens[review.id] = step_tokens.get(review.id, 0) + res.tokens_in + res.tokens_out + res.tokens_cached // 10
        upd = {**extra, "gates": gates, "usage": usage, "step_tokens": step_tokens, "review_rounds": rounds, "findings": [],
               "feedback": None, "last_answer": (res.text or "")[:2000], "phase": route, "data": data}
        a = self._action_input(state, None)
        a.title = f"address {review.name} findings"
        a.emit = self._tool_emit(fix.id)
        notes = []
        for action in _c.ROUTES[route]:
            r = await _c.run_action(action, a)
            notes.append(r.note)
            upd.update(r.update)
            if not r.ok:
                # Tests broke or the commit was refused: back to the question with what went wrong.
                found = found + [{"lens": "keel", "text": f"after the fix: {r.note}"}]
                return {**upd, "findings": found, "note": f"fix round {rounds[key]}: {r.note}"[:300]}, fix.id
        upd["note"] = " · ".join([f"{agent} ({route}) · {model['provider']} {model.get('model', '')}", *notes])[:300]
        return upd, self.nav.jump(review.redo or review.id, i)

    def _sections(self, step: Step, state: FlowState) -> list[str]:
        """knowledge-refresh: one librarian per section, for a parallel librarian step.

        Sections come from settings.sections, else from the thread's acs (the api starts the flow with
        one AC per stale section: {id: <section>, layer: "API", title: <section>}).
        """
        if step.kind != "parallel" or step.lanes or step.items_from or (step.agent or "") != "librarian":
            return []
        secs = [str(x) for x in self.ctx.settings.get("sections") or [] if str(x).strip()]
        if not secs and (self.wf.flow == "knowledge-refresh" or all(a["id"] in _c.KNOWN_SECTIONS for a in state.get("acs") or [{"id": "-"}])):
            secs = [a["id"] for a in state.get("acs") or []]
        if not secs and self.wf.flow == "knowledge-refresh":
            # Started without a list (the plain "start a flow" form): the stale sections (knowledge_scan: a cited file
            # changed) and the chosen ones not written yet; when none is, the sections init chose, else the ones that
            # exist, else all five. A librarian always gets its own section; without one it wanders.
            secs = [str(x) for x in ((state.get("data") or {}).get("knowledge_stale") or {}).get("sections") or []]
            if secs:
                return secs
            chosen = (rules.load_config(self.ctx.root).get("init") or {}).get("knowledge_sections")
            have = init_gates.existing_sections(self.ctx.root)
            secs = list(chosen) if isinstance(chosen, list) and chosen else (have or list(init_gates.SECTIONS))
        return secs

    def _acs_from(self, results, started: float | None = None, known: str | None = None) -> tuple[list[dict], str | None]:
        from pathlib import Path

        for res, _m, tb in results:
            if res.data.get("acs"):
                return [dict(a, status="todo") for a in res.data["acs"]], res.data.get("spec")
        root = Path(self.ctx.root)
        seen = None
        for res, _m, tb in results:
            # The spec file the agent wrote comes first; its chat answer only when no file has criteria.
            files = [w["path"] for w in tb.writes if w["path"].endswith(".md")]
            if git.is_repo(self.ctx.root):
                files += [rel for rel in git.dirty(self.ctx.root) if "spec" in rel and rel.endswith(".md")]
            # A CLI agent writes with its own tools (keel's ToolBox sees nothing), and a git-ignored .keel/specs/ is
            # never dirty: the spec its answer names, the one the flow already knows, and the spec folder's files
            # changed since the step started.
            files += self._specs_named(res.text) + ([known] if known else []) + self._specs_since(started)
            files = [f for f in dict.fromkeys(files) if (root / f).is_file()]
            spec = files[0] if files else None
            seen = seen or spec
            for rel in files:
                acs = prompts.parse_acs((root / rel).read_text(errors="replace"))
                if acs:
                    return acs, rel
            acs = prompts.parse_acs(res.text)
            if acs:
                return acs, spec
        # No criteria yet, but the spec file is known: a send-back changes that file instead of starting over.
        return [], seen

    def _spec_dirs(self) -> tuple[str, ...]:
        from .. import layout

        return layout.resolve(self.ctx.root).spec_dirs

    def _specs_named(self, text: str | None) -> list[str]:
        """Spec files an answer names ("I wrote the spec to `.keel/specs/x.md`"): .md paths inside the project, in a
        spec folder or with "spec" in the path."""
        root = Path(self.ctx.root).resolve()
        out = []
        for raw in _c.SPEC_PATH.findall(text or ""):
            p = (root / raw).resolve()          # an absolute path stays itself
            if not p.is_relative_to(root) or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if "spec" in rel or any(rel.startswith(d.rstrip("/") + "/") for d in self._spec_dirs()):
                out.append(rel)
        return out

    def _specs_since(self, started: float | None) -> list[str]:
        """The spec folders' .md files changed since the step started (git-ignored ones too), newest first."""
        if started is None:
            return []
        root = Path(self.ctx.root)
        found = []
        for d in self._spec_dirs():
            folder = root / d
            if not folder.is_dir():
                continue
            for p in folder.rglob("*.md"):
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if mtime >= started - 1:        # file systems keep mtimes to the second
                    found.append((mtime, p.relative_to(root).as_posix()))
        return [rel for _t, rel in sorted(found, reverse=True)]

    def _action_input(self, state: FlowState, ac: dict | None, item: dict | None = None) -> ActionInput:
        return ActionInput(root=self.ctx.root, project=self.ctx.project_id, phase=state["phase"], title=self.ctx.title, ac=ac, init=dict(state.get("init") or {}),
                           acs=copy.deepcopy(state.get("acs") or []), fake=self.ctx.simulate_checks, flow=self.wf.flow,
                           deps=list(state.get("deps") or []), gates_log=list((state.get("gates") or {}).get("log") or []),
                           base=state.get("base_head"), unlocks=list(state.get("unlocks") or []),
                           preexisting=dict(state.get("preexisting") or {}), item=item, data=dict(state.get("data") or {}),
                           keys=dict(self.ctx.keys), state=dict(state), settings=dict(self.ctx.settings or {}),
                           thread_id=self.ctx.thread_id, request=self.ctx.request)
