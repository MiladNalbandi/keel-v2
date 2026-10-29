"""The Compiler's code steps: a criterion's red/green loop, its checks and ladder, and the questions a stuck step asks."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import re
import time

from . import choices as choices_mod, flow_actions, prompts, run_mode, spec_check, suite_failure, test_scope
from .. import extensions, rules
from ..rules import checks as rule_checks
from ..workflows.model import Step
from .actions import ActionResult, revert_manifests
from .gate_state import deep_gates
from .state import FlowState, merge_unlocks, normalize_unlocks
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END

HISTORY_CHECKS = {"audit", "trace", "trace_strict"}   # checks that only read the branch's history

from . import compiler as _c      # the compiler module's own names (looked up when called: tests patch some)

_FIGURES = re.compile(r"0x[0-9a-f]+|\b[0-9a-f]{7,40}\b|\d+(?:[.,:]\d+)*")
TEST_WRONG = re.compile(r"^\s*(?:\*\*|`)?TEST-WRONG\b", re.I | re.M)


class CodeMixin:

    async def code_step(self, i: int, step: Step, state: FlowState):
        """0.18.15 a code step is a job like an agent's (code.started → code.step per action and command →
        code.finished; the api stores them as a job), so the Flow page, the Jobs list, keel TUI and KeelBot see what runs
        now, not "starting…". Its own event names: whoever counts agent.* still counts agents only."""
        job = _c._CodeJob(self.ctx, step, state)
        job.start()
        try:
            out = await self._code_step_run(i, step, state, job)
        except GraphBubbleUp:
            job.progress("waits for a person")    # an interrupt (a question the step raised) is not a failure; the
            job.finish("done")                    # step runs again on the answer, as a new job
            raise
        except BaseException:
            job.finish("failed")
            raise
        job.finish("done" if job.ok else "failed")
        return out

    async def _code_step_run(self, i: int, step: Step, state: FlowState, job: "_CodeJob"):
        ac = _c._ac(state) if step.per_ac else None
        item = self._item(state, step)
        st = dict(state)
        upd: dict = {}
        notes, details = [], []
        actions = step.actions()
        for n, action in enumerate(actions):
            if action == "start_flow":
                r = await self._start_flow(step, st, item)
            elif action == "escalate_model":
                r = self._escalate_model(step, st)
            else:
                a = self._action_input(st, ac, item)
                a.step = step.id
                a.emit = self._tool_emit(step.id)
                a.params = dict(step.params or {})
                a.event = self._addon_event(step.id)
                if extensions.owner(action):
                    a.state["gate_approved"] = self._gate_approved(i, st)     # a part's step (git:push asks the gate)
                if action == "open_pr":
                    a.state["pr_approved"] = self._gate_approved(i, st)
                    a.state["pr_auto"] = run_mode.is_auto_line(self._gate_line(i, st))
                a.progress = job.progress
                t0 = time.monotonic()
                job.progress(f"▶ {action}", tool=action)
                r = await _c.run_action(action, a)
                job.progress(r.note or action, tool=action, ok=bool(r.ok), ms=int((time.monotonic() - t0) * 1000),
                             output=(r.detail or "")[-_c.STEP_FIELD_MAX:] or None)
                if not r.ok and not step.soft:
                    job.ok = False
            if r.ask and r.ask.get("type") == "already-met" and ac:
                return await self._already_met(i, step, st, ac, r, upd, actions[n + 1:])
            if r.ask:
                return await self._answer_check(i, step, st, r, upd)
            if r.stop:
                # A refusal no retry can change (an empty diff to review): the flow ends here and says why.
                return {**upd, "status": "stopped", "error": r.note, "note": r.note}, END
            if not r.ok and step.soft:
                # A soft check records what it found and goes on; a branch reads markers[<id>].RESULT, a later step
                # reads what it said in data["<id>_output"]. 0.18.74 data.soft_failed: auto mode stops at the next gate
                st.update(r.update)
                upd.update(r.update)
                data = {**(st.get("data") or {}), f"{step.id}_output": f"{r.note}\n{r.detail}".strip()[:6000],
                        "soft_failed": {**((st.get("data") or {}).get("soft_failed") or {}), step.id: r.note[:200]}}
                st["data"] = upd["data"] = data
                notes.append(r.note)
                break
            if not r.ok:
                for k in ("blockers", "ladder", "suite_failure", "data"):
                    if k in r.update:
                        upd[k] = r.update[k]
                if r.update.get("unlocks"):
                    # 0.18.28 a check that opens files for the step it sends back to (a criterion's own test, after it
                    # broke tests outside it)
                    upd["unlocks"] = merge_unlocks(list(st.get("unlocks") or []),
                                                   normalize_unlocks(r.update["unlocks"], st.get("phase") or "none", "keel"))
                if action in _c.KNOWLEDGE_CHECKS:
                    return await self._knowledge_failed(i, step, st, r, upd)
                return await self._check_failed(i, step, st, ac or item, r, upd)
            st.update(r.update)
            upd.update(r.update)
            notes.append(r.note)
            if r.detail:
                details.append(r.detail)
        else:
            r = None
        if step.soft and r is None and step.id in ((state.get("data") or {}).get("soft_failed") or {}):
            # it passes now: no longer a reason for auto mode to stop
            d = dict(upd.get("data") or state.get("data") or {})
            d["soft_failed"] = {k: v for k, v in (d.get("soft_failed") or {}).items() if k != step.id}
            upd["data"] = d
        if step.soft:
            result = "fail" if r is not None else "pass"
            mk = dict(state.get("markers") or {})
            mk[step.id] = {"RESULT": result}
            mk["*"] = {**(mk.get("*") or {}), "RESULT": result}
            upd["markers"] = mk
        key = f"{step.id}:{(ac or item or {}).get('id')}"
        retries = dict(state.get("retries") or {})
        retries.pop(key, None)
        upd.update(note="; ".join(notes), retries=retries, stall={"fingerprint": None, "count": 0, "step": 0}, last_failure=None,
                   output="\n".join(details)[:2000] or None)   # what the actions printed (explain.py shows its head)
        if not ac and not item:
            # a step outside any loop passed: its "keeps failing" count starts again (a criterion's ends at its gate)
            upd.update(flow_actions.stuck_counted({**state, **upd}, step.id, None))
        upd.setdefault("show", None)        # what the next gate shows: only an action of this step sets it
        if step.then == "end":
            # The work goes on in the flow start_flow started (or there is nothing more to do); this one ends here.
            return {**upd, "status": "done", "ac": None, "item": None}, END
        if step.then and step.then != "continue":
            return upd, self.nav.jump(step.then, i)
        return upd, self.nav.after(i)

    def _escalate_model(self, step: Step, state: FlowState) -> ActionResult:
        """keel v1 "escalate the model once": the step's agent (default investigator) runs on a stronger model from here
        on (settings.stronger_model, else Opus for Claude, else high effort). Marker ESCALATED: yes."""
        agent = step.agent or "investigator"
        cur = self._model(state, step, agent)
        strong = self.ctx.settings.get("stronger_model") or _c.stronger(cur)
        mk = dict(state.get("markers") or {})
        mk[step.id] = {"ESCALATED": "yes"}
        mk["*"] = {**(mk.get("*") or {}), "ESCALATED": "yes"}
        upd = {"markers": mk, "agent_models": {**(state.get("agent_models") or {}), agent: strong}}
        same = strong == cur
        label = f"{strong.get('provider')} {strong.get('model', '')}{' ' + strong['effort'] if strong.get('effort') else ''}"
        return ActionResult(True, f"{agent}: no stronger model to escalate to; it tries again on {label}" if same
                            else f"{agent} escalated to {label}", update=upd)

    def _gate_approved(self, i: int, state: FlowState) -> bool:
        """Was the nearest gate before step i approved (its last line in the gate log)?"""
        gate = next((s for s in reversed(self.wf.steps[:i]) if s.kind == "gate"), None)
        last = self._gate_line(i, state)
        return bool(gate and last.startswith(f"gate {gate.id} approve"))

    def _gate_line(self, i: int, state: FlowState) -> str:
        """The last gate-log line of the nearest gate before step i ("" when it has none)."""
        gate = next((s for s in reversed(self.wf.steps[:i]) if s.kind == "gate"), None)
        if not gate:
            return ""
        mine = [line for line in (state.get("gates") or {}).get("log") or [] if line.startswith(f"gate {gate.id} ")]
        return mine[-1] if mine else ""

    def _seed_value(self, value, state: FlowState, item: dict | None):
        """A seed value: "$a.b.c" reads that path from the state (item = the loop's item), anything else is literal."""
        if isinstance(value, dict):
            return {k: self._seed_value(v, state, item) for k, v in value.items()}
        if isinstance(value, list):
            return [self._seed_value(v, state, item) for v in value]
        if not (isinstance(value, str) and value.startswith("$")):
            return value
        cur = {**state, "item": item, "request": self.ctx.request}
        for part in value[1:].split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        return cur

    async def _start_flow(self, step: Step, state: FlowState, item: dict | None) -> ActionResult:
        return await self.start_flow(step.flow, step.seed or {}, step, state, item)

    async def start_flow(self, flow: str, raw_seed: dict, step: Step, state: FlowState, item: dict | None) -> ActionResult:
        """Hand-off: start another workflow's thread on this project with a seed; both threads record the link."""
        ctx = self.ctx
        if not ctx.spawn:
            return ActionResult(False, "This engine cannot start another flow from here.")
        seed = {"title": (item or {}).get("title") or ctx.title, "request": ctx.request}
        seed.update({k: self._seed_value(v, state, item) for k, v in raw_seed.items()})
        link = {"thread_id": ctx.thread_id, "workflow": self.wf.id, "step": step.id}
        key = f"start_flow|{step.id}|{(item or {}).get('id', '')}"
        try:
            # A node that runs again (try again after a later failure in the same step) must not start a second child.
            child = ctx.done_calls.get(key) or await ctx.spawn(flow, seed, link)
        except Exception as exc:
            return ActionResult(False, f"Could not start the {flow} flow: {getattr(exc, 'error', None) or exc}")
        ctx.done_calls[key] = child
        children = list(state.get("children") or []) + [{"thread_id": child, "workflow": flow, "step": step.id,
                                                        "title": str(seed.get("title") or "")[:200]}]
        ctx.emit("flow.started", step=step.id, data={"child": child, "workflow": flow, "title": seed.get("title"),
                                                     "then": step.then or "continue"})
        return ActionResult(True, f"started the {flow} flow ({child[:8]}): {seed.get('title')}", update={"children": children})

    async def _already_met(self, i: int, step: Step, state: FlowState, ac: dict, r: ActionResult, upd: dict, rest: list[str]):
        """verify_red found the AC's own test already passing (keel v1 "already-met"). No retries: ask right away.

        approve: run the rest of this step (the red commit: `test(<AC>): ...`), set the AC to already-met and
        skip its green, review and gate steps. reject: back to the red step with the user's note as feedback.
        """
        q = r.ask
        answer, extra = self._ask(state, {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"],
                                          "options": _c.OPTIONS}, gate="already-met")
        upd = {**upd, **extra}
        state = {**state, **extra}
        decision = answer.get("decision", "reject")
        why = (answer.get("why") or "").strip()
        gates = deep_gates(state)
        gates["log"].append(f"ac {ac['id']} already-met {decision}" + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why, "ac": ac["id"]})
        key = f"{step.id}:{ac['id']}"
        retries = dict(state.get("retries") or {})
        retries.pop(key, None)
        if decision != "approve":
            feedback = (f"{ac['id']}: the test passed before any code was written, so it does not prove the criterion. "
                        f"Write a stricter test that fails until {ac['id']} is built.")
            if why:
                feedback += f"\n\nFrom the user: {why}"
            target = self.nav.retry_target(i) or step.id
            return {**upd, "gates": gates, "retries": retries, "feedback": feedback, "last_failure": r.note[:300],
                    "note": f"{ac['id']} already passes; sent back for a stricter test" + (f": {why}" if why else "")}, target
        st = {**state, "gates": gates}
        notes = [r.note]
        for action in rest:
            ai = self._action_input(st, ac)
            ai.marker = "already-met"       # 0.18.57 test(<AC>) with Keel-Path: already-met: the ship audit accepts it
            res = await _c.run_action(action, ai)
            if not res.ok or res.ask:
                # A refused commit is an ordinary failed check (it goes back to the red agent).
                return await self._check_failed(i, step, st, ac, res, {**upd, "gates": gates})
            st.update(res.update)
            upd.update(res.update)
            notes.append(res.note)
        acs = _c._set_ac(st.get("acs") or [], ac["id"], _c.ALREADY_MET)
        # 0.18.28 never skipped silently: who said "already passes" (a person, or the run mode) decides whether the
        # criterion-reviewer still reads the test; the tests outside it run either way, and the full-diff review lists it
        data = dict(upd.get("data") or st.get("data") or {})
        data["already_met"] = {**(data.get("already_met") or {}), ac["id"]: {"auto": bool(answer.get("auto")), "why": why}}
        upd.update(acs=acs, gates=gates, retries=retries, feedback=None, last_failure=None, data=data,
                   stall={"fingerprint": None, "count": 0, "step": 0},
                   note=" · ".join([f"{ac['id']} marked as already met" + (f": {why}" if why else ""), *notes[1:]]))
        after = self._after_green_step(i)
        if after is not None:
            return upd, self.wf.steps[after].id
        return upd, _c.AC_END if step.per_ac and self.nav.last is not None else self.nav.after(i)

    def _after_green_step(self, i: int) -> int | None:
        """0.18.28 the check of the tests outside a criterion (verify_after_green) in the same loop after step i."""
        lp = self.nav.loop_of(i)
        if not lp:
            return None
        return next((j for j in range(i + 1, lp.last + 1) if "verify_after_green" in self.wf.steps[j].actions()), None)

    @staticmethod
    def _met_by_person(state: FlowState, ac: dict | None) -> bool:
        """An already-met criterion a person approved at "already passes" (not the run mode)."""
        if not ac or ac.get("status") != _c.ALREADY_MET:
            return False
        seen = ((state.get("data") or {}).get("already_met") or {}).get(ac["id"])
        return bool(seen) and not seen.get("auto")

    def _guard_loop(self, step: Step, state: FlowState, ac: dict | None, target: str | None) -> dict | None:
        """The guard-loop question, or None: the agent step this check sends work back to had the same file(s) put back
        by keel's phase rule in two rounds in a row and the criterion, its plan or the request names them; or the agent
        said FROZEN: <file> for a file the phase freezes. Choices: allow the file(s) in this phase (recommended, the
        existing unlock), skip the criterion, stop."""
        if not target or target not in self.nav.index:
            return None
        tstep = self.wf.steps[self.nav.index[target]]
        if tstep.kind not in ("agent", "parallel"):
            return None
        phase = tstep.phase or state.get("phase") or "none"
        data = state.get("data") or {}
        skey = f"{tstep.id}:{(ac or {}).get('id')}"
        streak = (data.get("guard_streak") or {}).get(skey) or {}
        texts = [(ac or {}).get("title"), data.get("plan_text"), self.ctx.request,
                 prompts.plan_row(spec_check.read_spec(self.ctx.root, state.get("spec")), ac["id"]) if ac and state.get("spec") else ""]
        # 0.18.58 a file the criterion's own title names asks after one round: the agent may move it out of the
        # rule's way and pass the tests, so a second round may never come
        title = [(ac or {}).get("title")]
        files = [f for f in streak.get("paths") or []
                 if test_scope.named_in(f, texts if streak.get("count", 0) >= 2 else title)] if streak.get("count", 0) >= 1 else []
        said = test_scope.frozen_said(state.get("last_answer"))
        if said:
            cfg = rules.load_config(self.ctx.root)
            files += [f for f in test_scope.resolve(self.ctx.root, said) if test_scope.unlockable(phase, f, cfg)]
        files = [f for f in dict.fromkeys(files) if not rules.unlocked(state.get("unlocks"), f, phase)]
        if not files:
            return None
        cfg = rules.load_config(self.ctx.root)
        why = "; ".join(dict.fromkeys(test_scope.frozen_why(phase, rules.classify(cfg, f)) for f in files))
        shown = ", ".join(f"`{f}`" for f in files)
        lp = self.nav.loop_of(self.nav.index[tstep.id])
        can_skip = bool(ac and lp and lp.per_ac)
        rounds = int(streak.get("count") or 0)
        how = (f"keel's guard put {'it' if len(files) == 1 else 'them'} back after the last {rounds} rounds of "
               f"{tstep.name}" if rounds >= 2 else f"{tstep.agent or 'the agent'} says it cannot do {tstep.name} without "
               f"{'it' if len(files) == 1 else 'them'}")
        allow_label = f"Allow {shown} in this phase (recommended)" if len(files) == 1 else "Allow these files in this phase (recommended)"
        info = [{"name": "allow", "label": allow_label, "why": choices_mod.OPTIONAL,
                 "description": f"keel lets {shown} change in phase {phase}, for this flow only; the same round runs again"}]
        if can_skip:
            info.append({"name": "skip", "label": "Skip this criterion", "why": choices_mod.NONE,
                         "description": f"move {ac['id']} to the end of the list and go on with the next criterion"})
        info.append({"name": "stop", "label": "Stop the flow", "why": choices_mod.OPTIONAL,
                     "description": "stop the flow here (the commits so far stay)"})
        info = [{**c, "default": k == 0} for k, c in enumerate(info)]
        lines = [f"keel's rule for this phase blocks changes to {shown} ({why}); the change needs "
                 f"{'that file' if len(files) == 1 else 'those files'}. {how}, so another round, an investigator or a "
                 "stronger model cannot help: the rule decides, not the agent.", "",
                 f"Allow {'it' if len(files) == 1 else 'them'} if the change is meant to touch "
                 f"{'it' if len(files) == 1 else 'them'} (a change to test code only, a refactor of a test).", "",
                 "Choose one:", *[f"- {c['name']}: {c['description']}" for c in info]]
        q = {"type": "guard-loop", "kind": "fix", "step": step.id,
             "title": f"keel's rules block {shown} in {phase}"[:200], "detail": "\n".join(lines),
             "choices": [c["name"] for c in info], "choice_info": info, "retry_to": tstep.id, "streak_key": skey,
             "allow": [{"path": f, "phase": phase, "reason": f"the change needs it ({why})"} for f in files]}
        q["plain"] = choices_mod.plain_effects(q)
        return q

    def _stuck_question(self, step: Step, ac: dict, r: ActionResult, rounds: int, attempts: int) -> dict:
        """The question after the stall ladder: retry with a note / skip the criterion / edit the spec / stop."""
        edit = any(s.id == "amend" for s in self.wf.steps)
        choices = ["retry", "skip"] + (["edit"] if edit else []) + ["stop"]
        lines = [f"{step.name} failed {rounds} times for {ac['id']}: the same agent, an investigator's proposal and a "
                 "stronger model did not fix it.", "", r.note, "", r.detail[-1500:], "",
                 choices_mod.how_to(),
                 "- retry: one more round, with your note for the agent",
                 f"- skip: move {ac['id']} to the end of the list and go on with the next criterion"]
        if edit:
            lines.append(f"- edit: amend the spec for {ac['id']} first (your note says what is wrong)")
        lines.append("- stop: stop the flow here (the commits so far stay)")
        return choices_mod.for_question({"type": "stuck", "kind": "fix", "title": f"{ac['id']} is stuck at {step.name}",
                                         "detail": "\n".join(lines), "choices": choices, "retries_after": attempts},
                                        context="stuck")

    def _choice_answer(self, i: int, step: Step, state: FlowState, r: ActionResult, upd: dict):
        """v0.17.5 a check that asks with real choices (a command that cannot run, a stuck criterion), never decided by a
        run mode: recheck (this check again), retry (the agent again, with the note), skip (the criterion to the end of the
        list, on with the next), edit (an amendment of the spec, with the note), stop. Plain approve = the first choice."""
        q = r.ask
        names = list(q["choices"])
        detail = q["detail"]
        ac = _c._ac(state)
        lp = self.nav.loop_of(i)
        # 0.18.12 the same stuck criterion sent around again and again: past settings.sendback_limit only skip or stop
        who = (ac or {}).get("id") or step.id
        tries, limit = flow_actions.stuck_tries(state, who), flow_actions.sendback_limit(self.ctx.settings)
        if tries >= limit:
            # "allow" is not one more try of the same: it lifts the rule that made the tries fail, so it stays
            names = (["allow"] if "allow" in names else []) + \
                flow_actions.past_limit(names, bool(ac and lp and lp.per_ac and self._others_open(state, ac)))
            detail = flow_actions.limit_note(tries, limit, who) + "\n\n" + flow_actions.offered(detail, names)
        question = {"step": step.id, "kind": "fix", "title": q["title"], "detail": detail, "options": _c.OPTIONS,
                    "choices": names}
        # the check's own choice_info (its labels and reasons), only the choices still offered; else from the text
        info = choices_mod.keep(q.get("choice_info"), names)
        question = {**question, "choice_info": info} if len(info) == len(names) else \
            choices_mod.for_question(question, context=q.get("type"))
        answer, extra = self._ask(state, question, gate="failure")
        upd = {**upd, **extra}
        why = (answer.get("why") or "").strip()
        choice = str((answer.get("payload") or {}).get("choice") or (names[0] if answer.get("decision") == "approve" else "stop"))
        gates = deep_gates(state)
        gates["log"].append(f"{q['type']} {choice}" + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": choice, "why": why})
        key = f"{step.id}:{(ac or {}).get('id')}"
        retries = {**(state.get("retries") or {}), key: int(q.get("retries_after") or 0) if choice == "retry" else 0}
        base = {**upd, "gates": gates, "retries": retries, "stall": {"fingerprint": None, "count": 0, "step": 0}}
        if choice in ("recheck", "retry", "edit"):
            # the same criterion goes around again: one more toward the limit (skip and stop do not count)
            base.update(flow_actions.stuck_counted({**state, **upd}, who, tries + 1))
        if choice == "recheck":
            return {**base, "note": f"{step.name}: checked again"}, step.id
        if choice == "allow" and q.get("allow"):
            # the guard-loop gate: the person allows the files keel's phase rule kept putting back, for this flow, this
            # phase and these paths only (state.unlocks, the dashboard's "Allow this file in this phase"); the same round
            # runs again with the files open
            opened = normalize_unlocks(q["allow"], state.get("phase") or "none", "user")
            for u in opened:
                self.ctx.emit("gate.decided", step=step.id, data={
                    "gate": "unlock", "decision": "approve", "unlock": {"path": u["path"], "phase": u["phase"]},
                    "why": f"{u['path']} in {u['phase']}" + (f": {why}" if why else "")})
            files = ", ".join(u["path"] for u in opened)
            data = dict(base.get("data") or state.get("data") or {})
            streaks = dict(data.get("guard_streak") or {})
            streaks.pop(q.get("streak_key"), None)
            data["guard_streak"] = streaks
            feedback = (f"The person allowed {files} in phase {opened[0]['phase']} for this flow: keel no longer puts "
                        "changes to it back. Make the change the criterion needs." + (f"\n\nFrom the person: {why}" if why else ""))
            target = self.nav.jump(q["retry_to"], i) if q.get("retry_to") else self.nav.retry_target(i)
            return {**base, "data": data, "unlocks": merge_unlocks(list(state.get("unlocks") or []), opened),
                    "feedback": feedback, "last_failure": r.note[:300],
                    "note": f"allowed {files} in {opened[0]['phase']}; the round runs again"[:300]}, target or step.id
        if choice == "retry":
            feedback = f"{r.note}\n{r.detail[-2000:]}".strip() + (f"\n\nFrom the user: {why}" if why else "")
            target = self.nav.jump(q["retry_to"], i) if q.get("retry_to") else self.nav.retry_target(i)
            return {**base, "feedback": feedback, "last_failure": r.note[:300], "note": f"{r.note} · tried again"[:300]}, target or step.id
        if choice in ("skip", "edit") and ac:
            # the stuck criterion's uncommitted work goes back (keel's agents' files only, never the person's), so the next
            # criterion's red commit is not refused for it; its commits stay
            back = flow_actions.reset(self._action_input(state, ac))
            gates["log"].append(f"{ac['id']}: {back.note}"[:300])
        if choice == "skip" and ac and lp and lp.per_ac:
            # 0.18.69 its RED test commit is reverted and it starts from todo again when its turn comes
            undone = flow_actions.revert_red(self.ctx.root, ac["id"], state.get("base_head"))
            reverted = bool(undone and undone[0])
            if undone:
                gates["log"].append(undone[1][:300])
            moved = [x for x in state.get("acs") or [] if x["id"] != ac["id"]] + \
                [dict(x, status="todo") if reverted else x for x in state.get("acs") or [] if x["id"] == ac["id"]]
            return {**base, "acs": moved, "ac": None, "feedback": None, "last_failure": None,
                    "note": f"{ac['id']} moved to the end" + ("; its RED test reverted" if reverted else "")}, \
                self.nav.begin(lp)
        if choice == "edit" and ac and any(s.id == "amend" for s in self.wf.steps):
            data = {**(state.get("data") or {}), "amend": {"from": step.id, "ac": ac["id"], "route": "amend",
                                                          "reason": f"{ac['id']} · {r.note}" + (f"\nThe person says: {why}" if why else ""),
                                                          "at": datetime.date.today().isoformat()}}
            return {**base, "data": data, "note": f"{ac['id']}: the spec is amended first"}, self.nav.jump("amend", i)
        return {**base, "status": "stopped", "error": r.note + (f" ({why})" if why else ""),
                "note": f"stopped: {q['title']}"[:300]}, END

    @staticmethod
    def _others_open(state: FlowState, ac: dict) -> bool:
        """Is another criterion still to build (so skipping this one moves the flow on, not round to it again)?"""
        return any(x["id"] != ac["id"] and x.get("status") not in _c.DONE for x in state.get("acs") or [])

    async def _answer_check(self, i: int, step: Step, state: FlowState, r: ActionResult, upd: dict):
        """A commit check asked the user (new dependency, escalation). Apply the answer and run the step again.

        Returning to the same step keeps one interrupt per node run, so a resume can never hand one
        question's answer to another.
        """
        q = r.ask
        if q.get("type") == "audit":
            return self._audit_answer(step, state, r, upd)
        if q.get("choices"):
            return self._choice_answer(i, step, state, r, upd)
        kind = {"deps": "dependency", "escalate": "escalate", "secrets": "secrets", "readonly": "readonly",
                "plugin": "plugin"}.get(q["type"], "failure")
        question = {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"], "options": _c.OPTIONS}
        if q.get("labels"):
            question["labels"] = dict(q["labels"])
        answer, extra = self._ask(state, question, gate=kind)
        upd = {**upd, **extra}
        decision = answer.get("decision", "reject")
        why = (answer.get("why") or "").strip()
        gates = deep_gates(state)
        if q["type"] in ("secrets", "readonly"):
            # A secret in the staged diff (every mode stops for it), or a read-only run's commit: the user decides.
            self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
            gates["log"].append(f"{q['type']} {decision}" + (f": {why}" if why else ""))
            if decision != "approve":
                return {**upd, "gates": gates, "status": "stopped", "error": r.note, "note": f"stopped: {r.note}"[:300]}, END
            if q["type"] == "readonly":
                # Approve = try the commit again (after the run mode was changed; still read-only, it asks again).
                return {**upd, "gates": gates, "note": "the commit runs again"}, step.id
            feedback = f"{r.note}\n{r.detail[-2000:]}".strip() + (f"\n\nFrom the user: {why}" if why else "")
            target = self.nav.jump(step.back, i) if step.back else self.nav.retry_target(i)
            return {**upd, "gates": gates, "feedback": feedback, "last_failure": r.note[:300],
                    "note": f"{r.note} · sent back to remove it"[:300]}, target or step.id
        if q["type"] == "plugin":
            # a plugin step asked first (db:change, git:push, git:pr): approve runs the step again with the answer
            self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
            gates["log"].append(f"plugin {step.id} {decision}" + (f": {why}" if why else ""))
            if decision != "approve":
                return {**upd, "gates": gates, "status": "stopped", "error": r.note, "note": f"stopped: {q['title']}"[:300]}, END
            ok = {**(state.get("plugin_ok") or {}), step.id: q.get("fingerprint")}
            return {**upd, "gates": gates, "plugin_ok": ok, "note": f"approved: {q['title']}"[:300]}, step.id
        if q["type"] == "deps":
            if decision == "approve":
                deps = list(state.get("deps") or []) + [d for d in q["deps"] if d not in (state.get("deps") or [])]
                gates["log"].append("deps approve: " + ", ".join(q["deps"]) + (f": {why}" if why else ""))
                upd.update(deps=deps, gates=gates, note="new dependency approved: " + ", ".join(q["deps"]))
            else:
                await asyncio.to_thread(revert_manifests, self.ctx.root, q["files"])
                gates["log"].append("deps reject: " + ", ".join(q["deps"]) + (f": {why}" if why else ""))
                upd.update(gates=gates, note="new dependency rejected; manifest put back: " + ", ".join(q["files"]))
            self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
            return upd, step.id
        # escalate
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "why": why})
        if decision == "approve":
            gates["log"].append(f"escalation: {q['why']}" + (f" ({why})" if answer.get("auto") else ""))
            # keel v1 `keel escalate`: the inline criteria become the feature flow's, finished ones stay finished, and
            # every commit stays on the branch.
            r2 = await self.start_flow("feature", {"acs": "$acs", "escalated_from": self.wf.id, "why": q["why"]}, step, state, None) \
                if self.ctx.spawn else None
            if not r2 or not r2.ok:
                return {**upd, "gates": gates, "status": "stopped", "error": f"Escalated to a feature flow: {q['why']}.",
                        "note": "escalated: start a feature flow for this work"}, END
            return {**upd, **r2.update, "gates": gates, "status": "done", "ac": None,
                    "note": f"escalated: {r2.note}"[:300]}, END
        # 0.19.x the override names its triggers: it silences those, not a different one a later commit trips
        gates["log"].append(rule_checks.override_line(q.get("ids") or [], why or q["why"]))
        return {**upd, "gates": gates, "note": f"stays a small change: {why}"}, step.id

    def _audit_answer(self, step: Step, state: FlowState, r: ActionResult, upd: dict):
        """0.18.57 the ship audit found problems in the branch's history. Running it again unchanged finds them again
        (a real run asked "Approve fix" over and over), so every answer changes what it checks: remote = compare with
        the remote base (commits merged upstream are not this branch's), accept = these exact problems are accepted
        with the reason (data.audit_accepted, recorded with the verdict), stop = the person fixes the commits."""
        q = r.ask
        question = {"step": step.id, "kind": q["kind"], "title": q["title"], "detail": q["detail"],
                    "options": q.get("options") or _c.OPTIONS}
        for k in ("labels", "choices", "choice_info"):
            if q.get(k):
                question[k] = q[k]
        answer, extra = self._ask(state, question, gate="failure")
        upd = {**upd, **extra}
        decision = answer.get("decision", "reject")
        why = (answer.get("why") or "").strip()
        choice = (answer.get("payload") or {}).get("choice") or ("accept" if decision == "approve" else "stop")
        if q.get("choices") and decision == "approve" and not (answer.get("payload") or {}).get("choice"):
            choice = "remote"           # the recommended one is what a plain approve takes
        gates = deep_gates(state)
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": decision, "choice": choice, "why": why})
        data = {**(state.get("data") or {}), **(upd.get("data") or {})}
        if choice == "remote" and q.get("upstream"):
            data["audit_base"] = q["upstream"]
            gates["log"].append(f"audit: compare with {q['upstream']} instead of {q.get('base')}")
            return {**upd, "gates": gates, "data": data, "note": f"the audit compares with {q['upstream']}"}, step.id
        if choice == "accept":
            reason = why or "accepted at the audit gate"
            data["audit_accepted"] = {**(data.get("audit_accepted") or {}), **{p: reason for p in q.get("problems") or []}}
            gates["log"].append(f"audit accept {len(q.get('problems') or [])} problem(s): {reason}"[:300])
            return {**upd, "gates": gates, "data": data, "note": f"audit problems accepted: {reason}"[:300]}, step.id
        gates["log"].append("audit stop" + (f": {why}" if why else ""))
        return {**upd, "gates": gates, "status": "stopped", "error": r.note, "note": f"stopped: {r.note}"[:300]}, END

    def _red_step(self, lp) -> str | None:
        """The criterion loop's RED agent step (it writes the test), or None (a loop without one)."""
        for s in self.wf.steps:
            if s.kind == "agent" and s.phase == "red" and s.id in self.nav.index and self.nav.loop_of(self.nav.index[s.id]) == lp:
                return s.id
        return None

    async def _ladder(self, count: int, i: int, step: Step, state: FlowState, target: str, feedback: str) -> tuple[str, dict, str]:
        """v0.17.5 the stall ladder of a criterion's check, one rung per failed round:
        1. the same agent again, with the concrete failure (no extra call);
        2. an investigator (read only) reads the failure and the code and proposes a fix; the agent gets its proposal;
        3. the agent's model is escalated once (for this criterion), with the investigator's proposal;
        after that the person chooses (_check_failed). Returns (feedback, state update, what this round did)."""
        tstep = self.wf.steps[self.nav.index[target]] if target in self.nav.index else None
        agent = (tstep.agent if tstep and tstep.kind == "agent" else None) or ""
        data = dict(state.get("data") or {})
        ac_id = (_c._ac(state) or {}).get("id")
        if count == 2:
            inv = step.model_copy(update={"id": f"{step.id}_investigate", "kind": "agent", "name": f"{step.name}: why does it keep failing",
                                          "agent": "investigator", "phase": "review", "markers": None, "instructions": None})
            ask = (f"{feedback}\n\nThis check failed twice for {ac_id}. Read the failure and the code (read only). Say in a few "
                   "lines what really goes wrong and what the next try must change. If the cause is outside the agent's "
                   "reach (the test command, the build, a criterion that must be built first), say so. If the TEST is what "
                   "is wrong (it can never pass as the spec says: it sends input the spec says the code must refuse, "
                   "expects what the spec does not ask, or needs a fixture or test config the implementer may not "
                   "change), start your answer with the line TEST-WRONG: <what the test must change>.")
            try:
                res, _m, _tb = await self._run_agent({**state, "phase": "review", "feedback": ask}, inv, "investigator", 0)
            except (asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as exc:          # the ladder goes on without the proposal
                return feedback, {}, f"the investigator failed ({str(exc)[:120]}); tried again"
            usage = _c.add_usage(dict(state.get("usage") or {}), {k: getattr(res, k, 0) for k in
                                                               ("tokens_in", "tokens_out", "tokens_cached", "premium_requests", "cost_usd")})
            proposal = (res.text or "").strip()[-3000:]
            data["ladder_proposal"] = proposal
            # 0.18.69 the cause first, the failure after it (it came last, under four thousand characters of output)
            return (f"An investigator read this failure. What really goes wrong and what this try must change:\n{proposal}"
                    f"\n\nThe failure:\n{feedback}", {"usage": usage, "data": data}, "an investigator proposed a fix; tried again")
        if count == 1:
            return feedback, {}, "the same agent again, with the failure"
        proposal = data.get("ladder_proposal")
        extra = f"\n\nThe investigator's proposal:\n{proposal}" if proposal else ""
        if count == 3 and agent and not data.get("ladder_escalated"):
            r = self._escalate_model(tstep, state)
            before = (state.get("agent_models") or {}).get(agent)
            data["ladder_escalated"] = {"agent": agent, "ac": ac_id, "before": before}
            upd = {**r.update, "data": data}
            mk = dict(upd.get("markers") or {})
            mk.pop(tstep.id, None)
            upd["markers"] = mk
            return feedback + extra, upd, r.note
        return feedback + extra, {}, "tried again"

    async def _knowledge_failed(self, i: int, step: Step, state: FlowState, r: ActionResult, upd: dict):
        """The knowledge check found problems (a citation of a file that is not there, a section without any). The pages
        are the librarian's work, so the librarian gets the list and fixes them: one round per run of this step, at most
        the step's rounds (else settings.fix_attempts), then the check runs again. Running the same check again without a
        fix would change nothing. After the rounds the person chooses: go on with a warning, or stop. Neither fails the
        flow: its code, tests and reviews are done, only its notes are off. The warning stays in data.warnings, the gate
        log and the flow's last note."""
        key = f"{step.id}:knowledge"
        retries = dict(state.get("retries") or {})
        count = retries.get(key, 0) + 1
        rounds = step.rounds if step.rounds is not None else int(self.ctx.settings.get("fix_attempts") or 3)
        problems = (r.detail or "").strip()
        gates = deep_gates(upd, state)
        base = {**upd, "last_failure": r.note[:300]}
        if count <= rounds:
            fixer = step.model_copy(update={"id": f"{step.id}_fix", "kind": "agent", "name": f"{step.name}: fix the pages",
                                            "agent": "librarian", "action": None, "markers": None, "instructions": None})
            ask = (f"keel's knowledge check found these problems in the knowledge pages (round {count} of {rounds}). Fix "
                   "each one in the page it names and change nothing else. A citation needs the file's full path from "
                   "the repo root (as `git ls-files` shows it) and a line that exists; when you cannot find the source "
                   f"of a claim, remove the claim.\n\n{problems[-4000:]}")
            try:
                res, _m, _tb = await self._run_agent({**state, "feedback": ask}, fixer, "librarian", 0)
            except (asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as exc:          # the check runs again; the rounds still end
                did = f"the librarian failed ({str(exc)[:120]})"
            else:
                base["usage"] = _c.add_usage(dict(state.get("usage") or {}), {k: getattr(res, k, 0) for k in (
                    "tokens_in", "tokens_out", "tokens_cached", "premium_requests", "cost_usd")})
                tokens = dict(state.get("step_tokens") or {})
                tokens[step.id] = tokens.get(step.id, 0) + res.tokens_in + res.tokens_out + res.tokens_cached // 10
                base["step_tokens"] = tokens
                did = "the librarian fixed the pages it names"
            retries[key] = count
            gates["log"].append(f"knowledge {step.id} round {count} of {rounds}: {r.note} {did}"[:300])
            return {**base, "gates": gates, "retries": retries,
                    "note": f"{r.note} · round {count} of {rounds}: {did}; the check runs again"[:300]}, step.id
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "fix", "title": f"{step.name}: problems remain after {rounds} round(s)",
            "detail": f"{r.note}\n\n{problems[-1500:]}\n\nThe librarian had {rounds} round(s) to fix them. Go on with a "
                      "warning: the flow goes on, and the warning is kept in the flow and its last note. Stop: the flow ends "
                      "here as done, with the warning (the work before this step stays).",
            "options": _c.OPTIONS, "labels": {"approve": "Go on with a warning", "reject": "Stop"}}, gate="failure")
        why = (answer.get("why") or "").strip()
        go_on = answer.get("decision") == "approve"
        warning = f"{step.name}: {r.note.rstrip('.')} (not fixed after {rounds} round(s))"
        data = dict(state.get("data") or {})
        data["warnings"] = [w for w in data.get("warnings") or [] if w.get("step") != step.id] + \
            [{"step": step.id, "text": warning, "problems": problems.splitlines()[:50]}]
        gates["log"].append(f"knowledge {step.id} {'go on with a warning' if go_on else 'stop'}: {warning}"
                            + (f" ({why})" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": answer.get("decision"),
                                                          "why": why, "warning": warning})
        retries[key] = 0
        out = {**base, **extra, "gates": gates, "data": data, "retries": retries, "feedback": None, "last_failure": None}
        if not go_on:
            return {**out, "status": "done", "ac": None, "item": None, "note": f"stopped with a warning: {warning}"[:300]}, END
        out["note"] = f"warning: {warning}"[:300]
        if step.then == "end":
            return {**out, "status": "done", "ac": None, "item": None}, END
        if step.then and step.then != "continue":
            return out, self.nav.jump(step.then, i)
        return out, self.nav.after(i)

    async def _check_failed(self, i: int, step: Step, state: FlowState, ac: dict | None, r: ActionResult, upd: dict):
        found = upd.get("suite_failure")
        if found and not ac:
            # 0.18.28 the whole suite fails in a flow with criteria (integration, a review fix): with a likely cause
            # (a test that passes alone, a criterion's test that writes to the DB and never cleans up) the person
            # chooses now, since the agent here cannot change tests; else the agent tries first, then the choices
            key = f"{step.id}:None"
            count = int((state.get("retries") or {}).get(key, 0)) + 1
            attempts = step.rounds if step.rounds is not None else int(self.ctx.settings.get("fix_attempts") or 3)
            target = self.nav.jump(step.back, i) if step.back else self.nav.retry_target(i)
            if not (found.get("pollution") or found.get("likely_ac")) and target and count <= attempts:
                retries = {**(state.get("retries") or {}), key: count}
                return {**upd, "retries": retries, "last_failure": r.note[:300], "note": r.note,
                        "feedback": f"{r.note}\n{r.detail[-6000:]}".strip()}, target
            return await self._suite_answer(i, step, state, r, upd, found)
        key = f"{step.id}:{(ac or {}).get('id')}"
        retries = dict(state.get("retries") or {})
        count = retries.get(key, 0) + 1
        # 0.18.69 the failure without its numbers (times, counts, ids, line numbers): the same failure in two rounds has
        # the same fingerprint (raw, a duration alone made every round look new)
        fp = hashlib.sha1(_FIGURES.sub("#", r.note + r.detail[:600]).encode()).hexdigest()[:12]
        stall = dict(state.get("stall") or {"fingerprint": None, "count": 0, "step": 0})
        if stall.get("fingerprint") == fp:
            stall["count"] = stall.get("count", 0) + 1
        else:
            stall = {"fingerprint": fp, "count": 1, "step": 0}
        feedback = f"{r.note}\n{r.detail[-4000:]}".strip()
        if stall["count"] >= 2:
            feedback = (f"SAME FAILURE as the last try ({stall['count']} rounds in a row): what was tried did not change the "
                        "outcome. Do not repeat it: find a different cause, or say BLOCKED with why.\n\n" + feedback)
        limit = (rules.load_config(self.ctx.root).get("loops") or {}).get("stall_repeats", 3)
        if stall["count"] >= limit:
            stall["step"] = min(stall.get("step", 0) + 1, len(rules.LADDER))
            feedback += "\n\nStall ladder: " + rules.LADDER[stall["step"] - 1]
        # A code step's `back` names where its failures go; else the agent step before it. `rounds` caps the attempts.
        target = self.nav.jump(step.back, i) if step.back else self.nav.retry_target(i)
        attempts = step.rounds if step.rounds is not None else int(self.ctx.settings.get("fix_attempts") or 3)
        base = {**upd, "stall": stall, "last_failure": r.note[:300], "feedback": feedback, "note": r.note}
        lp = self.nav.loop_of(i)
        blocked = self._guard_loop(step, state, ac, target)
        if blocked:
            # the guard put the same file(s) back in two rounds (or the agent said it needs them): another round, an
            # investigator or a stronger model cannot change a rule, so the person decides now
            retries[key] = count
            return self._choice_answer(i, step, state, ActionResult(False, r.note, r.detail, ask=blocked),
                                       {**base, "retries": retries})
        ladder = bool(step.per_ac and lp and lp.per_ac and ac and target and not step.after_rounds)
        if target and count <= attempts and not r.final:
            retries[key] = count
            if ladder:
                fb, more, did = await self._ladder(count, i, step, {**state, **upd}, target, feedback)
                red = self._red_step(lp) if count == 2 else None
                said = str(((more.get("data") or {}).get("ladder_proposal")) or "")
                if red and TEST_WRONG.match(said):
                    # 0.18.69 the investigator says the test is what is wrong (a field report: a RED test that sent what
                    # the spec answers with 400; GREEN may not change tests, so three rounds failed the same way): the
                    # RED step writes the test again, with the reason; then GREEN goes on
                    gates = deep_gates(base, state)
                    # GREEN's uncommitted tries go back first: a RED commit may hold no production code
                    back = flow_actions.reset(self._action_input(state, ac))
                    gates["log"].append(f"{ac['id']}: {back.note}"[:300])
                    gates["log"].append(f"stall {ac['id']} round {count}: the investigator says the test is wrong; RED again"[:300])
                    fb = (f"The test you wrote for {ac['id']} cannot pass as the spec says. An investigator read it with the "
                          f"failure:\n{said}\n\nWrite the test again so it checks {ac['id']} as the spec says; it must "
                          f"fail now for the missing behaviour, not for a wrong input or expectation.\n\nThe failure:\n{feedback}")
                    return {**base, **more, "gates": gates, "feedback": fb, "retries": retries,
                            "note": f"{r.note} · round {count}: the test is wrong; RED writes it again"[:300]}, red
                gates = deep_gates(base, state)
                gates["log"].append(f"stall {ac['id']} round {count}: {did}"[:300])
                return {**base, **more, "gates": gates, "feedback": fb, "retries": retries,
                        "note": f"{r.note} · round {count}: {did}"[:300]}, target
            return {**base, "retries": retries}, target
        if step.after_rounds:
            # keel v1 "fails twice: reset and reproduce again": a fresh start instead of another try in the same place.
            return {**base, "retries": {**retries, key: 0}, "note": f"{r.note} · on to {step.after_rounds}"[:300]}, \
                self.nav.jump(step.after_rounds, i)
        if ladder:
            return self._choice_answer(i, step, state, ActionResult(False, r.note, r.detail, ask=self._stuck_question(
                step, ac, r, count - 1, attempts)), base)
        in_items = bool(lp and not lp.per_ac and ac)
        reject = "reject to mark this item failed and go on with the next one" if in_items else "reject to stop the flow"
        who = (ac or {}).get("id") or step.id
        tries, limit = flow_actions.stuck_tries(state, who), flow_actions.sendback_limit(self.ctx.settings)
        again = f"Approve to try again, {reject}."
        history = bool(set(step.actions()) & HISTORY_CHECKS)
        if history:
            # 0.18.57 a check of the branch's commits finds the same thing again unless a commit changes them first;
            # 0.18.58 so "try again" was a dead end (the same gate came back): approve goes on, with a warning
            again = (f"This check reads the branch's commits: trying again finds the same thing. Approve to go on "
                     f"without it (the flow ends with a warning), {reject}.")
        question = {"step": step.id, "kind": "fix", "title": f"{step.name} keeps failing",
                    "detail": f"{r.note}\n\n{r.detail[-1500:]}\n\n{again}", "options": _c.OPTIONS}
        if history:
            question["labels"] = {"approve": "Go on with a warning", "reject": "Skip this item" if in_items else "Stop"}
        if tries >= limit and not history:
            # 0.18.12 past settings.sendback_limit tries: no "try again", only the way out
            question.update(options=["reject"], labels={"reject": "Skip this item" if in_items else "Stop"},
                            detail=f"{flow_actions.limit_note(tries, limit, who)}\n\n{r.note}\n\n{r.detail[-1500:]}\n\n"
                                   f"Reject: {'the item is marked failed and the next one goes on' if in_items else 'the flow stops'}.")
        answer, extra = self._ask(state, question)
        base.update(extra)
        if (answer or {}).get("decision") != "approve":
            if in_items:
                items = _c._set_ac(_c.get_list(state, lp.key), ac["id"], "failed")
                return {**base, **_c.put_list(state, lp.key, items), "retries": {**retries, key: 0}, "feedback": None,
                        "note": f"{ac['id']} failed: {r.note}"[:300]}, self.nav.end(lp)
            return {**base, "status": "failed", "error": r.note}, END
        retries[key] = 0
        if history:
            data = dict(base.get("data") or state.get("data") or {})
            warning = f"{step.name}: {r.note.rstrip('.')} (the person went on without it)"
            data["warnings"] = [w for w in data.get("warnings") or [] if w.get("step") != step.id] + \
                [{"step": step.id, "text": warning}]
            return {**base, "data": data, "retries": retries, "note": warning[:300]}, self.nav.after(i)
        base.update(flow_actions.stuck_counted({**state, **base}, who, tries + 1))
        why = (answer or {}).get("why")
        if why:
            base["feedback"] = f"{feedback}\n\nFrom the user: {why}"
        return {**base, "retries": retries}, target or step.id

    def _suite_question(self, step: Step, state: FlowState, r: ActionResult, found: dict) -> dict:
        """0.18.28 the whole suite fails and the agent cannot fix it: the ways out, the recommended first."""
        ac_id = found.get("likely_ac")
        acs = {a["id"] for a in state.get("acs") or []}
        names: list[str] = []
        info: list[dict] = []
        why_ac = "; ".join(found.get("evidence") or [])[:500]
        if ac_id and ac_id in acs:
            names.append("send_back_ac")
            info.append({"name": "send_back_ac", "label": f"Send back to {ac_id}", "why": choices_mod.OPTIONAL,
                         "description": f"{ac_id}'s test is the likely cause ({why_ac or 'it writes to the database'}): {ac_id} "
                                        "opens again at green with this failure and your note, and its test files may change"})
        if extensions.hooks("flow_fix"):
            names.append("keelbot_fix")
            info.append({"name": "keelbot_fix", "label": "Let KeelBot fix it", "why": choices_mod.OPTIONAL,
                         "description": "KeelBot gets the failure and the suspects in an Edit chat (it may change tests), "
                                        "then keel checks the suite again"})
        names.append("retry")
        info.append({"name": "retry", "label": "Check again", "why": choices_mod.NONE,
                     "description": "run the suite again (after you fixed it yourself)"})
        names.append("stop")
        info.append({"name": "stop", "label": "Stop the flow", "why": choices_mod.OPTIONAL,
                     "description": "stop here; the commits so far stay"})
        info = [{**c, "default": k == 0} for k, c in enumerate(info)]
        lines = [f"{step.name}: the whole suite fails, and the agent here cannot change tests.", "", r.note, "",
                 suite_failure.describe(found) or "", "", r.detail[-3000:], "", "Choose one:",
                 *[f"- {c['name']}: {c['description']}" for c in info],
                 "", f"A plain send-back with a reason takes {names[0]} with your reason."]
        q = {"step": step.id, "kind": "fix", "type": "suite", "title": f"{step.name}: the suite fails"
             + (f" (likely {ac_id}'s test)" if "send_back_ac" in names else ""),
             "detail": "\n".join(x for x in lines if x is not None), "options": _c.OPTIONS, "choices": names,
             "choice_info": info, "reject_choice": names[0]}
        q["plain"] = choices_mod.plain_effects(q)
        return q

    async def _suite_answer(self, i: int, step: Step, state: FlowState, r: ActionResult, upd: dict, found: dict):
        """0.18.28 the answer to the suite question: send the likely criterion back to its green (its test may change),
        let KeelBot fix it in an Edit chat and check again, check again, or stop. A plain send-back takes the first way
        out with its reason (never a silent failure); approve takes the recommended one."""
        q = self._suite_question(step, state, r, found)
        tries, limit = flow_actions.stuck_tries(state, step.id), flow_actions.sendback_limit(self.ctx.settings)
        if tries >= limit:
            q["choices"] = [c for c in q["choices"] if c in ("retry", "stop")]
            q["choice_info"] = choices_mod.keep(q["choice_info"], q["choices"])
            q["reject_choice"] = "stop"
            q["detail"] = flow_actions.limit_note(tries, limit, step.name) + "\n\n" + q["detail"]
            q["plain"] = choices_mod.plain_effects(q)
        answer, extra = self._ask(state, q, gate="failure")
        upd = {**upd, **extra}
        why = (answer.get("why") or "").strip()
        payload = answer.get("payload") or {}
        choice = str(payload.get("choice") or (q["choices"][0] if answer.get("decision") == "approve" else q["reject_choice"]))
        if choice not in q["choices"]:
            choice = q["reject_choice"]
        gates = deep_gates(state)
        gates["log"].append(f"suite {step.id} {choice}" + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": q["title"], "decision": answer.get("decision"),
                                                          "choice": choice, "why": why})
        base = {**upd, "gates": gates, "retries": {**(state.get("retries") or {}), f"{step.id}:None": 0},
                "stall": {"fingerprint": None, "count": 0, "step": 0}, "suite_failure": None}
        if choice in ("send_back_ac", "keelbot_fix", "retry"):
            base.update(flow_actions.stuck_counted({**state, **base}, step.id, tries + 1))
        said = f"\n\nFrom the person: {why}" if why else ""
        failure = f"{r.note}\n\n{suite_failure.describe(found)}\n\n{r.detail[-3000:]}".strip()
        if choice == "send_back_ac":
            ac_id = found["likely_ac"]
            lp = next((lp for lp in self.nav.loops if lp.per_ac), None)
            files = [s["file"] for s in found.get("suspects") or [] if ac_id in s.get("acs", [])]
            opened = normalize_unlocks([{"path": f, "phase": "green", "reason": f"{ac_id}'s test broke the suite"} for f in files],
                                       "green", "keel")
            data = {**(state.get("data") or {}), "reopen": {"ac": ac_id, "to": "green", "from": step.id}}
            note = (f"{ac_id}'s test is the likely cause of the suite failure at {step.name}. Fix it so the whole suite "
                    f"passes, not only {ac_id}'s own test: a test that writes to the database must clean up after itself "
                    "(a rollback, or delete what it wrote in tearDown), also on a second connection. Its test files are "
                    f"open to change now.\n\n{failure}{said}")
            return {**base, "acs": _c._set_ac(state.get("acs") or [], ac_id, "todo"), "data": data, "feedback": note,
                    "last_failure": r.note[:300], "unlocks": merge_unlocks(list(state.get("unlocks") or []), opened),
                    "note": f"{step.name}: sent back to {ac_id} (its test is the likely cause)"}, \
                self.nav.begin(lp) if lp else step.id
        if choice == "keelbot_fix":
            text = (f"keel's flow \"{self.ctx.title}\" stopped at {step.name}: the whole suite fails. Fix the cause (a test "
                    "may change: a missing cleanup or rollback is the usual one), run the suite to check, and say what you "
                    f"changed. Do not commit; keel checks the suite again and commits.\n\n{failure}{said}")
            fixed = await self._keelbot_fix(step, state, text)
            return {**base, "note": f"{step.name}: KeelBot {fixed}; checked again", "feedback": None}, step.id
        if choice == "retry":
            return {**base, "note": f"{step.name}: checked again", "feedback": None}, step.id
        return {**base, "status": "stopped", "error": r.note + (f" ({why})" if why else ""),
                "note": f"stopped: {q['title']}"[:300]}, END

    async def _keelbot_fix(self, step: Step, state: FlowState, text: str) -> str:
        """0.18.28 a KeelBot Edit turn in the flow's folder (the KeelBot part's flow_fix hook), waited for; what it did."""
        model = self._model(state, step, "implementer")
        for who, fn in extensions.hooks("flow_fix"):
            try:
                got = await fn(project=self.ctx.project_id or "", root=self.ctx.root, model=model, keys=dict(self.ctx.keys),
                               text=text, thread=self.ctx.thread_id, title=f"Fix: {step.name}")
            except Exception as exc:  # noqa: BLE001 - the suite is checked again either way
                _c.log.warning("flow_fix of %s failed: %s", who, exc)
                return f"could not run ({str(exc)[:160]})"
            self.ctx.emit("keelbot.fix", step=step.id, data={k: got.get(k) for k in ("session", "status") if got})
            return f"answered in chat {str((got or {}).get('session') or '')[:8]} ({(got or {}).get('status') or 'done'})"
        return "is not available here"
