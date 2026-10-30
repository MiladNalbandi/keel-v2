"""The Compiler's agent steps: one agent call (budget, usage, tools, markers) and the step around it."""
from __future__ import annotations

import asyncio
import re
import tempfile
import time
import uuid

from . import agent_knowledge, blockers as push_gates, choices as choices_mod, clarify, flow_actions, guard_ctx, markers, memory as memory_mod, other_projects, prompts, security_actions, spec_check, stacks, test_scope, tools as tool_runner
from .. import addons, agent_names, extensions, models, rules
from ..models import catalog, cli_runners, usage as provider_usage
from ..models.base import AgentRequest, AgentResult
from ..models.stream import DeltaThrottle
from ..tools import git, guard, mcp
from ..tools.agent_tools import ToolBox
from ..workflows.model import Step
from .actions import ActionResult
from .findings import REVIEWERS, blocking, key as finding_key, nonblocking, same as same_finding, unique
from .state import FlowState
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END
from pathlib import Path

from . import compiler as _c      # the compiler module's own names (looked up when called: tests patch some)

# the tools whose refusal is a read, not a write: they never count toward the guard loop (CodeMixin._guard_loop)
READ_TOOLS = re.compile(r"^(read_file|read|grep|glob|ls|list_files|search|webfetch|websearch)$", re.I)


class AgentMixin:

    async def _run_agent(self, state: FlowState, step: Step, agent: str, index: int, section: str | None = None,
                         item: dict | None = None) -> tuple[AgentResult, dict, ToolBox]:
        ctx = self.ctx
        phase = state["phase"]
        ac = _c._ac(state) if step.per_ac else None
        model = self._model(state, step, agent)
        call_id = uuid.uuid4().hex
        # `root: item` (a parallel step): this agent works in its item's folder, read only (another project, e.g. one
        # repo of a product); otherwise in the thread's folder.
        item_root = bool(step.root == "item" and item and item.get("root"))
        root = str(item["root"]) if item_root else ctx.root
        cfg = rules.load_config(root)
        # What this agent may use: its knowledge sections, the code graph, its memory (agent_knowledge.py).
        know = agent_knowledge.for_agent(agent, ctx.agents)
        # The parts' own servers join the MCP servers (keel_engine/extensions.py mcp_specs: the code graph's once the
        # project's index is ready); filter_mcp keeps the code graph only for agents with code_graph on.
        specs, allow = await asyncio.to_thread(extensions.with_part_servers, list(ctx.mcp), list(step.tools or []),
                                               extensions.Agent(agent, know), root)
        # a plugin's read tools (keel-db, keel-git) when the plugin is on and this agent may use them (Tools page)
        wanted = mcp.parse_allow(allow)
        servers = extensions.servers()
        mine = [n for n in extensions.enabled(ctx.settings) if servers.get(n) in wanted]
        plugin_key = extensions.open_call(project=ctx.project_id, root=root, keys=ctx.keys, plugins=mine,
                                          who=f"agent:{agent}", bus=ctx.bus, thread=ctx.thread_id) if mine else None
        if plugin_key:
            specs += extensions.server_specs(mine, plugin_key)
        mcp_specs, tools_allow = agent_knowledge.filter_mcp(specs, allow, know)
        if step.mcp == "gateway":
            # 0.19.0 harness mode: the project's MCP servers through keel's own server only (mcp_gateway.py)
            mcp_specs, tools_allow = mcp.gateway(mcp_specs, tools_allow, [s.get("name") for s in ctx.mcp or []])

        refused_paths: list[str] = []      # the guard-loop count (CodeMixin._guard_loop) reads the files refused here

        def on_refuse(tool: str, path: str, reason: str, command: str | None = None):
            if path and not READ_TOOLS.match(tool or ""):       # a refused read (a file read twice) is not a frozen file
                refused_paths.append(path)
            data = {"tool": tool, "path": path or "", "reason": reason, "agent": agent, "phase": phase}
            if command:
                data["command"] = command
            ctx.emit("guard.refused", step=step.id, call_id=call_id, data=data)

        toolbox = ToolBox(root, phase, cfg=cfg, lane=rules.ac_lane(ac) if ac else None,
                          ac=(ac or {}).get("id"), ac_layer=(ac or {}).get("layer", "API"), on_refuse=on_refuse,
                          unlocks=state.get("unlocks") or [], agent=agent, knowledge=know,
                          readonly=self._readonly() or item_root)
        toolbox.refused_paths = refused_paths
        # keel 0.18.64 the other projects registered in this keel: read only (runtime/other_projects.py)
        toolbox.others_off = other_projects.is_off(cfg)
        toolbox.others = await asyncio.to_thread(other_projects.usable, ctx.other_projects, root, ctx.project_id, cfg)
        # 0.18.64 keel's own MCP server reads only this flow's project (and the ones it may read): keel_read_file,
        # keel_search, keel_knowledge, keel_agent_prompt ... on another project are refused there (mcp_api.KeelApi)
        if ctx.project_id:
            mcp_specs = mcp.scoped(mcp_specs, tools_allow, [ctx.project_id, *(o["id"] for o in toolbox.others if o.get("id"))])
        toolbox.flow = f'"{ctx.title}" (thread {ctx.thread_id})'     # 0.18.64 a refused commit names this flow
        # keel v1's push gate for the agent's own `git push` / `gh pr create` (rules.push_gate)
        toolbox.push_blockers = await asyncio.to_thread(push_gates.for_agents, root, state.get("base_head"), ctx.project_id or None)
        ctx.emit("agent.started", step=step.id, call_id=call_id, data={
            "agent": agent, "provider": model["provider"], "model": model.get("model"), "mode": model.get("mode"),
            "effort": catalog.effort_for(model), "phase": phase, "ac": (ac or {}).get("id"), "index": index, **({"item": item["id"]} if item else {})})
        counter = {"n": 0}

        # Agent memory: the same step again (restart, try again, send-back) continues this agent's own session.
        mem = ctx.memory
        key = memory_mod.attempt_key(step.id, ac or item, agent, index, section)
        if step.session == "thread":
            # 0.19.0 harness mode: every step marked `session: thread` continues one harness session (plan, build,
            # fix-ups), as a person does in one Claude Code / Codex / Copilot / OpenCode session
            key = memory_mod.THREAD_KEY
        elif agent in REVIEWERS:
            # 0.18.17 a reviewer remembers one commit only: after a fix commit it continued its old session ("use what
            # you already know") and repeated the fixed findings word for word. A new HEAD is a new review.
            key += "|" + ((await asyncio.to_thread(git.head, root)) or "")[:12]
        prev = await mem.get(key) if mem and know["memory"] else None
        same = bool(prev and prev["provider"] == model["provider"] and prev["root"] == root)
        can_resume = model.get("mode") != "api" and model["provider"] in _c.RESUMABLE
        resuming = bool(same and can_resume and prev.get("session"))
        session = prev["session"] if resuming else (str(uuid.uuid4()) if can_resume and model["provider"] in _c.NAMED_SESSION else None)
        if mem:
            await mem.start(key, model["provider"], root, session, keep_trail=same)

        # the agent's text as it writes it: agent.delta events (the text so far, every 80 ms; never stored)
        live_text = DeltaThrottle(lambda d: ctx.emit("agent.delta", step=step.id, call_id=call_id, data=d))

        usage_sent = {"at": 0.0}

        def emit(kind: str, text: str = "", **extra):
            if kind == "delta":
                live_text.add(str(text))
                return
            if kind == "thinking_delta":
                # 0.18.23 thinking as the model writes it: KeelBot shows it live; a flow's agent keeps its whole
                # thinking step only
                return
            if kind == "usage":
                # 0.18.17 the run's usage so far (runners that learn it while they run): kept for the thread's view and
                # the cap, and sent as agent.usage at most every USAGE_EVERY seconds; agent.finished still books the total
                ctx.running[call_id] = {k: extra[k] for k in ("tokens_in", "tokens_out", "tokens_cached", "cost_usd") if extra.get(k)}
                now = time.monotonic()
                if now - usage_sent["at"] >= _c.USAGE_EVERY:
                    usage_sent["at"] = now
                    ctx.emit("agent.usage", step=step.id, call_id=call_id, data={"agent": agent, **_c._usage_fields(ctx.running[call_id])})
                    self._running_cap(state, step)
                return
            live_text.reset()
            counter["n"] += 1
            # Every string field (text, diff, output, ...) is bounded; newlines are kept for the web's Markdown and diff views.
            data = {"n": counter["n"], "kind": kind, "text": str(text)[:_c.STEP_FIELD_MAX]}
            data.update({k: (v[:_c.STEP_FIELD_MAX] if isinstance(v, str) else v) for k, v in extra.items() if v is not None})
            ctx.emit("agent.step", step=step.id, call_id=call_id, data=data)
            line = memory_mod.trail_line(kind, str(text), extra) if mem else None
            if line:
                mem.note(key, line)

        prompt = prompts.task_prompt(agent=agent, phase=phase, step_name=step.name, title=ctx.title, root=root, ac=ac,
                                     acs=state.get("acs") or [], feedback=state.get("feedback"), index=index, spec=state.get("spec"),
                                     section=section, unlocks=state.get("unlocks") or [], request=ctx.request,
                                     knowledge=know, graph=agent_knowledge.has_codegraph(mcp_specs, tools_allow), item=item,
                                     pid=ctx.project_id or "", writes_spec=prompts.spec_writer(step),
                                     ac_commits=flow_actions.ac_commits(root, ac["id"], state.get("base_head"))
                                     if ac and agent == "criterion-reviewer" else None)
        met = [a for a in state.get("acs") or [] if a.get("status") == _c.ALREADY_MET]
        if agent == "criterion-reviewer" and ac and ac.get("status") == _c.ALREADY_MET:
            # 0.18.28 "already passes", approved by the run mode: the test is all there is to review
            prompt += (f"\n\n{ac['id']} passed before any code was written for it (\"already passes\"), so there is no "
                       "GREEN commit: review its test only. Focus on what lets a passing test lie or break others: "
                       "isolation (it depends on another test's data or order), cleanup (it writes to the database, files "
                       "or a cache and leaves them behind, also on a second connection or a committed transaction), and "
                       "assertions (it asserts something that was true before the criterion, or nothing at all).")
        said = ((state.get("data") or {}).get("change") or {}).get("history") or []
        if step.id == "triage" and said:
            # 0.18.74 triage once: the earlier answer, to keep unless something new shows it was wrong
            last = said[-1]
            prompt += (f"\n\nYou triaged this change before: SIZE {last.get('size')}. "
                       + (f"Your reason then: {str(last.get('reason') or '')[:400]} " if last.get("reason") else "")
                       + "Keep that size unless what happened since (below) shows it was wrong. If you change it, start "
                         "with a line WHY: <what changed your mind>.")
        if agent == "branch-reviewer" and not item and root:
            # 0.18.65 the full-diff review reads the branch against the canonical remote's base, without keel init's
            # setup commits (runtime/bases.py branch_scope), never a local main that is behind or went its own way
            from . import bases

            found = await asyncio.to_thread(bases.branch_scope, root, None, state.get("base_head"))
            own = (state.get("data") or {}).get("own_since")
            if own:
                # 0.18.74 a flow that continues a branch: its own commits only (verdict_actions.flow_base)
                found = {**found, "scope": f"git diff {own[:12]}...HEAD", "compared": f"this flow's start ({own[:7]}): the "
                         "branch had commits of its own before this flow, and they are not this flow's to review",
                         "setup": [], "warning": None}
            if found.get("scope"):
                prompt += (f"\n\nThe branch diff to review: `{found['scope']}` (the base: {found['compared']}). Use this "
                           "range, not main...HEAD." + "".join(f" {x}." for x in (found.get("warning"),) if x)
                           + (f" {bases.setup_note(found['setup'])}" if found.get("setup") else ""))
        if agent == "branch-reviewer" and met:
            seen = (state.get("data") or {}).get("already_met") or {}
            prompt += ("\n\nNot reviewed per AC: these criteria passed before any code was written (\"already passes\"), so "
                       "their tests had no GREEN to prove them. Review each test here: isolation, cleanup of what it "
                       "writes (database rows, files), and whether its assertions prove the criterion.\n"
                       + "\n".join(f"- {a['id']} {a.get('title', '')}"
                                   + (" (approved by the run mode; the criterion-reviewer read its test)" if (seen.get(a["id"]) or {}).get("auto")
                                      else " (approved by a person; no per-AC review)") for a in met))
        if ac and rules.edits_code(phase):
            # the files this criterion (its plan, the request) names that this phase freezes: said before the agent
            # starts, so it stops with FROZEN: <file> instead of burning rounds on edits keel puts back
            data = state.get("data") or {}
            plan = prompts.plan_row(spec_check.read_spec(root, state.get("spec")), ac["id"]) if state.get("spec") else ""
            frozen = await asyncio.to_thread(test_scope.frozen_named, root, phase,
                                             [ac.get("title"), plan, data.get("plan_text"), ctx.request], state.get("unlocks") or [])
            note = test_scope.frozen_note(frozen, phase)
            if note:
                prompt += "\n\n" + note
        asks = prompts.step_asks(step, lambda path: self._seed_value("$" + path, state, item))
        if asks:
            prompt = f"{prompt}\n{asks}"
        if phase == "spec" and not item and not state.get("feedback") and (step.asks or prompts.spec_writer(step)):
            # the parent flow's whole evidence (a spike's report) for the interview and the spec writer
            evidence = prompts.evidence_block((state.get("data") or {}).get("seed"))
            if evidence:
                prompt = f"{prompt}\n\n{evidence}"
        if phase == "spec" and not item and self._spec_step(step):
            # 0.18.64 a spec handed over (the clarify hand-over, a spec pasted into the request, the spec it started
            # from) is kept whole: the spec writer fills its gaps, and the spec check names what it dropped
            named = {"the handed-over document": "the document handed over by the flow that started this one",
                     "the request": "what the user asked for"}
            for where, _doc in spec_check.seeded(ctx.request, state.get("data")):
                prompt += "\n\n" + prompts.seeded_block(named.get(where, where))
        started_from = (state.get("data") or {}).get("spec")
        if phase == "spec" and started_from and started_from == state.get("spec") and not state.get("feedback"):
            # 0.17.3 started from a spec (the Wiki): the agents change that file instead of writing another one
            prompt += (f"\nThis flow starts from the spec {started_from}: change that file (keep its criteria ids and words), "
                       "do not write a new spec file.")
        others_note = other_projects.prompt_note(toolbox.others)
        if others_note:
            prompt += "\n\n" + others_note
        lint_notes = (state.get("data") or {}).get("lint_notes") or []
        if lint_notes:
            why = "know them while you review" if rules.read_only(phase) else "fix them when they are in your scope"
            prompt += (f"\n\nStatic checks on the files the last step changed ({why}):\n"
                       + "\n".join(f"- {n}" for n in lint_notes))
        note = memory_mod.resume_note(prev, resuming) if same else ""
        if note:
            prompt = f"{note}\n\n{prompt}"
            emit("text", "Continuing this agent's earlier session for this step." if resuming
                 else "This agent gets a summary of its last try on this step.")
        with tempfile.TemporaryDirectory(prefix="keel-agent-") as tmp:
            req = AgentRequest(agent=agent, system=prompts.system_prompt(agent, ctx.skills), prompt=prompt, root=root,
                               phase=phase, model=model, toolbox=toolbox, ac=ac, acs=state.get("acs") or [], title=ctx.title,
                               step_name=step.name, index=index, feedback=state.get("feedback"), mcp_specs=mcp_specs,
                               tools_allow=tools_allow, key=models.key_for(model["provider"], ctx.keys), workdir=tmp,
                               keys=dict(ctx.keys), section=section, session=session, resume=resuming,
                               on_session=(lambda sid: mem.set_session(key, sid)) if mem else None, thread=ctx.thread_id,
                               knowledge=know, item=item)
            # 0.18.64 the exact prompt this call got, kept with the job (api events, GET /jobs/{id}/prompt, the MCP tool
            # keel_agent_prompt): a field report could not tell whether a retry's prompt still had the reject note.
            # A record, not a live view: the api stores it and sends it to no screen.
            system_text, task_text = str(req.system or ""), str(req.prompt or "")
            ctx.emit("agent.prompt", step=step.id, call_id=call_id, data={
                "agent": agent, "ac": (ac or {}).get("id"),
                "system": system_text[:_c.PROMPT_RECORD_MAX], "system_cut": len(system_text) > _c.PROMPT_RECORD_MAX,
                "prompt": task_text[:_c.PROMPT_RECORD_MAX], "prompt_cut": len(task_text) > _c.PROMPT_RECORD_MAX})
            # The guard context keel's hook reads on every tool call (CLI agents). It sits in this run's scratch folder,
            # outside the project; an unlock granted while the agent runs rewrites it (ThreadContext.add_unlocks).
            gfile = guard_ctx.GuardFile(Path(tmp) / guard_ctx.FILE, **guard_ctx.context_for(req))
            req.guard_ctx = gfile.path
            live = [gfile, toolbox]
            ctx.guards.extend(live)
            t0 = time.time()
            try:
                res = await _c.run_or_continue(models.runner_for(model), req, emit, can_resume,
                                            lambda: mem.get(key) if mem else None)
            except asyncio.CancelledError:
                # 0.18.17 a stopped run used what it reported so far (it booked 0 before)
                so_far = dict(ctx.running.get(call_id) or {})
                _c.add_usage(ctx.unbooked, so_far)
                ctx.emit("agent.finished", step=step.id, call_id=call_id, data={"agent": agent, "status": "stopped",
                                                                                **_c._usage_fields(so_far), "premium_requests": 0})
                raise
            except Exception as exc:
                emit("error", f"{exc}{(' ' + exc.hint) if getattr(exc, 'hint', '') else ''}", ok=False)
                if mem:
                    await mem.finish(key, "failed", str(exc))
                # 0.18.17 an error that carries no usage still used what the run reported so far
                used = getattr(exc, "usage", None) or dict(ctx.running.get(call_id) or {})
                _c.add_usage(ctx.unbooked, used)
                # A failed run still used tokens (a turn limit after 30 turns is not free): the job and the budget count them.
                ctx.emit("agent.finished", step=step.id, call_id=call_id, data={
                    "agent": agent, "status": "failed", "tokens_in": used.get("tokens_in", 0), "tokens_out": used.get("tokens_out", 0),
                    "tokens_cached": used.get("tokens_cached", 0), "cost_usd": round(used.get("cost_usd", 0.0), 6),
                    "premium_requests": 0, "result": str(exc)[:500]})
                raise
            finally:
                ctx.running.pop(call_id, None)
                for g in live:
                    ctx.guards.remove(g)
                extensions.close_call(plugin_key)
                self._usage_event(step, model["provider"], t0)
        if mem:
            await mem.finish(key, "done", res.text or "")
        if not res.cost_usd and model.get("mode") == "api" and model["provider"] != "fake":
            res.cost_usd = catalog.cost_usd(model["provider"], model.get("model", ""), res.tokens_in, res.tokens_out)
        if not res.cost_usd and res.premium_requests:
            # 0.18.16 Copilot (any mode) costs premium requests, not tokens: it showed $0 for every call before
            res.cost_usd = catalog.premium_cost(model["provider"], model.get("model", ""), res.premium_requests)
        ctx.emit("agent.finished", step=step.id, call_id=call_id, data={
            "agent": agent, "status": "done", "tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "tokens_cached": res.tokens_cached,
            # 0.18.64 the whole answer up to RESULT_MAX (it was 2,000 characters): keel_flow_diff compares a step's last
            # two answers (a spec assembled again after a reject), and a cut answer hid what changed
            "cost_usd": round(res.cost_usd, 6), "premium_requests": res.premium_requests, "result": res.text[:_c.RESULT_MAX]})
        return res, model, toolbox

    # ------------------------------------------------------------ budget

    def _usage_event(self, step: Step, provider: str, since: float):
        """provider.usage: the plan's windows this run reported (claude's rate_limit_event), for the api's usage cards."""
        entry = provider_usage.LATEST.get(provider)
        if entry and entry["at"] >= since:
            self.ctx.emit("provider.usage", step=step.id, data={"provider": provider, "windows": entry["windows"],
                                                                "source": entry["source"], "at": provider_usage.iso(entry["at"])})

    def _first_cap(self) -> int:
        """The token cap the flow started with (initial_state): the settings' (or start request's) cap_tokens, else the
        workflow's budget.max_tokens."""
        b = self.wf.budget
        return int(self.ctx.settings.get("cap_tokens") or (b.max_tokens if b and b.max_tokens else 0) or 0)

    def _running_cap(self, state: FlowState, step: Step) -> None:
        """0.18.17 while agents run: the flow's tokens with what they used so far (state.usage holds only finished
        calls). Past the token cap, budget.warn says so once per step (a running CLI cannot be paused); _budget asks
        before the next step, which then counts this step's whole usage."""
        ctx = self.ctx
        cap = int((state.get("usage") or {}).get("cap_tokens") or 0)
        if not cap or ctx.running_warned == step.id:
            return
        used = _c.budget_tokens(_c.running_usage(state.get("usage") or {}, ctx.running))
        if used >= cap:
            ctx.running_warned = step.id
            ctx.emit("budget.warn", step=step.id, data={
                "used": used, "cap": cap, "pct": round(used * 100 / cap), "running": True,
                "detail": f"This flow has used {used:,} of {cap:,} tokens while {step.name} still runs. keel asks before the next step."})

    async def _provider_window(self, state: FlowState, step: Step) -> dict:
        """Before a subscription agent: warn when its plan window is nearly used (settings.usage_warn, default 0.80) and
        pause at settings.usage_pause (default 0.95): continue, wait for the reset, use the cheaper model, or stop."""
        ctx = self.ctx
        model = self._model(state, step, step.agent or "")
        provider = model.get("provider")
        if model.get("mode") == "api" or provider in (None, "fake"):
            return {}
        await provider_usage.refresh_before_step(model, ctx.keys)
        w = provider_usage.fullest(provider)
        warn, pause = float(ctx.settings.get("usage_warn") or 0.8), float(ctx.settings.get("usage_pause") or 0.95)
        if not w or w["used_pct"] < warn:
            return {}
        seen = dict(state.get("usage_seen") or {})
        key = f"{provider}:{w['window']}:{w.get('resets_at') or ''}"
        info = {"provider": provider, "window": w["window"], "used_pct": w["used_pct"], "resets_at": provider_usage.iso(w.get("resets_at")),
                "detail": provider_usage.headline(provider, w), "kind": "usage"}
        if w["used_pct"] < pause or seen.get(key) == "ok":
            if seen.get(key):
                return {}
            ctx.emit("budget.warn", step=step.id, data=info)
            return {"usage_seen": {**seen, key: "warn"}}
        qkey = (ctx.thread_id, step.id, key)
        question = provider_usage.QUESTIONS.setdefault(qkey, {
            "step": step.id, "kind": "usage", "title": info["detail"], "options": _c.OPTIONS, "choices": ["continue", "wait", "cheaper", "stop"],
            "detail": "Continue uses the plan anyway (the provider may refuse when it is full). Wait sleeps until the window resets, "
                      "then goes on. Cheaper switches to the cheaper model in settings. Stop ends the flow here.",
            "choice_info": choices_mod.choice_info(["continue", "wait", "cheaper", "stop"], context="usage")})
        answer, extra = self._ask(state, question)
        provider_usage.QUESTIONS.pop(qkey, None)
        choice = ((answer or {}).get("payload") or {}).get("choice")
        if (answer or {}).get("decision") != "approve" or choice == "stop":
            ctx.emit("budget.stop", step=step.id, data={**info, "why": (answer or {}).get("why")})
            return {**extra, "status": "stopped", "note": f"stopped: {info['detail']}"}
        upd = {**extra, "usage_seen": {**seen, key: "ok"}}
        cheaper = _c._real_cheaper(ctx)
        if choice == "cheaper" and cheaper:
            ctx.emit("budget.warn", step=step.id, data={**info, "action": "cheaper", "model": cheaper})
            return {**upd, "model_override": cheaper, "note": "plan window nearly used; switched to the cheaper model"}
        if choice == "cheaper":
            return {**upd, "note": "no real cheaper model is set (Settings › Cheaper model): went on with the same model"}
        if choice == "wait" and w.get("resets_at"):
            delay = min(max(0.0, w["resets_at"] - time.time()) + 30, 8 * 24 * 3600)
            ctx.emit("budget.warn", step=step.id, data={**info, "action": "wait", "seconds": int(delay)})
            await asyncio.sleep(delay)
        return upd

    def _budget(self, state: FlowState, step: Step) -> dict:
        """Before an agent step: the flow's token cap (usage.cap_tokens), its dollar cap (usage.cap_usd, against the
        reported usage.cost_usd) and the step's token limit (its own max_tokens, or settings.step_cap_tokens when that
        is smaller or the step has none). Over one: switch to the cheaper model, stop, or pause and ask."""
        ctx = self.ctx
        usage = dict(state.get("usage") or {})
        cap = int(usage.get("cap_tokens") or 0)
        used = _c.budget_tokens(usage)
        cap_usd = float(usage.get("cap_usd") or 0)
        cost = float(usage.get("cost_usd") or 0)
        if cap or cap_usd:      # what side agents spent for this flow (the Supervisor) counts toward its caps too
            side = _c.side_usage.total(ctx.thread_id)
            used, cost = used + side["tokens"], cost + side["cost_usd"]
        step_cap, own_cap = _c._step_cap(step, ctx.settings)
        step_used = int((state.get("step_tokens") or {}).get(step.id, 0))
        over_step = bool(step_cap) and step_used >= step_cap
        over_cap = bool(cap) and used >= cap
        over_usd = cap_usd > 0 and cost >= cap_usd
        if not (over_step or over_cap or over_usd):
            if not state.get("warned"):
                if cap and used >= 0.8 * cap:
                    ctx.emit("budget.warn", step=step.id, data={"used": used, "cap": cap, "pct": round(used * 100 / cap)})
                    return {"warned": True}
                if cap_usd and cost >= 0.8 * cap_usd:
                    ctx.emit("budget.warn", step=step.id, data={"cost_usd": round(cost, 4), "cap_usd": cap_usd,
                                                                "pct": round(cost * 100 / cap_usd)})
                    return {"warned": True}
            return {}
        wf_on = self.wf.budget.on_limit if self.wf.budget else None
        on_cap = ctx.settings.get("on_cap")
        if over_step:       # the step's own limit says what it does; the project's per-step cap has step_on_cap
            on = (step.on_limit if own_cap else ctx.settings.get("step_on_cap")) or on_cap or wf_on or "pause"
        elif over_cap:
            on = on_cap or wf_on or "pause"
        else:
            on = ctx.settings.get("on_cap_usd") or on_cap or wf_on or "pause"
        what = "token" if (over_step or over_cap) else "cost"
        cheaper = _c._real_cheaper(ctx)          # None (never the fake model for a real flow): pause and ask below
        info = {"used": used, "cap": cap, "step_used": step_used, "step_cap": step_cap or None,
                "cost_usd": round(cost, 4), "cap_usd": cap_usd or None, "limit": "step" if over_step else what}
        if on == "cheaper" and cheaper and state.get("model_override") != cheaper:
            ctx.emit("budget.warn", step=step.id, data={**info, "action": "cheaper", "model": cheaper})
            return {"model_override": cheaper, "warned": True, "note": f"{what} cap reached; switched to the cheaper model"}
        if on == "stop":
            ctx.emit("budget.stop", step=step.id, data=info)
            return {"status": "stopped", "note": f"{what} cap reached; flow stopped"}
        limit_txt = (f"{step_used:,} of {step_cap:,} tokens for this step" if over_step
                     else f"{used:,} of {cap:,} tokens" if over_cap else f"${cost:,.2f} of ${cap_usd:,.2f}")
        new_cap = _c.raised_cap(used, cap, self._first_cap()) if over_cap else 0
        new_usd = round(cost + max(cap_usd, 0.01), 4) if over_usd else 0.0
        after = [f"the flow's cap goes up to {new_cap:,} tokens" if over_cap else "",
                 f"its cost cap to ${new_usd:,.2f}" if over_usd else "",
                 f"this step gets another {step_cap:,} tokens" if over_step else ""]
        answer, extra = self._ask(state, {"step": step.id, "kind": "budget", "title": "Token cap reached" if what == "token" else "Cost cap reached",
                                          "detail": f"This flow has used {limit_txt}. Approve to continue: "
                                                    f"{', '.join(a for a in after if a)}. Reject to stop.",
                                          "options": _c.OPTIONS})
        if (answer or {}).get("decision") != "approve":
            ctx.emit("budget.stop", step=step.id, data={**info, "why": (answer or {}).get("why")})
            return {**extra, "status": "stopped", "note": f"stopped at the {what} cap"}
        payload = (answer or {}).get("payload") or {}
        upd: dict = {"warned": False, **extra}
        if over_cap:
            try:
                usage["cap_tokens"] = int(payload.get("cap_tokens") or new_cap)
            except (TypeError, ValueError):
                usage["cap_tokens"] = int(new_cap)      # not a number: keep keel's suggestion, not a crash
            upd["usage"] = usage
        if over_usd:
            try:
                usage["cap_usd"] = round(float(payload.get("cap_usd") or new_usd), 4)
            except (TypeError, ValueError):
                usage["cap_usd"] = round(float(new_usd), 4)
            upd["usage"] = usage
        if over_step:
            st = dict(state.get("step_tokens") or {})
            st[step.id] = 0
            upd["step_tokens"] = st
        return upd

    # ------------------------------------------------------------ step kinds

    async def agent_step(self, i: int, step: Step, state: FlowState):
        ctx = self.ctx
        ctx.running_warned = None
        upd = self._budget(state, step)
        if upd.get("status") == "stopped":
            return upd, END
        state = {**state, **upd}
        plan = await self._provider_window(state, step)
        upd = {**upd, **plan}
        if plan.get("status") == "stopped":
            return upd, END
        state = {**state, **plan}

        calls: list[tuple[str, int]] = []
        code_lanes: list[str] = []
        if step.kind == "agent":
            calls = [(step.agent or "", 0)]
        elif step.lanes:
            lane_sections: dict[int, str] = {}
            for lane in step.lanes:
                if lane.kind == "agent":
                    who = lane.sub or step.agent or ""
                    init = state.get("init") or {}
                    # 0.18.22 write_sections: the ones written this run (a section that exists is kept, not rebuilt)
                    chosen = init.get("write_sections") if "write_sections" in init else init.get("knowledge_sections")
                    if who == "librarian" and isinstance(chosen, list):
                        # init: one librarian per section the user chose, each told its section (none: no librarian).
                        for sec in chosen:
                            lane_sections[len(calls)] = sec
                            calls.append((who, len(lane_sections) - 1))
                    else:
                        calls += [(who, k) for k in range(max(1, step.parallel or 1))]
                elif lane.sub:
                    code_lanes.append(lane.sub)
        else:
            calls = [(step.agent or "", k) for k in range(max(1, step.parallel or 1))]
        sections = self._sections(step, state)
        if sections:
            calls = [(step.agent or "librarian", k) for k in range(len(sections))]
        section_of = {n: sections[k] for n, (_a, k) in enumerate(calls)} if sections else \
            (dict(lane_sections) if step.lanes else {})
        # Dynamic fan-out: one call per item of a state list (at most `cap`); a for_each step gives its item to each call.
        loop_item = self._item(state, step)
        item_of: dict[int, dict] = {}
        if step.items_from:
            items = _c.get_list(state, step.items_from)[: step.cap or None]
            if not items:
                note = f"{step.name}: no items in {step.items_from}; no agent ran"
                return {**upd, "note": note, "data": {**(state.get("data") or {}), f"{step.id}_results": []}}, self.nav.after(i)
            calls = [(self._item_agent(step, it), k) for k, it in enumerate(items)]
            item_of = dict(enumerate(items))
        elif loop_item:
            item_of = {n: loop_item for n in range(len(calls))}

        before = await asyncio.to_thread(guard.snapshot, ctx.root)
        started = time.time()       # a spec a CLI agent wrote with its own tools is found by its time (_acs_from)
        ac = _c._ac(state) if step.per_ac else None

        async def lane(action: str) -> ActionResult:
            # 0.18.16 a code lane (init's setup ladder) is a live job too, like a code step
            job = _c._CodeJob(self.ctx, step, state, name=f"{step.name}: {action}", actions=[action])
            job.start()
            a = self._action_input(state, ac)
            a.step, a.progress = step.id, job.progress
            t0 = time.monotonic()
            job.progress(f"▶ {action}", tool=action)
            try:
                r = await _c.run_action(action, a)
            except BaseException:
                job.finish("failed")
                raise
            job.progress(r.note or action, tool=action, ok=bool(r.ok), ms=int((time.monotonic() - t0) * 1000),
                         output=(r.detail or "")[-_c.STEP_FIELD_MAX:] or None)
            job.finish("done" if r.ok else "failed")
            if not r.ok:
                raise _c.LaneFailed(f"{action}: {r.note}\n{r.detail}")
            return r

        done = ctx.done_calls
        attempt = f"{step.id}|{(ac or loop_item or {}).get('id', '')}"
        if self._reviews(step):
            # 0.18.17 the finished calls a "try again" keeps belong to one HEAD: after a fix commit they are stale
            head = ((await asyncio.to_thread(git.head, ctx.root)) or "")[:12]
            for old in [k for k in done if k.startswith(attempt + "|") and not k.startswith(f"{attempt}|{head}|")]:
                del done[old]
            attempt += f"|{head}"
        # `batch: N`: at most N agents of this step run at the same time; the step is still one node.
        # "$data.x" reads the number from the state when the step runs.
        batch = self._seed_value(step.batch, state, None) if isinstance(step.batch, str) else step.batch
        batch = int(batch) if isinstance(batch, (int, float)) or str(batch or "").isdigit() else None
        gate = asyncio.Semaphore(batch) if batch and batch > 0 else None

        async def one(n: int, a: str, k: int):
            key = f"{attempt}|{a}|{k}"
            if key in done:
                return done[key]
            if gate:
                async with gate:
                    r = await self._run_agent(state, step, a, k, section_of.get(n), item_of.get(n))
            else:
                r = await self._run_agent(state, step, a, k, section_of.get(n), item_of.get(n))
            if len(calls) > 1:
                done[key] = r
            return r

        while True:
            try:
                outs = await asyncio.gather(*[one(n, a, k) for n, (a, k) in enumerate(calls)], *[lane(c) for c in code_lanes], return_exceptions=True)
                for o in outs:
                    if isinstance(o, (asyncio.CancelledError, GraphBubbleUp)):
                        raise o
                failed = [o for o in outs if isinstance(o, BaseException)]
                if failed:
                    raise failed[0]
                out = list(outs)
                break
            except (asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as exc:
                _c.log.warning("step %s failed: %s", step.id, exc, exc_info=True)
                if len(calls) > 1:
                    kept = sum(1 for key in done if key.startswith(attempt + "|"))
                    if kept:
                        exc.args = (f"{exc} ({kept} of {len(calls)} agents finished; try again runs only the others)",)
                answer, extra = self._ask(state, {"step": step.id, "kind": "fix", "title": f"{step.name} failed",
                                                  "detail": f"{exc}{(' ' + exc.hint) if getattr(exc, 'hint', '') else ''}\n\nApprove to try again, reject to stop the flow.",
                                                  "options": _c.OPTIONS})
                upd.update(extra)
                state = {**state, **extra}
                if (answer or {}).get("decision") != "approve":
                    return {**upd, **self._booked(state), "status": "failed", "error": str(exc)[:500],
                            "note": f"{step.name} failed"}, END
                # Try again = run this step again from the top (one interrupt per node run, like _answer_check): looping
                # here would replay every earlier approval on resume, so approval k would cost k+1 runs.
                return {**upd, "note": f"{step.name}: trying again"}, step.id

        for key in [key for key in done if key.startswith(attempt + "|")]:
            del done[key]
        agent_results = [o for o in out if isinstance(o, tuple)]
        lane_notes = [o.note for o in out if isinstance(o, ActionResult)]
        for o in out:
            if isinstance(o, ActionResult):
                upd.update(o.update)

        refused = await asyncio.to_thread(guard.guard_diff, ctx.root, state["phase"], before, None,
                                          rules.ac_lane(ac) if ac else None, state.get("unlocks") or [], self._readonly())
        for r in refused:
            ctx.emit("guard.refused", step=step.id, data={"tool": "diff-guard", "phase": state["phase"], **r})
        tools_note = ""
        if agent_results:
            tools_note, lint_data = await self._after_edit(step, state, before, refused)
            if lint_data is not None:
                state = {**state, "data": lint_data}
                upd["data"] = lint_data

        usage = _c.add_usage(dict(state.get("usage") or {}), ctx.unbooked)
        ctx.unbooked.clear()
        step_tokens = dict(state.get("step_tokens") or {})
        notes = []
        for res, model, _tb in agent_results:
            usage["tokens_in"] = usage.get("tokens_in", 0) + res.tokens_in
            usage["tokens_out"] = usage.get("tokens_out", 0) + res.tokens_out
            usage["tokens_cached"] = usage.get("tokens_cached", 0) + res.tokens_cached
            usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + res.cost_usd, 6)
            usage["premium_requests"] = usage.get("premium_requests", 0) + res.premium_requests
            step_tokens[step.id] = step_tokens.get(step.id, 0) + res.tokens_in + res.tokens_out + res.tokens_cached // 10
            notes.append(res.text.strip().splitlines()[0][:160] if res.text.strip() else "")
        upd.update(usage=usage, step_tokens=step_tokens, feedback=None)
        label = step.agent or "lanes"
        if agent_results:
            m = agent_results[0][1]
            label = f"{step.agent or calls[0][0]} · {m['provider']} {m.get('model', '')}"
        upd["note"] = " · ".join(x for x in [label, *notes[:2], *lane_notes] if x)
        if refused:
            upd["note"] += f" · guard put back {len(refused)} file(s)"
        # every file keel kept from changing in this step: put back by the diff guard, refused by the ToolBox, or by the
        # PreToolUse hook of a CLI agent
        kept = [r["path"] for r in refused] + [p for _res, _m, tb in agent_results for p in getattr(tb, "refused_paths", [])] \
            + [str(r.get("path")) for res, _m, _tb in agent_results for r in res.data.get("refusals") or []
               if r.get("path") and not READ_TOOLS.match(str(r.get("tool") or ""))]
        streak = self._guard_streak(step, state, ac, kept, upd)
        if streak is not None:
            upd["data"] = streak
        # 0.18.71 what keel put back after this step, whatever the file (a review's question names a spec file a fix
        # round may not change: data.put_back[<step>])
        pb = dict((upd.get("data") or state.get("data") or {}).get("put_back") or {})
        if kept or step.id in pb:
            root_ = str(ctx.root).rstrip("/") + "/"
            pb[step.id] = sorted({rules._rel(p, ctx.root).removeprefix(root_) for p in kept})
            upd["data"] = {**(upd.get("data") or state.get("data") or {}), "put_back": pb}
        if tools_note:
            upd["note"] += f" · {tools_note}"
        if sections and state.get("acs"):
            upd["acs"] = [dict(a, status="done") if a["id"] in sections else dict(a) for a in state["acs"]]

        for res, _m, tb in agent_results:
            # A CLI agent stopped by keel's PreToolUse hook: the runner reported it as a guard step.
            for r in res.data.get("refusals") or []:
                data = {"tool": r.get("tool") or "hook", "phase": state["phase"], "path": r.get("path") or "",
                        "reason": r.get("reason", ""), "source": "keel-hook"}
                if r.get("command"):
                    data["command"] = r["command"]
                ctx.emit("guard.refused", step=step.id, data=data)
        if agent_results:
            agent_results = await self._marker_reask(state, step, agent_results, calls, section_of, item_of)
            upd["last_answer"] = (agent_results[-1][0].text or "")[:8000]
            # keel v1 post-failure: the last shell command an agent saw fail (shown by Explain and "Rules now")
            fails = [(res.data.get("failed_command") or getattr(tb, "failed_command", None)) for res, _m, tb in agent_results]
            fail = next((f for f in reversed(fails) if f), None)
            if fail:
                data = dict(upd.get("data") or state.get("data") or {})
                data["last_command_failure"] = {**fail, "step": step.id, "agent": step.agent or calls[0][0]}
                upd["data"] = data
                state = {**state, "data": data}
        if step.session == "thread" and agent_results and ctx.memory:
            # 0.19.0 harness mode: the session this flow keeps, and how a person opens it in their own terminal
            got = await ctx.memory.get(memory_mod.THREAD_KEY) or {}
            m = agent_results[-1][1]
            if got.get("session"):
                data = dict(upd.get("data") or state.get("data") or {})
                data["harness"] = {"provider": m["provider"], "model": m.get("model"), "session": got["session"],
                                   "open": memory_mod.open_command(m["provider"], got["session"], m.get("mode"),
                                                                   cli_runners.claude_home())}
                upd["data"] = data
                state = {**state, "data": data}
        upd.update(self._outputs(step, state, [r[0] for r in agent_results], item_of))
        said = ((upd.get("markers") or {}).get(step.id) or {}) if step.markers else {}
        if said:
            upd["note"] += " · " + ", ".join(f"{k}: {v}" for k, v in said.items())
        # 0.18.21 only the spec writer's answer holds criteria (a `keep` step's answer is text for the next steps)
        if state["phase"] in ("spec", "triage") and not state.get("acs") and prompts.spec_writer(step):
            acs, spec = await asyncio.to_thread(self._acs_from, agent_results, started, state.get("spec"))
            spec = await asyncio.to_thread(self._renumbered, state, spec)
            if not acs and ctx.request:
                # 0.18.17 the agent wrote its criteria in another form ("1. ..."), but the request lists them as AC
                # lines: those are the person's own criteria, not "no acceptance criteria yet"
                acs = prompts.parse_acs(ctx.request)
            if acs:
                # keel's spec check: something clear is missing (a screen with no mockup, an API with no request path)
                # → send the spec back to the explorer ONCE, in the same session. Otherwise the gate shows the warnings.
                # 0.18.64 with the project's root: its citations are checked against the files (git ls-files, reads); and
                # what a handed-over spec named and this one lost (a risk, a criterion, an out-of-scope item)
                text = await asyncio.to_thread(spec_check.read_spec, ctx.root, spec or state.get("spec"))
                found = await asyncio.to_thread(spec_check.check, text, acs,
                                                spec_check.outside_facts(ctx.request, state.get("data")), ctx.root) \
                    + spec_check.lost(text, ctx.request, state.get("data"))
                if any(f["level"] == "fix" for f in found) and not state.get("spec_revisions") \
                        and ctx.settings.get("spec_check", not ctx.fake):
                    upd.update(spec_revisions=1, feedback=spec_check.revision_note(found), clarify={},
                               note=upd["note"] + " · spec check: sent back once")
                    if spec:
                        upd["spec"] = spec
                    return upd, self.nav.jump(step.id, i)
                web = [a["id"] for a in acs if str(a.get("layer") or "").upper() == "WEB"]
                if web and not stacks.has_web_layer(ctx.root):
                    # 0.18.12 no web layer: the pages are tested by backend tests in the api lane; spec_sync fixes the
                    # spec's lines and the spec gate names these criteria (data.web_to_api)
                    acs = [dict(a, layer="API") for a in acs]
                    upd["data"] = {**(upd.get("data") or state.get("data") or {}), "web_to_api": web}
                upd["acs"] = acs
                upd["clarify"] = {}
                # the plan's own words (a small flow keeps its criteria inline, with no spec file): the guard-loop gate
                # and the frozen-file heads-up look for the files it names
                plan = "\n".join((res.text or "") for res, _m, _tb in agent_results)[-8000:]
                upd["data"] = {**(upd.get("data") or state.get("data") or {}), "plan_text": plan}
            elif state.get("clarify_rounds", 0) < clarify.MAX_ROUNDS:
                # No criteria, but questions: the spec gate shows them as buttons (clarify loop).
                asked = [q for res, _m, _tb in agent_results for q in clarify.parse_questions(res.text)]
                upd["clarify"] = {"questions": asked[:clarify.MAX_QUESTIONS]} if asked else {}
            if spec:
                upd["spec"] = spec
        elif state["phase"] == "spec" and state.get("acs") and self._spec_step(step) \
                and spec_check.seeded(ctx.request, state.get("data")):
            # 0.18.64 criteria handed over (clarify, change): the spec written around them must still hold what the
            # handed-over spec named; a lost risk sends it back once, like the spec check above
            _acs, spec = await asyncio.to_thread(self._acs_from, agent_results, started, state.get("spec"))
            spec = await asyncio.to_thread(self._renumbered, state, spec)
            text = await asyncio.to_thread(spec_check.read_spec, ctx.root, spec)
            gone = spec_check.lost(text, ctx.request, state.get("data"))
            if spec:
                upd["spec"] = spec
            if gone and not state.get("spec_revisions") and ctx.settings.get("spec_check", not ctx.fake):
                upd.update(spec_revisions=1, feedback=spec_check.revision_note(gone),
                           note=upd["note"] + " · spec check: sent back once (lost what was handed over)")
                return upd, self.nav.jump(step.id, i)
        if step.asks:
            # keel's clarify loop for any agent step: its keel-questions go to the next gate as buttons, the answers back
            # to this agent (at most clarify.MAX_ROUNDS rounds; then it must decide with the recommended options).
            # `rounds` on the step: a longer interview (the clarify flow's rounds), else clarify.MAX_ROUNDS
            rounds, cap = int(state.get("clarify_rounds") or 0), step.rounds or clarify.MAX_ROUNDS
            # 0.18.55 also the plain Markdown forms a real model writes instead of the fenced block ("# keel-questions"
            # and a numbered list): else the questions were saved as the step's document
            asked = [q for res, _m, _tb in agent_results
                     for q in clarify.parse_questions(res.text, clarify.STEP_QUESTIONS, plain=True)] if rounds < cap else []
            upd["clarify"] = {"questions": asked[:clarify.STEP_QUESTIONS], "step": step.id,
                              **({"rounds": cap} if step.rounds else {})} if asked else {}
        if self._reviews(step):
            def lens(n: int) -> str:
                if step.items_from and n in item_of:
                    return f"{step.name}: {item_of[n].get('title') or item_of[n]['id']}"
                return f"{step.name} #{n + 1}" if len(agent_results) > 1 else step.name
            found = unique([{"lens": lens(n), "text": t}
                            for n, (res, _m, _tb) in enumerate(agent_results) for t in blocking(res.text)])
            upd["findings"] = found
            nb = [{"step": step.id, "name": step.name, "lens": lens(n), "text": t}
                  for n, (res, _m, _tb) in enumerate(agent_results) for t in nonblocking(res.text)]
            if nb:
                # 0.18.22 non-blocking findings do not stop the flow, but the final review lists them
                data = dict(upd.get("data") or state.get("data") or {})
                kept = list(data.get("nonblocking_findings") or [])
                seen = {(x.get("step"), finding_key(x.get("text") or "")) for x in kept}
                data["nonblocking_findings"] = kept + [x for x in nb if (x["step"], finding_key(x["text"])) not in seen]
                upd["data"] = data
            sec = [((item_of.get(n) or {"agent": step.agent}), res.text or "") for n, (res, _m, _tb) in enumerate(agent_results)
                   if ((item_of.get(n) or {}).get("agent") or step.agent) in ("security-auditor", "dependency-triager")]
            if sec:
                # 0.18.22 the security phase writes its verdicts (the push gate reads them); the triage may clear deps
                got, said = await asyncio.to_thread(security_actions.after_review, ctx.project_id or ctx.root, ctx.root, sec)
                if got:
                    upd["data"] = {**(upd.get("data") or state.get("data") or {}), **got}
                if said:
                    upd["note"] += f" · {said}"
            if found:
                upd["note"] += f" · {len(found)} blocking finding(s)"
                key = self._rounds_key(step, loop_item)
                done_rounds = int((state.get("review_rounds") or {}).get(key, 0))
                # 0.18.71 a field report: the same findings came back round after round (the fix was put back by the
                # guard, or the fix round committed nothing), and the review reached four rounds. Each round remembers
                # its findings and the commit it read; the same findings again, or no new commit, go to the person now.
                data = dict(upd.get("data") or state.get("data") or {})
                trail = dict(data.get("review_trail") or {})
                last = trail.get(key) or {}
                head = git.head(ctx.root) if git.is_repo(ctx.root) else None
                texts = [f["text"] for f in found]
                again = bool(last.get("texts")) and all(any(same_finding(t, o) for o in last["texts"]) for t in texts)
                stuck_round = done_rounds >= 1 and (again or (head is not None and head == last.get("head")))
                trail[key] = {"texts": texts[:30], "head": head, "same": again,
                              "no_change": head is not None and head == last.get("head")}
                data["review_trail"] = trail
                upd["data"] = data
                if stuck_round:
                    upd["note"] += " · " + ("the same findings as the last round" if again else "the fix round changed nothing")
                if step.back and done_rounds < (step.rounds or 2) and not stuck_round:
                    # Findings go straight back to the step that wrote the work (cover: the test author), no question,
                    # until the rounds are used up; then the fix node asks.
                    rounds = {**(state.get("review_rounds") or {}), key: done_rounds + 1}
                    listed = "\n".join(f"- **[{f['lens']}]** {f['text']}" for f in found)
                    upd.update(review_rounds=rounds, findings=[],
                               feedback=f"Fix these blocking findings from {step.name} (round {done_rounds + 1}):\n{listed}")
                    upd["note"] += f" · sent back to {step.back} (round {done_rounds + 1})"
                    return upd, self.nav.jump(step.back, i)
                return upd, _c.fix_id(step.id)
        return upd, self.nav.after(i)

    def _spec_step(self, step: Step) -> bool:
        """0.18.64 the step that writes the flow's spec: the workflow's first spec writer in a spec or triage phase
        (feature's spec, change's triage). The plan under the criteria and a gate's edit change a spec that exists."""
        first = next((s for s in self.wf.steps if s.phase in ("spec", "triage") and prompts.spec_writer(s)), None)
        return first is not None and first.id == step.id

    def _renumbered(self, state: FlowState, spec: str | None) -> str | None:
        """0.18.64 the spec this flow wrote first: a number another spec has already (in any spec folder, keel v1's
        too) moves to the next free one. A spec the flow knew before, or started from, or one committed already keeps
        its name."""
        if not spec or state.get("spec") or spec == (state.get("data") or {}).get("spec"):
            return spec
        if git.is_repo(self.ctx.root) and git.tracked_in_head(self.ctx.root, spec):
            return spec
        return prompts.renumber_spec(self.ctx.root, spec)

    def _addon_event(self, step_id: str):
        """An add-on action's own events: kind "<addon>.<what>" only (keel's own kinds stay keel's)."""
        def event(kind: str, data: dict | None = None):
            if not addons.may_emit(kind):
                raise ValueError(f"an add-on may emit only its own events ('<name>.<what>'), not {kind!r}")
            self.ctx.emit(kind, step=step_id, data=dict(data or {}))
        return event

    def _tool_emit(self, step_id: str):
        """Each tool run as an event `tool.ran` with its full output (the dashboard); the agent only gets one line."""
        def emit(r: dict):
            self.ctx.emit("tool.ran", step=step_id, data={
                "tool": r["name"], "on": r["on"], "fail": r["fail"], "ok": r["ok"], "available": r["available"],
                "ms": r["ms"], "files": r["files"], "cmd": r["cmd"], "dir": r["dir"], "code": r["code"],
                "source": r.get("source"), "output": r.get("output", "")[:_c.STEP_FIELD_MAX]})
        return emit

    async def _marker_reask(self, state: FlowState, step: Step, results: list, calls: list, section_of: dict,
                            item_of: dict) -> list:
        """keel v1 subagent-stop: an agent whose answer has none of the result lines the step reads (BLOCKING: yes | no,
        ...) is asked once more, in its own session where the provider keeps one, to repeat its whole answer and end with
        the line. A reply with only the line would lose the findings, so the prompt says so. Still missing: as before.
        Like keel v1's one contract per agent, one of the step's lines is enough."""
        wanted = [m.upper() for m in step.markers or [] if markers.REGISTRY.get(m.upper())]
        if not wanted or len(results) != len(calls):
            return results
        out = list(results)
        for n, (res, _model, _tb) in enumerate(results):
            if markers.parse(res.text or "", wanted):
                continue
            missing = wanted
            agent, k = calls[n]
            lines = "\n".join(f"{m}: {' | '.join(markers.REGISTRY[m])}" for m in missing)
            ask = (f"Your answer did not end with keel's result line. Repeat your full answer (this reply replaces it, so "
                   f"anything you leave out is lost) and end with:\n{lines}")
            self.ctx.emit("agent.reask", step=step.id, data={"agent": agent, "missing": missing})
            try:
                again = await self._run_agent({**state, "feedback": ask}, step, agent, k, section_of.get(n), item_of.get(n))
            except Exception as exc:  # noqa: BLE001  the first answer stays
                _c.log.warning("re-ask of %s failed: %s", agent, exc)
                continue
            if markers.parse(again[0].text or "", missing) or not (res.text or "").strip():
                out[n] = again
        return out

    async def _after_edit(self, step: Step, state: FlowState, before, refused: list[dict]) -> tuple[str, dict | None]:
        """After an agent step that changed files: the project's `edit` and `batch` tools on those files (runtime/tools.py).
        A formatter's changes stay; block and warn failures go to data.lint_notes, which the next agent's prompt and
        the next gate show. Returns (a note for the step, the new data or None when nothing ran)."""
        ctx = self.ctx
        if before is None or rules.read_only(state.get("phase")) or self._readonly():
            return "", None
        gone = {r.get("path") for r in refused}
        changed = await asyncio.to_thread(guard.changed_since, ctx.root, before)
        files = [f for f in changed if f not in gone]
        if not files:
            return "", None
        res = await asyncio.to_thread(tool_runner.after_edit, ctx.root, files, include_stacks=not ctx.simulate_checks,
                                      emit=self._tool_emit(step.id))
        if not res["ran"]:
            return "", None
        data = {**(state.get("data") or {}), "lint_notes": res["notes"]}
        parts = [f"tools: {len(res['ran'])} ran"]
        if res["changed"]:
            parts.append(f"{len(res['changed'])} file(s) formatted")
        if res["notes"]:
            parts.append(f"{len(res['notes'])} finding(s): " + "; ".join(n.split(':')[0] for n in res["notes"]))
        return ", ".join(parts), data

    def _item_agent(self, step: Step, item: dict) -> str:
        """The agent for one item of a fan-out: the item's own `agent` when the list was made by the flow's code
        (review_scope: lens-reviewer, branch-reviewer or criterion-reviewer), never when an agent's answer made the list."""
        own = item.get("agent")
        if own and not any(s.collect == step.items_from for s in self.wf.steps):
            return agent_names.canonical(str(own)) or ""
        return step.agent or ""

    @staticmethod
    def _rounds_key(step: Step, item: dict | None) -> str:
        """Fix rounds count per review step, and per item inside a for_each loop."""
        return f"{step.id}:{item['id']}" if item and step.per_item else step.id

    def _guard_streak(self, step: Step, state: FlowState, ac: dict | None, refused: list[str], upd: dict) -> dict | None:
        """data.guard_streak[<step>:<ac>] = {paths, count}: the files keel's phase rule put back after this step, and in
        how many rounds in a row the same ones came back (a file only an unlock would let pass; a secret does not count).
        The check after the step reads it (CodeMixin._guard_loop): two rounds in a row ask the person. None: no change."""
        data = dict(upd.get("data") or state.get("data") or {})
        streaks = dict(data.get("guard_streak") or {})
        key = f"{step.id}:{(ac or {}).get('id')}"
        phase = state.get("phase") or "none"
        cfg = rules.load_config(self.ctx.root) if refused else None
        root = self.ctx.root
        rels = {rules._rel(p, root).removeprefix(str(root).rstrip("/") + "/") for p in refused}
        paths = sorted(p for p in rels if test_scope.unlockable(phase, p, cfg)) if refused else []
        prev = streaks.get(key) or {}
        if not paths:
            if key not in streaks:
                return None
            streaks.pop(key)
        else:
            same = sorted(set(paths) & set(prev.get("paths") or []))
            streaks[key] = {"paths": same, "count": int(prev.get("count") or 0) + 1} if same else {"paths": paths, "count": 1}
        data["guard_streak"] = streaks
        return data

    def _outputs(self, step: Step, state: FlowState, results: list[AgentResult], item_of: dict[int, dict]) -> dict:
        """What a step's answers put into the state: per-item results (fan-out), markers, a collected list."""
        upd: dict = {}
        data = dict(state.get("data") or {})
        found = [markers.parse(r.text, step.markers) if step.markers else {} for r in results]
        if "BLOCKED" in [m.upper() for m in step.markers or []]:
            for f, r in zip(found, results):
                why = markers.blocked(r.text or "")
                if why:
                    f["BLOCKED"] = why
        if step.items_from:
            # usage per item (keel 0.18.57): review-pr compares the reviewers' tokens with the size of the diff
            data[f"{step.id}_results"] = [{"item": item_of[n]["id"], "title": item_of[n].get("title") or item_of[n]["id"],
                                           "text": (r.text or "")[:16000], "markers": found[n],
                                           "usage": {"tokens_in": r.tokens_in, "tokens_cached": r.tokens_cached,
                                                     "tokens_out": r.tokens_out}} for n, r in enumerate(results)]
        elif step.keep and results:
            data[f"{step.id}_text"] = (results[-1].text or "")[:_c.KEEP_MAX]
        if step.markers:
            mine: dict = {}
            for f in found:
                mine.update(f)
            mk = dict(state.get("markers") or {})
            mk[step.id] = mine
            mk["*"] = {**(mk.get("*") or {}), **mine}
            upd["markers"] = mk
        if step.collect:
            got: list[dict] = []
            for n, r in enumerate(results):
                # In a fan-out, each collected entry says which item's agent gave it (from_item).
                src = (item_of.get(n) or {}).get("id") if step.items_from else None
                got += [dict(x, from_item=src) if src else x for x in markers.collect(r.text, step.collect)]
            ids = [x["id"] for x in got]
            if len(set(ids)) != len(ids):
                got = [dict(x, id=f"{step.collect}-{n + 1}") for n, x in enumerate(got)]
            data[step.collect] = got
        if data != (state.get("data") or {}):
            upd["data"] = data
        return upd

    def _reviews(self, step: Step) -> bool:
        """A review step whose blocking findings must be fixed or accepted before the flow goes on."""
        if rules.read_only(step.phase):
            return False      # a read-only flow (review) reports its findings; nothing is fixed
        if self._branch_decides(step):
            return False      # 0.18.28 a critic in a loop: its branch reads its marker and loops, no findings gate
        return step.kind in ("agent", "parallel") and not step.per_ac and not step.lanes and (step.agent or "") in REVIEWERS

    def _branch_decides(self, step: Step) -> bool:
        """0.18.28 Does a later branch read a marker this step declares (when.step names it, or names no step)? Then the
        step is a critic in a loop (a spike's "GAPS: 9": the branch loops back while rounds remain). A reviewer agent
        there used to stop at its findings gate ("critic: 9 blocking findings") instead of looping."""
        names = {m.upper() for m in step.markers or []}
        if not names or step.id not in self.nav.index:
            return False
        for s in self.wf.steps[self.nav.index[step.id] + 1:]:
            if s.kind != "branch" or not s.when or not s.when.get("marker") or not s.no:
                continue
            src = s.when.get("step")
            srcs = src if isinstance(src, list) else ([src] if src else [])
            if str(s.when["marker"]).upper() in names and (not srcs or step.id in srcs):
                return True
        return False
