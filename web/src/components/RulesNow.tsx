// v0.17.2 "Rules now" on the Flow page: in short sentences, what agents may and may not change in this phase, what you
// allowed, your own rules, which rules stopped something, and that keel's harness checks every action before it runs.

import { go, useApp, useLoad } from "../state";
import { rulesApi, type RulesNow as Now } from "../rulesApi";
import type { ThreadState } from "../api";
import { FoldPanel } from "./FoldPanel";
import { Async } from "./ui";

const PROTECTION: Record<string, [string, string]> = {
  before: ["✓", "keel's harness checks every action before it runs"],
};

export function RulesNowPanel({ thread }: { thread: ThreadState }) {
  const { recent } = useApp();
  const fired = recent.filter((e) => e.thread_id === thread.thread_id && e.type === "guard.refused").length;
  const now = useLoad(`rules-now:${thread.thread_id}:${thread.phase}:${thread.status}:${fired}`, () => rulesApi.now(thread.thread_id), { live: false });
  return (
    <FoldPanel id="flow.rules" title="Rules now"
      extra={<button className="btn sm ghost" type="button" onClick={() => go("rules")}>All rules ›</button>}
      big={() => now.data ? <RulesNowBody n={now.data} /> : null}>
      <Async r={now} what="Loading the rules">{(n) => <RulesNowBody n={n} />}</Async>
    </FoldPanel>
  );
}

export function RulesNowBody({ n }: { n: Now }) {
  const prot = n.engine?.protection ? PROTECTION[n.engine.protection] : null;
  return (
    <div className="rn grid" data-testid="rules-now">
      <p className="rn-phase"><b>Phase {n.phase}.</b> {n.meaning}{n.lane ? ` This is the ${n.lane} lane.` : ""}</p>
      {prot && (
        <p className={`rn-engine rn-${n.engine.protection}`}>
          <span className="rn-ico" aria-hidden="true">{prot[0]}</span> <b>{n.engine.provider}</b>: {prot[1]}.
        </p>
      )}
      {n.may.length > 0 && (
        <div className="rn-row"><span className="rn-lab">Agents may change</span>
          <div className="rn-chips">{n.may.map((b) => <span key={b.bucket} className="rn-chip rn-ok" title={b.words}>✓ {b.what}{b.rule && b.rule !== "allow" ? ` (${b.words})` : ""}</span>)}</div></div>
      )}
      {n.may_not.length > 0 && (
        <div className="rn-row"><span className="rn-lab">Agents may not change</span>
          <div className="rn-chips">{n.may_not.map((b) => <span key={b.bucket} className="rn-chip rn-no">✗ {b.what}</span>)}</div></div>
      )}
      <div className="rn-row"><span className="rn-lab">Never</span>
        <div className="rn-chips">{n.always.map((b) => <span key={b.bucket} className="rn-chip rn-lock" title={b.words}>🔒 {b.what}</span>)}</div></div>
      {n.unlocks.length > 0 && (
        <div className="rn-row"><span className="rn-lab">You allowed</span>
          <ul className="rn-list">{n.unlocks.map((u) => <li key={u.path}><span className="mono">{u.path}</span>{u.reason ? <span className="sub"> · {u.reason}</span> : null}</li>)}</ul></div>
      )}
      {n.packs.length > 0 && (
        <div className="rn-row"><span className="rn-lab">Your rules</span>
          <ul className="rn-list">{n.packs.map((p) => <li key={`${p.pack}/${p.rule}`}><b>{p.name}</b>: {p.reason}</li>)}</ul></div>
      )}
      <div className="rn-row"><span className="rn-lab">Stopped so far</span>
        {n.fired && n.fired.length > 0 ? (
          <ul className="rn-list rn-fired">{n.fired.map((f) => (
            <li key={f.reason}><b>{f.count}×</b> {f.reason}
              <span className="sub"> · {f.source}{f.examples.length ? ` · ${f.examples.join(", ")}` : ""}</span></li>
          ))}</ul>
        ) : <span className="sub">Nothing yet in this flow.</span>}
      </div>
      {n.last_command_failure && (
        <div className="rn-row"><span className="rn-lab">Last failed command</span>
          <span><span className="mono">{n.last_command_failure.command}</span><span className="sub"> · {n.last_command_failure.output}</span></span></div>
      )}
      <p className="rn-legend sub">✓ may change · ✗ may not · 🔒 never · ◐ only some actions checked before · ↺ checked after the step</p>
    </div>
  );
}
