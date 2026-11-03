// Settings › Slack: what goes where, the tokens (written once, never read back), the channel, who may act, and what happened. It imports only react and
// @keel/web-sdk.

import { useState } from "react";
import { Async, del, errorParts, get, Note, Panel, Pill, post, SwitchRow, useApp, useLoad, type PillTone } from "@keel/web-sdk";

export type SlackPerson = { id: string; name: string };
export type SlackView = {
  linked: boolean; state: string; problem: string | null; bot_hint: string | null; app_hint: string | null;
  config: { allow_chat: boolean; chat_day_usd: number; chat_day_tokens: number; allow_start: boolean; allow_all_gates: boolean; strict_allowed: SlackPerson[];
    on: boolean; channel: string; allowed: SlackPerson[]; send_detail: boolean; project: string | null; team_name: string | null };
  log: { at: string; who: string; what: string; ok: boolean }[];
  sends_slack: string; sends_model: string;
};

const PATH = "/plugins/slack";
export const slackApi = {
  view: () => get<SlackView>(PATH),
  save: (b: Record<string, unknown>) => post<SlackView>(PATH, b),
  forget: () => del<SlackView>(PATH),
};

const TONE: Record<string, PillTone> = { connected: "ok", connecting: "run", failed: "bad", off: "idle" };
const when = (iso: string) => iso.replace("T", " ").replace(/\.\d+Z?$|Z$/, "");

/** "U012AB Ann" per line, to the list the api takes. */
export const parsePeople = (text: string): SlackPerson[] =>
  text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const [id, ...name] = l.split(/\s+/);
    return { id, name: name.join(" ") };
  });

export function SlackConnections() {
  return <p className="ss-sub">Asking, answering and making tasks from Slack are in <a href="#/settings/slack">Slack</a>.</p>;
}

function Form({ v, onSaved }: { v: SlackView; onSaved: (x: SlackView) => void }) {
  const { toast } = useApp();
  const c = v.config;
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const [channel, setChannel] = useState(c.channel);
  const [people, setPeople] = useState(c.allowed.map((p) => `${p.id} ${p.name}`).join("\n"));
  const [project, setProject] = useState(c.project ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      onSaved(await slackApi.save({ bot_token: bot, app_token: app, channel, allowed: parsePeople(people), project }));
      setBot("");
      setApp("");
      toast("Saved.");
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={(e) => void save(e)} aria-label="Link Slack" className="grid" style={{ gap: 8 }}>
      <div className="field"><label htmlFor="sl-bot">Bot token</label> <input id="sl-bot" type="password" autoComplete="off" value={bot} onChange={(e) => setBot(e.target.value)} placeholder={v.bot_hint ? `kept, ends in ${v.bot_hint}` : "xoxb-…"} /></div>
      <div className="field"><label htmlFor="sl-app">App-level token</label> <input id="sl-app" type="password" autoComplete="off" value={app} onChange={(e) => setApp(e.target.value)} placeholder={v.app_hint ? `kept, ends in ${v.app_hint}` : "xapp-…"} />
        <span className="hint">Both stay encrypted in keel and are never shown again. keel asks Slack first, and keeps a token only if Slack takes it.</span></div>
      <div className="field"><label htmlFor="sl-channel">Channel id for gates</label> <input id="sl-channel" value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="C0123ABCD" />
        <span className="hint">Invite the app to this channel. Everyone in it can read the questions.</span></div>
      <div className="field"><label htmlFor="sl-people">Who may answer and make tasks</label> <textarea id="sl-people" rows={3} value={people} onChange={(e) => setPeople(e.target.value)} placeholder={"U012ABCDEF Ann\nU034GHIJKL Ben"} />
        <span className="hint">One person per line: the Slack member id (profile › More › Copy member ID), then the name keel records. Everyone else is refused, and so is every bot.</span></div>
      <div className="field"><label htmlFor="sl-project">Project for new tasks (optional)</label> <input id="sl-project" value={project} onChange={(e) => setProject(e.target.value)} placeholder="Its id. Without it, say: task in <project>: <title>" /></div>
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div><button className="btn primary" type="submit" disabled={busy}>{busy ? "Checking…" : "Save"}</button></div>
    </form>
  );
}

/** The powers, each off until the person turns it on, each saying what it costs or opens. */
function Powers({ v, onSaved }: { v: SlackView; onSaved: (x: SlackView) => void }) {
  const c = v.config;
  const [usd, setUsd] = useState(String(c.chat_day_usd));
  const [tokens, setTokens] = useState(String(c.chat_day_tokens));
  const [strict, setStrict] = useState(c.strict_allowed.map((p) => `${p.id} ${p.name}`).join("\n"));
  const [problem, setProblem] = useState<string | null>(null);
  const save = async (b: Record<string, unknown>) => {
    setProblem(null);
    try {
      onSaved(await slackApi.save(b));
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };
  return (
    <div className="grid" style={{ gap: 8 }}>
      <SwitchRow on={c.allow_chat} onChange={(on: boolean) => void save({ allow_chat: on })} title="Let people ask keel in Slack"
        help="A message that is no command goes to your model company: the message, the thread's earlier messages, keel's status lines and the files it reads. It can only read. It cannot write, commit, start a flow or answer a gate; it says which command does." />
      {c.allow_chat && (
        <>
          <div className="field"><label htmlFor="sl-usd">Daily cap per person (USD)</label> <input id="sl-usd" type="number" min="0.01" step="0.01" value={usd} onChange={(e) => setUsd(e.target.value)}
            onBlur={() => { if (Number(usd) !== c.chat_day_usd) void save({ chat_day_usd: Number(usd) }); }} /></div>
          <div className="field"><label htmlFor="sl-tokens">Daily cap per person (tokens)</label> <input id="sl-tokens" type="number" min="1" value={tokens} onChange={(e) => setTokens(e.target.value)}
            onBlur={() => { if (Number(tokens) !== c.chat_day_tokens) void save({ chat_day_tokens: Number(tokens) }); }} />
            <span className="hint">One question at a time per person. A model on a plan reports no cost, so the tokens count too. It counts on the Budget page.</span></div>
        </>
      )}
      <SwitchRow on={c.allow_start} onChange={(on: boolean) => void save({ allow_start: on })} title="Let people start flows from Slack"
        help="`start <flow> <what to do>` asks with one button, for the person who asked. It is the dashboard's start: the budget and caps count, and what they wrote goes to the flow's agents." />
      <SwitchRow on={c.allow_all_gates} onChange={(on: boolean) => void save({ allow_all_gates: on })} title="Allow every gate from Slack"
        help="Gates only a person answers and the Supervisor's never list get buttons, for the people below, with a reason and one confirm. Budget and secret gates stay in keel whatever you choose. The Supervisor's own limits do not change." />
      {c.allow_all_gates && (
        <>
          <Note tone="warn">Slack is not keel's sign-in. Someone who gets into one of these Slack accounts can approve a new dependency, the final review or the pull request.</Note>
          <div className="field"><label htmlFor="sl-strict">Who may answer these</label> <textarea id="sl-strict" rows={2} value={strict} onChange={(e) => setStrict(e.target.value)}
            onBlur={() => void save({ strict_allowed: parsePeople(strict) })} />
            <span className="hint">One per line, as above. Each must also be on the first list. Keep it short.</span></div>
        </>
      )}
      {problem && <Note tone="err" role="alert">{problem}</Note>}
    </div>
  );
}

export function SlackTab() {
  const view = useLoad("slack", () => slackApi.view());
  return (
    <Async r={view} what="Loading Slack">
      {(v) => (
        <div className="grid" style={{ gap: 12 }}>
          <Panel title="What goes where">
            <p>{v.sends_slack}</p>
            <p>{v.sends_model}</p>
          </Panel>
          <Panel title="The link" extra={<Pill tone={TONE[v.state] ?? "idle"}>{v.state}{v.config.team_name ? ` · ${v.config.team_name}` : ""}</Pill>}>
            {v.problem && <Note tone="err">{v.problem}</Note>}
            <Form key={`${v.bot_hint}-${v.app_hint}`} v={v} onSaved={(x) => view.setData(x)} />
          </Panel>
          <Panel title="Choices">
            <div className="grid" style={{ gap: 8 }}>
              <SwitchRow on={v.config.on} onChange={(on: boolean) => void slackApi.save({ on }).then((x) => view.setData(x))} title="On"
                help="Off: keel closes the connection, reads nothing and sends nothing." />
              <SwitchRow on={v.config.send_detail} onChange={(on: boolean) => void slackApi.save({ send_detail: on }).then((x) => view.setData(x))} title="Also send a gate's whole text"
                help="Off: only its name and short lines. On: its text too, which can hold code and goes to Slack's servers." />
              {v.linked && <div><button className="btn sm ghost" type="button" onClick={() => void slackApi.forget().then((x) => view.setData(x))}>Forget the tokens and settings</button></div>}
            </div>
          </Panel>
          <Panel title="More from Slack (all off until you turn them on)">
            <Powers key={JSON.stringify(v.config.strict_allowed)} v={v} onSaved={(x) => view.setData(x)} />
          </Panel>
          <Panel title="What stays in keel">
            <ul>
              <li>Budget and secret gates, always.</li>
              <li>A gate only a person signed in to keel answers, and the rest of what the Supervisor may never answer: new dependencies, read-only commits, notes, the final review, the pull request. {v.config.allow_all_gates ? "You allowed the people on the short list to answer these, with a reason." : "Allow every gate from Slack changes this one."}</li>
              <li>A form Slack cannot show: too many questions or options, or a very long option.</li>
            </ul>
          </Panel>
          {v.log.length > 0 && (
            <Panel title="What happened from Slack">
              <ul aria-label="From Slack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {v.log.map((r, i) => <li key={i}><span className="mono">{when(r.at)}</span> {r.who} {r.what} {!r.ok && <Pill tone="warn">refused</Pill>}</li>)}
              </ul>
            </Panel>
          )}
        </div>
      )}
    </Async>
  );
}
