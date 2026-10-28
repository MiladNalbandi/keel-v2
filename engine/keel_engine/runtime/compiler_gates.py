"""The Compiler's gate steps: a human gate, its named choices and menus, the clarify and init question gates."""
from __future__ import annotations

import asyncio
import json

from . import choices as choices_mod, clarify, flow_actions, flow_doc, init_gates, knowledge, markers, md, notice, prompts, ship as ship_mod, spec_check
from .. import rules
from ..workflows.model import Loop, Step
from .gate_state import deep_gates
from .state import FlowState
from langgraph.graph import END

from . import compiler as _c      # the compiler module's own names (looked up when called: tests patch some)


class GateMixin:

    async def gate_step(self, i: int, step: Step, state: FlowState):
        ctx = self.ctx
        ac = _c._ac(state) if step.per_ac else None
        item = self._item(state, step)
        acs = state.get("acs") or []
        gates = deep_gates(state)
        if ac and self._met_by_person(state, ac):
            gates["log"].append(f"ac {ac['id']} approve: already met, approved at \"already passes\"")
            return {"gates": gates, "note": f"{ac['id']}: already met (approved at \"already passes\")"}, self.nav.after(i)
        if ac:
            due = rules.gate_due(gates.get("mode", "every-ac"), acs, ac["id"], gates.get("skipped"), rules.ac_lane(ac))
            if not due["due"]:
                gates["log"].append(f"ac {ac['id']} approve: no gate here ({due['why']})")
                return {"gates": gates, "acs": _c._set_ac(acs, ac["id"], _c.ALREADY_MET if ac.get("status") == _c.ALREADY_MET else "done"), "note": f"no gate: {due['why']}",
                        **flow_actions.stuck_counted(state, ac["id"], None)}, self.nav.after(i)
        if step.when:
            label, values = self._when(state, step.when)
            if not self._holds(step.when, values):
                # Not this time (a --semi gate in an --auto run, a question with nothing to ask): approved unasked.
                why = f"not asked, {label}: {self._shown_values(values)}"
                gates["log"].append(f"gate {step.id} approve: {why}")
                ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": why, "by": "engine"})
                return {"gates": gates, "note": f"{step.name}: {why}"[:300]}, self.nav.after(i)
        if self.wf.flow == "init" and step.id == "questions":
            return self._init_questions(step, state, gates), self.nav.after(i)
        if step.skip_menu:
            return self._skip_menu(i, step, state, gates)
        if isinstance(step.choices, list) and item:
            return self._choice_gate(i, step, state, gates, item)
        waived = (gates.get("skipped") or {}).get(step.phase or "") if not ac and not item else None
        if waived:
            # keel v1 --no-gates: the gate records an automatic approval (it shows in the PR body's skipped gates).
            gates["log"].append(f"gate {step.id} approve: waived ({waived})")
            self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": f"waived: {waived}"})
            upd = {"gates": gates, "note": f"{step.name}: waived ({waived})"}
            if isinstance(step.choices, dict):
                first = self._open_choices(step, state)[0]     # not one a step before ruled out (init: apply_fix)
                upd["data"] = {**(state.get("data") or {}), f"{step.id}_answer": {"choice": first, "why": "waived", "payload": {}}}
                return upd, self.nav.jump(step.choices[first], i)
            if step.choices:
                upd["markers"] = self._choice_markers(state, step, _c._default_choice(state, step), "waived")
            return upd, self.nav.after(i)
        options = _c.OPTIONS
        title = step.name + (f" · {ac['id']}" if ac else f" · {item['id']}" if item else "")
        # only a flow with a step that writes the spec waits for criteria (a spike's gates have none, on purpose)
        no_criteria = not ac and step.phase in ("spec", "triage") and not acs and self._writes_specs()
        clar = state.get("clarify") or {}
        # the spec's questions, or the questions of an agent step with `asks: true` (clar["step"])
        asked = clar.get("questions") if (no_criteria or clar.get("step")) else None
        if asked:
            return self._clarify_gate(i, step, state, gates, asked)
        if no_criteria:
            # Nothing real to approve: say so and only allow "send back" with what the agent should do.
            options = ["reject"]
            title = f"{step.name} — no acceptance criteria yet"
            said = (state.get("last_answer") or state.get("note") or "").strip()
            detail = ("The spec step wrote no acceptance criteria, so there is nothing to approve yet. "
                      "Send it back and say what to build.\n\nWhat the agent said:\n" + said[:1200])
        elif step.report == "verdicts":
            detail = await asyncio.to_thread(ship_mod.final_report, ctx.root, ctx.project_id or ctx.root, dict(state), ctx.title,
                                             ctx.thread_id)
        elif self.wf.flow == "init" and step.id == "plan_gate":
            detail = init_gates.plan(ctx.root, state.get("init") or init_gates.defaults(ctx.root, bool(ctx.settings.get("fast"))))
        elif (state.get("data") or {}).get(f"{step.id}_doc"):
            # 0.18.64 a gate that shows a flow's document builds its text from the file as it is now (the clarify
            # result: clarify_flow.gate_detail); the question names it, so a read while it waits builds it again
            detail = await asyncio.to_thread(flow_doc.gate_text, ctx.root, state["data"], step.id)
        elif (state.get("data") or {}).get(f"{step.id}_detail"):
            # A step before this gate wrote what it should show (the hunt's lens list, its report summary).
            detail = str(state["data"][f"{step.id}_detail"])
        elif step.when and step.when.get("data") and not {"equals", "in"} & set(step.when) and isinstance(self._seed_value(f"$data.{step.when['data']}", state, None), str):
            # A gate that pauses on a data value shows it (cover: why coverage could not be measured).
            detail = self._seed_value(f"$data.{step.when['data']}", state, None)
        elif ac:
            # keel v1's AC gate: the criterion, its RED and GREEN commits, the criterion-reviewer's answer, the board
            prev = self.wf.steps[i - 1] if i else None
            reviewed = prev is not None and prev.per_ac and (prev.agent or "") in ("criterion-reviewer", "lens-reviewer", "branch-reviewer")
            detail = await asyncio.to_thread(flow_actions.ac_evidence, ctx.root, ac, acs,
                                             state.get("last_answer") if reviewed else None, state.get("note", ""),
                                             state.get("base_head"))
            ag = ((state.get("data") or {}).get("after_green") or {}).get(ac["id"])
            if ag:
                # 0.18.28 what ran outside the criterion's own filter after its green
                ran = {"suite": "the whole suite", "changed": "the branch's test files", "none": "nothing"}.get(ag.get("ran"), ag.get("ran"))
                detail += "\n\n" + md.section("Outside this criterion", f"{ran} ({ag.get('why')}): "
                                                + ("passed" if ag.get("ok") else "failed: " + ", ".join(ag.get("failing") or [])))
        elif item:
            about = item.get("title") or item.get("summary") or json.dumps({k: v for k, v in item.items() if k not in ("id", "status")},
                                                                          ensure_ascii=False, default=str)[:600]
            detail = (f"{item['id']}: {about}\nLast step: {state.get('note', '')}\n\n"
                      'Approve to go on, or choose skip (payload {"choice": "skip"}) and say why to skip this item.')
        elif i and self._code_before(i) and state.get("show"):
            # What the code step before this gate prepared for it (a review report, a diagnosis, a size proposal);
            # branches in between change nothing (v0.17.4: the amendment's branches before amend_gate and amend_loop).
            detail = state["show"]
        elif i and "pr" in self.wf.steps[i - 1].actions() and state.get("pr_body"):
            # The PR body the pr step built: approve to go on (an open_pr step after this gate may open it).
            detail = state["pr_body"]
        else:
            detail = "\n".join(f"{a['id']} [{a.get('layer', 'API')}] {a.get('title', '')}" for a in acs) or (state.get("note") or "")
            if state.get("spec"):
                detail = f"Spec: {state['spec']}\n{detail}"
                if step.phase in ("spec", "triage"):
                    detail += "\n\n" + spec_check.describe(spec_check.check(spec_check.read_spec(ctx.root, state["spec"]), acs,
                                                                              spec_check.outside_facts(ctx.request, state.get("data")),
                                                                              root=ctx.root))
        lint_notes = (state.get("data") or {}).get("lint_notes") or []
        if lint_notes and not no_criteria:
            detail = f"{detail}\n\nStatic checks on the last changed files:\n" + "\n".join(f"- {n}" for n in lint_notes)
        if step.menus and not no_criteria:
            detail = f"{detail}\n\n{self._folded_menus(i)}"
        question = {"step": step.id, "kind": "gate", "title": title, "detail": detail, "options": options}
        if (state.get("data") or {}).get(f"{step.id}_doc") and not no_criteria:
            question["doc"] = str(state["data"][f"{step.id}_doc"])
        # 0.18.55 the notification's short lines: what a step before wrote for it (the init hand-over), or from the
        # criterion, the item or the spec's criteria; none: the api makes them from the detail
        wrote = (state.get("data") or {}).get(f"{step.id}_summary")
        short = (wrote if isinstance(wrote, list) else None) or notice.gate(
            ac=ac, acs=acs, item=item, no_criteria=no_criteria, spec_phase=step.phase in ("spec", "triage"))
        if short:
            question["summary"] = list(short)
        if ac:
            question["ac"] = ac["id"]
        routes = isinstance(step.choices, dict) and not no_criteria
        context = f"{self.wf.flow}:{step.id}"
        if routes:
            question.update(options=["approve"], choices=self._open_choices(step, state))
            question = choices_mod.for_question(question, context)
        elif step.choices and not no_criteria:
            # A list outside a loop: named exits as markers CHOICE and WHY (branches route on them); reject sends back.
            question.update(choices=list(step.choices), detail=detail + "\n\nApprove with one of: " + ", ".join(step.choices)
                            + f" (default {_c._default_choice(state, step)}). Send back to {step.back or 'the step before'} with a note.")
            question = choices_mod.for_question(question, context, default=_c._default_choice(state, step))
        elif item:
            # 0.19.0 a for_each item's gate: skip is a named choice that needs a reason (payload skip: true still works)
            question.update(choices=["approve", "skip"])
            question = choices_mod.for_question(question, "item")
        extra_q = self._gate_questions(i, step, state) if not no_criteria else []
        if extra_q:
            # questions a form can show beside the choices (the hunt's lenses and triage, the folded skip menus)
            question["questions"] = extra_q
        kind, review = self._gate_kind(i, step, state, question, ac)
        answer, extra = self._ask(state, question, gate=kind, review=review)
        decision = answer.get("decision", "reject") if not no_criteria else "reject"
        why = (answer.get("why") or "").strip()
        payload = answer.get("payload") or {}
        if routes:
            names = self._open_choices(step, state)
            plain = "reject" if decision == "reject" and "reject" in names else names[0]
            back = str(payload.get("choice") or plain) == "reject"
        else:
            back = decision != "approve"
        stop, counted = self._sendbacks(step, {**state, **extra}, f"{step.id}:{(ac or item or {}).get('id', '')}", back)
        extra = {**extra, **counted}
        if ac and not back and decision == "approve":
            # the criterion is through its gate: its stuck count starts again (flow_actions.STUCK)
            extra.update(flow_actions.stuck_counted({**state, **extra}, ac["id"], None))
        if stop:
            gates["log"].append(f"gate {step.id} stop: {stop['note']}")
            return {"gates": gates, **extra, **stop}, END
        if routes:
            return self._choice(i, step, state, gates, extra, answer, item)
        choice = None
        if step.choices and decision == "approve":
            choice = payload.get("choice") if payload.get("choice") in step.choices else _c._default_choice(state, step)
        subject = f"ac {ac['id']}" if ac else f"gate {step.id}"
        gates["log"].append(f"{subject} {decision}" + (f" ({item['id']})" if item else "") + (f" [{choice}]" if choice else "")
                            + (f": {why}" if why else ""))
        decided = {"gate": step.name, "decision": decision, "why": why, "ac": (ac or {}).get("id")}
        if item:
            decided["item"] = item["id"]
        if choice:
            decided["choice"] = choice
        ctx.emit("gate.decided", step=step.id, data=decided)
        upd: dict = {"gates": gates, **extra}
        if payload.get("acs"):
            acs = [{"id": a["id"], "layer": a.get("layer", "API"), "title": a.get("title", ""), "status": a.get("status", "todo")}
                   for a in payload["acs"]]
            upd["acs"] = acs
        if decision == "approve":
            if ac:
                upd["acs"] = _c._set_ac(acs, ac["id"], _c.ALREADY_MET if ac.get("status") == _c.ALREADY_MET else "done")
            upd["note"] = f"{step.name} approved" + (f" [{choice}]" if choice else "") + (f": {why}" if why else "")
            if choice:
                upd["markers"] = self._choice_markers(state, step, choice, why)
            if item and (payload.get("skip") or payload.get("choice") == "skip"):
                lp = self.nav.loop_of(i)
                why = why or str(payload.get("reason") or "").strip()
                upd.update(self._end_item(state, lp, item, "skipped", None, why, (step.on_skip or {}).get("record")))
                upd["note"] = f"{item['id']} skipped" + (f": {why}" if why else "")
                return upd, self.nav.end(lp)
            return upd, self.nav.after(i)
        if ac:
            upd["acs"] = _c._set_ac(acs, ac["id"], "todo")
        upd["feedback"] = why or f"Sent back at {step.name}. {_c.NO_REASON}"
        upd["note"] = f"{step.name} sent back" + (f": {why}" if why else "")
        target = self.nav.jump(step.back, i) if step.back else self.nav.enter(max(i - 1, 0))
        return upd, target

    def _sendbacks(self, step: Step, state: FlowState, key: str, back: bool) -> tuple[dict | None, dict]:
        """Counts the send-backs of one gate in a row (key: step and criterion or item; an approval starts again) and
        returns (an update that stops the flow, or None; the state update). With a reason or without, after
        settings.sendback_limit send-backs (default SENDBACK_LIMIT) the next one asks the person first: go on anyway
        (the count starts again) or stop. Never a run mode's answer: it is a loop keel cannot see the end of."""
        counts = dict(state.get("sendbacks") or {})
        if not back:
            return None, ({"sendbacks": {k: v for k, v in counts.items() if k != key}} if key in counts else {})
        limit = flow_actions.sendback_limit(self.ctx.settings)
        n = int(counts.get(key, 0))
        extra: dict = {}
        if n >= limit:
            answer, extra = self._ask(state, {
                "step": step.id, "kind": "fix", "title": f"{step.name}: sent back {n} time(s) in a row",
                "detail": f"{step.name} was sent back {n} time(s) in a row (the limit is {limit}, settings.sendback_limit). "
                          "Another round may change nothing. Go on anyway: this send-back goes ahead and the count starts "
                          "again. Stop: the flow stops here (the work so far stays; rewind to go on later).",
                "options": _c.OPTIONS, "labels": {"approve": "Go on anyway", "reject": "Stop"},
                "summary": [f"{notice.FAIL} Sent back {notice.plural(n, 'time')} in a row", f"{notice.ASK} Go on anyway, or stop here"]},
                gate="rounds")
            self.ctx.emit("gate.decided", step=step.id, data={"gate": f"{step.name} send-back limit",
                                                              "decision": answer.get("decision"), "why": answer.get("why") or ""})
            if answer.get("decision") != "approve":
                return {**extra, "status": "stopped", "sendbacks": {**counts, key: 0},
                        "note": f"stopped: {step.name} was sent back {n} time(s) in a row"}, {}
            n = 0
        return None, {**extra, "sendbacks": {**counts, key: n + 1}}

    @staticmethod
    def _choice_markers(state: FlowState, step: Step, choice: str, why: str) -> dict:
        """A gate's named exit as markers (CHOICE, WHY) that branches read: `when: {marker: CHOICE, step: <gate>}`."""
        mk = dict(state.get("markers") or {})
        mk[step.id] = {"CHOICE": choice, "WHY": why}
        mk["*"] = {**(mk.get("*") or {}), "CHOICE": choice, "WHY": why}
        return mk

    def _when(self, state: FlowState, when: dict) -> tuple[str, list]:
        """A `when`'s subject and the values it looks at: a path in state.data (`data: hunt.mode`), or a marker an earlier
        agent ended with (when.step names that step or a list of steps, else the latest value from any step; `any: true`
        adds each fan-out item's marker). Empty values count as not given."""
        if when.get("data") or when.get("state"):
            # `data: hunt.mode` reads state.data; `state: acs` reads any path of the flow state.
            path = str(when["data"]).removeprefix("data.") if when.get("data") else str(when["state"])
            value = self._seed_value(f"$data.{path}" if when.get("data") else f"${path}", state, None)
            return path, [None if value in (None, "", [], {}) else value]
        name = str(when["marker"]).upper()
        return name, self._marker_values(state, when, name)

    @staticmethod
    def _holds(when: dict, values: list) -> bool:
        if when.get("absent"):
            # 0.18.21 `absent: true`: the value is not given (the feature's interview runs only without criteria)
            return not any(v not in (None, "", [], {}) for v in values)
        return any(markers.matches(v, when) for v in values)

    @staticmethod
    def _shown_values(values: list) -> str:
        shown = [f"{len(v)} item(s)" if isinstance(v, (list, dict)) else str(v) for v in values if v not in (None, "")]
        return ", ".join(shown) or "not given"

    def _code_before(self, i: int) -> bool:
        """Is the step before this one (past any branches) a code step?"""
        j = i - 1
        while j >= 0 and self.wf.steps[j].kind == "branch":
            j -= 1
        return j >= 0 and self.wf.steps[j].kind == "code"

    def _choice(self, i: int, step: Step, state: FlowState, gates: dict, extra: dict, answer: dict, item: dict | None):
        """A gate with named exits: payload.choice picks one (default the first); its answer is kept in
        state.data["<gate id>_answer"] for the step it leads to ({choice, why, payload})."""
        payload = answer.get("payload") or {}
        why = (answer.get("why") or "").strip()
        names = self._open_choices(step, state)
        # A plain "send back" picks the exit named reject when the gate has one; otherwise the first exit.
        plain = "reject" if answer.get("decision") == "reject" and "reject" in names else names[0]
        choice = str(payload.get("choice") or plain)
        if choice not in names:
            gates["log"].append(f"gate {step.id} unknown choice {choice}")
            return {"gates": gates, **extra, "note": f"{step.name}: '{choice}' is not one of {', '.join(names)}"}, step.id
        gates["log"].append(f"gate {step.id} {choice}" + (f" ({item['id']})" if item else "") + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "choice": choice, "why": why})
        # auto: the run mode answered, no person (the hunt's lens gate records "confirmed by the model")
        data = {**(state.get("data") or {}), f"{step.id}_answer": {"choice": choice, "why": why, "payload": payload,
                                                                  **({"auto": True} if answer.get("auto") else {})}}
        said = None if answer.get("auto") else why
        upd = {"gates": gates, **extra, "data": data, "feedback": said or (_c.NO_REASON if choice == "reject" else None),
               "note": f"{step.name}: {choice}" + (f": {why}" if why else "")}
        return upd, self.nav.jump(step.choices[choice], i)

    @staticmethod
    def _open_choices(step: Step, state: FlowState) -> list[str]:
        """A dict-choice gate's exits, minus the ones a step before it ruled out this time (data["<gate id>_without"],
        v0.17.5: amend_loop has no `first` when nothing else is unfinished)."""
        data = state.get("data") or {}
        without = set(data.get(f"{step.id}_without") or [])
        names = [c for c in step.choices or {} if c not in without] or list(step.choices or {})
        # data["<gate id>_order"]: the exits a step before put first, in its order (the clarify flow's matching flow
        # first: the first exit is the default and the recommended one)
        order = [c for c in data.get(f"{step.id}_order") or [] if c in names]
        return order + [c for c in names if c not in order]

    def _gate_questions(self, i: int, step: Step, state: FlowState) -> list[dict]:
        """Questions a gate shows beside its choices: what a step before it wrote (data["<gate id>_questions"]: the hunt's
        lenses, its triage's finding) and, for a gate with menus: true, the skip menus it decides."""
        out = [dict(q) for q in (state.get("data") or {}).get(f"{step.id}_questions") or [] if isinstance(q, dict)]
        if step.menus:
            units = [u for j in self._menu_steps(i) for u in ship_mod.skip_units(self.wf.steps, j)]
            if units:
                out.append(ship_mod.skip_question(units))
            if any(ship_mod.has_loop(self.wf.steps, j) for j in self._menu_steps(i)):
                out.append(ship_mod.gate_mode_question())
        return out

    def _menu_steps(self, after: int) -> list[int]:
        return [j for j, s in enumerate(self.wf.steps) if j > after and s.skip_menu]

    def _folded_menus(self, i: int) -> str:
        """The skip menus after gate i, for its own text (a gate with menus: true)."""
        out = ["Optional steps (decided here with this approval, so the flow does not stop again to ask):"]
        for j in self._menu_steps(i):
            out += ["", f"— {self.wf.steps[j].name} —", ship_mod.skip_menu_detail(self.wf.steps, j)]
        return "\n".join(out)

    def _folded_answer(self, i: int, state: FlowState) -> tuple[str, dict] | None:
        """(gate id, payload) when an earlier gate with menus: true was approved and so answers the menu at step i."""
        for j in range(i - 1, -1, -1):
            g = self.wf.steps[j]
            if g.kind == "gate" and g.menus and i in self._menu_steps(j):
                ans = (state.get("data") or {}).get(f"{g.id}_answer") or {}
                first = next(iter(g.choices)) if isinstance(g.choices, dict) else "approve"
                if ans and ans.get("choice") == first:
                    return g.id, dict(ans.get("payload") or {})
        return None

    def _skip_menu(self, i: int, step: Step, state: FlowState, gates: dict):
        """keel v1 ship's opening question: which skippable steps after this gate run this time. Every skip needs a
        reason; skips land in data.ship_skipped (the PR body and the final review show them) and the gate log."""
        units = ship_mod.skip_units(self.wf.steps, i)
        detail = ship_mod.skip_menu_detail(self.wf.steps, i)
        if step.phase == "ship":
            # 0.18.57 before ship: a local base behind or diverged from its remote is said first (keel compares with
            # the remote one; the audit once read upstream commits as this branch's)
            drift = ship_mod.base_warning(self.ctx.root)
            if drift:
                detail = f"Note: {drift}.\n\n{detail}"
        title, extra_all = step.name, {}
        mode_payload: dict = {}
        folded = self._folded_answer(i, state)
        if folded:
            # v0.17.5 decided at an earlier gate (the spec gate): its skips for this menu, no second stop. A name no menu
            # knows, or a skip without a reason, still asks here.
            gid, payload = folded
            mine = {u["name"] for u in units}
            known = {u["name"] for j in self._menu_steps(-1) for u in ship_mod.skip_units(self.wf.steps, j)}
            payload = ship_mod.skip_payload(payload, known)
            # B10 the gate mode answered there holds even when this menu asks again about a skip
            mode_payload = payload
            raw = payload.get("skip") or {}
            raw = {str(x.get("step") or x.get("name") or ""): x.get("reason") for x in raw if isinstance(x, dict)} \
                if isinstance(raw, list) else raw
            unknown = [n for n in raw if n not in known] if isinstance(raw, dict) else ["skip"]
            skips, lenses, problem = ship_mod.parse_skips(
                {**payload, "skip": {k: v for k, v in raw.items() if k in mine} if isinstance(raw, dict) else raw}, units)
            if unknown:
                problem = f"{', '.join(unknown)} cannot be skipped (skippable: {', '.join(sorted(known)) or 'none'})"
            if not problem:
                data = dict(state.get("data") or {})
                names = {s["step"] for s in skips}
                data["ship_skipped"] = [x for x in data.get("ship_skipped") or [] if x.get("step") not in names] + skips
                if lenses:
                    data["lenses_chosen"] = lenses
                self._gate_mode(i, gates, payload)
                gates["log"].append(f"gate {step.id} approve: chosen at {gid}")
                for sk in skips:
                    gates["log"].append(f"gate {step.id} skip {sk['step']} ({sk['band']}): {sk['reason']}")
                self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve",
                                                                  "why": f"chosen at {gid}", "skipped": skips})
                note = ("skipped: " + ", ".join(f"{s['step']} ({s['reason']})" for s in skips)) if skips else "every step runs"
                return {"gates": gates, "data": data, "note": f"{note} (chosen at {gid})"[:300]}, self.nav.after(i)
            title = f"{step.name}: {problem}"
        loop = ship_mod.has_loop(self.wf.steps, i)
        names = [u["name"] for u in units]
        refused: list[str] = []
        while True:
            ask = {"step": step.id, "kind": "gate", "title": title, "detail": detail, "options": ["approve"],
                   "labels": {"approve": "Run these steps"}}
            if units:
                ask["questions"] = [ship_mod.skip_question(units)]
            if loop:
                # B10 the gate mode is asked again with the skip, the earlier answer marked and kept when unanswered
                ask["questions"] = ask.get("questions", []) + [ship_mod.gate_mode_question(ship_mod.gate_mode_of(mode_payload))]
            answer, extra = self._ask(state, ask, gate="skip-menu")
            extra_all.update(extra)
            state = {**state, **extra}
            got = answer.get("payload") or {}
            if ship_mod.gate_mode_of(got):
                mode_payload = got
            said = (answer.get("why") or "").strip()
            if said and not got.get("reason"):
                got = {**got, "reason": said}           # 0.18.57 the answer's why is the shared reason of its skips
            later = {u["name"] for j in self._menu_steps(-1) for u in ship_mod.skip_units(self.wf.steps, j)} - set(names)
            parsed = ship_mod.skip_payload(got, [*names, *later])
            if isinstance(parsed.get("skip"), dict) and later:
                # a later menu's steps (ship's) named here are asked at that menu, not refused as unknown here
                parsed = {**parsed, "skip": {k: v for k, v in parsed["skip"].items() if k not in later}}
            skips, lenses, problem = ship_mod.parse_skips(parsed, units)
            if not problem and refused and ship_mod.names_skip(got) is None:
                # 0.18.57 an answer refused for a skip without a reason, then one that says nothing about skipping:
                # never read as "run every step" (an approve with no payload ran the steps the person wanted skipped)
                problem = (f"you asked to skip {', '.join(refused)} without a reason. Give the reason (why), or answer "
                           "with skip: [] to run every step")
            if not problem:
                break
            if "needs a reason" in problem:
                refused = refused or sorted((ship_mod.names_skip(got) or set()) - later)
            title = f"{step.name}: {problem}"
        why = (answer.get("why") or "").strip()
        data = dict(state.get("data") or {})
        names = {s["step"] for s in skips}
        data["ship_skipped"] = [x for x in data.get("ship_skipped") or [] if x.get("step") not in names] + skips
        if lenses:
            data["lenses_chosen"] = lenses
        self._gate_mode(i, gates, got if ship_mod.gate_mode_of(got) else mode_payload)
        gates["log"].append(f"gate {step.id} approve" + (f": {why}" if why else ""))
        for sk in skips:
            gates["log"].append(f"gate {step.id} skip {sk['step']} ({sk['band']}): {sk['reason']}")
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": why,
                                                          "skipped": skips, **({"lenses": lenses} if lenses else {})})
        note = ("skipped: " + ", ".join(f"{s['step']} ({s['reason']})" for s in skips)) if skips else "every step runs"
        return {**extra_all, "gates": gates, "data": data, "note": note[:300]}, self.nav.after(i)

    def _gate_mode(self, i: int, gates: dict, payload: dict) -> None:
        """keel v1's start picker: a skip menu before the per-criterion loop may set the AC gates for this flow only
        (payload gate_mode, or the form's answers.gate_mode); the project's gates_mode stays the default."""
        mode = ship_mod.gate_mode_of(payload) if ship_mod.has_loop(self.wf.steps, i) else None
        if mode and mode != gates.get("mode"):
            gates["log"].append(f"gate mode for this flow: {mode} (was {gates.get('mode') or 'every-ac'})")
            gates["mode"] = mode

    def _choice_gate(self, i: int, step: Step, state: FlowState, gates: dict, item: dict):
        """One choice per item of a for_each loop (cover: test, delete or accept). The choice is stored on the item
        (decision, reason) and as markers[<id>].CHOICE; on_skip's choice ends the item as skipped, needs a reason and is
        appended to data[on_skip.record]."""
        lp = self.nav.loop_of(i)
        on_skip = step.on_skip or {}
        skip_choice = on_skip.get("choice")
        about = item.get("title") or json.dumps({k: v for k, v in item.items() if k not in ("id", "status", "show")},
                                                ensure_ascii=False, default=str)[:600]
        # 0.18.22 what the item wants the person to see (cover: the uncovered code lines), not only its name
        shown = f"\n\n{item['show']}" if isinstance(item.get("show"), str) and item["show"].strip() else ""
        detail = (f"{item['id']}: {about}{shown}\nLast step: {state.get('note', '')}\n\nChoose one: {' | '.join(step.choices)} "
                  f"(approve with payload {{\"choice\": \"<one>\"}}; plain approve = {step.choices[0]})."
                  + (f" {skip_choice} needs a reason (why): it is recorded and shown at the final review and in the PR body."
                     if skip_choice else ""))
        title, extra_all = f"{step.name} · {item['id']}", {}
        while True:
            ask = choices_mod.for_question({"step": step.id, "kind": "gate", "title": title, "detail": detail,
                                            "options": ["approve"], "choices": list(step.choices)},
                                           f"{self.wf.flow}:{step.id}", default=step.choices[0],
                                           needs_why={skip_choice: choices_mod.REQUIRED} if skip_choice else None)
            answer, extra = self._ask(state, ask, gate="choice")
            extra_all.update(extra)
            state = {**state, **extra}
            payload = answer.get("payload") or {}
            choice = str(payload.get("choice") or (skip_choice if payload.get("skip") and skip_choice else step.choices[0]))
            why = (answer.get("why") or str(payload.get("reason") or "")).strip()
            problem = (f"{choice} is not one of {', '.join(step.choices)}" if choice not in step.choices else
                       f"{choice} needs a reason" if choice == skip_choice and not why else None)
            if not problem:
                break
            title = f"{step.name} · {item['id']}: {problem}"
        gates["log"].append(f"gate {step.id} {choice} ({item['id']})" + (f": {why}" if why else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "choice": choice,
                                                          "why": why, "item": item["id"]})
        mk = dict(state.get("markers") or {})
        mk[step.id] = {"CHOICE": choice}
        mk["*"] = {**(mk.get("*") or {}), "CHOICE": choice}
        upd = {**extra_all, "gates": gates, "markers": mk}
        if choice == skip_choice:
            upd.update(self._end_item(state, lp, item, "skipped", choice, why, on_skip.get("record")))
            upd["note"] = f"{item['id']}: {choice}: {why}"[:300]
            return upd, self.nav.end(lp)
        items = [dict(x, decision=choice, reason=why or None) if x["id"] == item["id"] else x for x in _c.get_list(state, lp.key)]
        upd.update(_c.put_list(state, lp.key, items))
        upd["note"] = f"{item['id']}: {choice}" + (f": {why}" if why else "")
        return upd, self.nav.after(i)

    @staticmethod
    def _end_item(state: FlowState, lp: Loop, item: dict, status: str, decision: str | None, why: str,
                  record: str | None) -> dict:
        """The state update that ends a loop item (skipped) and, with `record`, keeps it in data[record] with its reason."""
        items = [dict(x, status=status, **({"decision": decision} if decision else {}), reason=why or None)
                 if x["id"] == item["id"] else x for x in _c.get_list(state, lp.key)]
        upd = _c.put_list(state, lp.key, items)
        if record:
            data = dict(upd.get("data") or state.get("data") or {})
            rec = {k: item[k] for k in ("id", "key", "file", "lines", "title", "app", "critical") if k in item}
            rec.update(decision=decision or status, reason=why or None)
            ident = rec.get("key") or rec["id"]
            data[record] = [x for x in data.get(record) or [] if (x.get("key") or x.get("id")) != ident] + [rec]
            upd = {**upd, "data": data}
        return upd

    def _writes_specs(self) -> bool:
        """Does any agent step of this flow write a spec (prompts.spec_writer, in a spec or triage phase)?"""
        return any(s.phase in ("spec", "triage") and prompts.spec_writer(s) for s in self.wf.steps)

    def _clarify_gate(self, i: int, step: Step, state: FlowState, gates: dict, asked: list[dict]):
        """The explorer's questions as a pause with buttons. Answers (clicked or typed) go back to the explorer."""
        n = len(asked)
        clar = state.get("clarify") or {}
        # an agent step with `asks: true`, or a code step that asked and reads the answers (the clarify flow's Step 0);
        # else the spec's explorer
        src = clar.get("step")
        asker = next((s.name for s in self.wf.steps if s.id == src), src) if src else None
        title = clar.get("title") or (f"{asker} has {n} question{'s' if n != 1 else ''}" if asker
                                      else f"The explorer has {n} question{'s' if n != 1 else ''} before the spec")
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "clarify", "title": title,
            "detail": clarify.describe(asked), "questions": asked, "options": ["approve"],
            "labels": {"approve": "Send my answers"}, "summary": notice.questions(asked)})
        payload = answer.get("payload") or {}
        given = dict(payload.get("answers") or {}) if isinstance(payload.get("answers"), dict) else {}
        said_why = (answer.get("why") or "").strip()
        # 0.18.28 an answer that asks back ("what do you mean", "?", "explain") is no answer: that question is asked
        # again once, in plain words with examples; the other answers wait for it (they are kept, not asked again)
        if not answer.get("auto"):
            fresh = [q for q in asked if not q.get("explained")]
            confused = [q for q in fresh if clarify.is_confused(given.get(q["id"]), q)]
            if not given and clarify.is_confused(said_why):
                confused = fresh
            if confused:
                ids = {q["id"] for q in confused}
                again = [clarify.explained(q, str(given.get(q["id"]) or said_why)) for q in confused]
                kept_q = list(clar.get("kept_questions") or []) + [q for q in asked if q["id"] not in ids]
                kept_a = {**(clar.get("kept_answers") or {}), **{k: v for k, v in given.items() if k not in ids}}
                plain = f"{title}: in plain words" if not str(title).endswith(": in plain words") else title
                gates["log"].append(f"gate {step.id} asked back on {', '.join(sorted(ids))}: asked again in plain words")
                self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "explain", "clarify": True,
                                                                  "why": f"asked back on {', '.join(sorted(ids))}"})
                return {"gates": gates, **extra, "clarify": {**clar, "questions": again, "title": plain,
                                                             "kept_questions": kept_q, "kept_answers": kept_a},
                        "note": f"{len(again)} question(s) asked again in plain words"}, step.id
        # the answers kept from before a question was asked again join this round's
        if clar.get("kept_questions"):
            asked = [q for q in clar["kept_questions"]] + [
                {k: v for k, v in q.items() if k not in ("explained", "asked_back")} for q in asked]
            for q in asked:
                if q.get("question", "").count("\n"):
                    q["question"] = q["question"].split("\n", 1)[0]
            given = {**(clar.get("kept_answers") or {}), **given}
        if not answer.get("auto"):
            for q in asked:
                v = given.get(q["id"])
                if clarify.is_confused(v, q):
                    # still asked back after the plain words: never the recommended option in its place
                    given[q["id"]] = (f"{v} (the person did not understand this question, also in plain words: do not "
                                      "pick an option for them; write it under the open questions)")
        payload = {**payload, "answers": given} if given else payload
        round_no = int(state.get("clarify_rounds") or 0) + 1
        auto = answer.get("why") if answer.get("auto") else ""
        text = clarify.answers_text(asked, payload.get("answers"), "" if auto else (answer.get("why") or ""), round_no,
                                    spec=not src, rounds=int(clar.get("rounds") or clarify.MAX_ROUNDS))
        gates["log"].append(f"gate {step.id} answered the explorer's {n} question(s)" + (f": {auto}, the recommended options" if auto else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve", "why": text[:600],
                                                          "clarify": True})
        upd = {"gates": gates, **extra, "feedback": text, "clarify": {}, "clarify_rounds": round_no,
               "note": f"answered {n} question(s)"}
        if src:
            # keep the answers for the add-on's records too (data.<gate>_clarify: questions and answers, every round)
            kept = list(((state.get("data") or {}).get(f"{step.id}_clarify")) or [])
            kept.append({"round": round_no, "questions": asked, "answers": dict(payload.get("answers") or {}), "note": answer.get("why") or ""})
            # and the answers as text for the steps after it (data.<asking step>_answers: the feature's spec step reads the
            # interview's), every round
            now = clarify.answers_message(asked, payload.get("answers")) + (f"\nAlso, in the person's words: {answer['why']}"
                                                                         if answer.get("why") and not auto else "")
            said = "\n\n".join(x for x in (str((state.get("data") or {}).get(f"{src}_answers") or ""), now) if x)
            upd["data"] = {**(state.get("data") or {}), f"{step.id}_clarify": kept, f"{src}_answers": said}
            target = self.nav.jump(src, i)
        else:
            target = self.nav.jump(step.back, i) if step.back else self.nav.enter(max(i - 1, 0))
        return upd, target

    def _init_questions(self, step: Step, state: FlowState, gates: dict) -> dict:
        """keel init's three questions as a clarify form (0.19.0): the answers ({run, services, knowledge}) set the init
        config; approving without answers takes the recommended options (keel's defaults). An old client's "Use my
        answers" (a send-back with typed words) still goes through the keyword parser."""
        root, fast = self.ctx.root, bool(self.ctx.settings.get("fast"))
        # 0.18.57 the project notes are opt-in: the question says what building them costs (this project's models,
        # what the librarians read of its code); a failing estimate never hides the question
        try:
            cost = knowledge.build_estimate(root, self.ctx.models)["label"]
        except Exception:  # noqa: BLE001
            cost = None
        asked = init_gates.clarify_questions(root, fast=fast, cost=cost)
        answer, extra = self._ask(state, {
            "step": step.id, "kind": "clarify", "title": f"{_c._COUNT.get(len(asked), len(asked))} questions",
            "detail": clarify.describe(asked),
            "questions": asked, "options": _c.OPTIONS, "labels": {"approve": "Use these answers", "reject": "Use my answers"}},
            gate="clarify")
        why = (answer.get("why") or "").strip()
        given = (answer.get("payload") or {}).get("answers")
        mine = answer.get("decision") == "reject" and why
        if isinstance(given, dict) and given:
            init = init_gates.from_answers(root, given, fast=fast)
            said = "answered: " + "; ".join(f"{k} {v}" for k, v in given.items())
        else:
            init = init_gates.answers(root, why if mine else None, fast=fast)
            said = "answered: " + why if mine else "defaults"
        if cost:
            init["notes_cost"] = cost          # run only (the plan and the hand-over say it), never in the config
        gates["log"].append(f"gate questions: {said}"[:300] + (f" ({why})" if answer.get("auto") else ""))
        self.ctx.emit("gate.decided", step=step.id, data={"gate": step.name, "decision": "approve",
                                                          "why": said if said != "defaults" else "defaults"})
        return {"gates": gates, **extra, "init": init,
                "note": "answers: " + ", ".join(f"{k} {v}" for k, v in init.items() if k not in ("said", "write_sections", "notes_cost"))[:300]}
