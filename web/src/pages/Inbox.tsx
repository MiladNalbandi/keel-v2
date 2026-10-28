// Inbox (Run): everything that waits for a person, in every project — gates, the explorer's questions, failing checks,
// budget pauses, new dependencies. Each item can be answered here (the same resume as the Flow page) or opened on its
// Flow page. The list follows the app's event stream (gate events send project.changed to every tab).
// #/inbox/<project> opens it filtered to one project (the Answer link on All projects). Long details fold with
// "Show more"; after you answer, the focus moves to the next item so the keyboard can go on.
// A part shows the items of its own kinds with its own card (the slot inbox.card: the Tasks plugin's task items).
// v0.16.0 an agent's (or a workflow's) request to install a plugin is keel's own card: Approve and install, or Deny.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts } from "../api";
import { ClarifyForm, type ClarifyAnswers } from "../components/ClarifyForm";
import { questionsPayload, reasonsMissing } from "../questions";
import { ChoiceForm } from "../components/ChoiceForm";
import { clearDraft, DraftKept, dropOtherDrafts, noKeys, questionKey, useAnswerDraft } from "../drafts";
import { RunModeNote } from "../components/RunMode";
import { EmptyState } from "../components/EmptyState";
import { GateDetail } from "./Flow";
import { InstallRequestCard } from "../components/InstallRequestCard";
import { ApprovalCard } from "../components/ApprovalCard";
import { ErrorBox, Loading, PageHead, Pill, Since, Ticks } from "../components/ui";
import { inboxApi, type InboxAnswer, type InboxItem } from "../inboxApi";
import { useHasPage, useSlot } from "../sdk/registry";
import { SLOTS, type InboxCardItem } from "../sdk/slots";
import { queryValue } from "../routes";
import { go, useApp, useLoad, useRoute } from "../state";
import "../components/inbox.css";

const KIND_LABEL: Record<string, string> = {
  gate: "gate", clarify: "questions", fix: "needs a fix", budget: "budget", usage: "plan window", dependency: "new dependency",
  permission: "may it run?", "plugin-install": "install request", "stack-setup": "stack setup", "product-jira-send": "send to Jira",
};
const kindLabel = (k: string) => KIND_LABEL[k] ?? k;
const PLUGIN_INSTALL = "plugin-install";
const STACK_SETUP = "stack-setup";
/** P4 an approval keel or a part asks itself (no flow behind it): answered at /approvals/{id}/decide. */
const isApproval = (it: InboxItem) => it.step === "approval" && it.kind !== PLUGIN_INSTALL && it.kind !== STACK_SETUP;
/** One waiting question: its id, else the step with the AC and the time it began to wait, so a gate that comes back to the
 *  same step is a new card (not the old one with its buttons stuck disabled). */
const keyOf = (it: InboxItem) => `${it.thread_id}:${it.id ?? `${it.step}|${it.ac ?? ""}|${it.since ?? ""}`}`;

/** Details longer than this fold behind "Show more". */
const LONG_LINES = 6;
const LONG_CHARS = 420;

/** A pause's text, folded when long. `more`: the Inbox got it cut (700 characters): Show more loads the whole text
 *  from the flow (before, it only unfolded the cut part and the rest stayed on the Flow page). */
function Detail({ text, id, more, full }: { text: string; id: string; more?: boolean; full?: () => Promise<string | null> }) {
  const [whole, setWhole] = useState<string | null>(null);
  const shown = whole ?? text;
  const long = !!more || shown.split("\n").length > LONG_LINES || shown.length > LONG_CHARS;
  const [open, setOpen] = useState(false);
  const [missing, setMissing] = useState(false);
  const toggle = async () => {
    if (!open && more && whole === null && full) {
      const t = await full().catch(() => null);
      if (t) setWhole(t);
      else setMissing(true);
    }
    setOpen((o) => !o);
  };
  return (
    <div className="inbox-detail-wrap">
      {/* a gate's text is Markdown (a PR body, a final review), with `$ command` output folded, as on the Flow page */}
      <div id={id} className={`inbox-detail md${long ? (open ? " open" : " folded") : ""}`}><GateDetail text={shown} /></div>
      {long && (
        <button className="btn sm ghost inbox-more" type="button" aria-expanded={open} aria-controls={id} onClick={() => void toggle()}>
          {open ? "Show less" : "Show more"}
        </button>
      )}
      {more && (missing || !full) && <span className="hint">The rest is on the Flow page.</span>}
    </div>
  );
}

type Buttons = { approve: string; reject: string; needWhy: boolean; whyLabel: string };

/** The button words for one item (the engine's labels win). */
function buttonsFor(it: InboxItem): Buttons {
  const l = it.labels ?? {};
  if (it.kind === "dependency") return { approve: l.approve ?? "Allow", reject: l.reject ?? "Refuse", needWhy: false, whyLabel: "Why (optional)" };
  if (it.kind === "budget") return { approve: "Continue over the cap", reject: "Stop here", needWhy: false, whyLabel: "Note (optional)" };
  if (it.kind === "fix") return { approve: l.approve ?? "Try again", reject: l.reject ?? "Stop the flow", needWhy: false, whyLabel: "Note (optional)" };
  if (it.kind === "clarify") return { approve: l.approve ?? "Send my answers", reject: "Send back", needWhy: false, whyLabel: "Anything else the explorer should know (optional)" };
  // a commit check asks whether to escalate; keeping the change flow is a send back, which needs a reason
  if (/escalat/i.test(it.title)) return { approve: "Yes, switch to a feature flow", reject: "No, keep the change flow", needWhy: true, whyLabel: "Why (needed to keep the change flow)" };
  return { approve: l.approve ?? `Approve${it.ac ? " " + it.ac : ""}`, reject: l.reject ?? "Send back", needWhy: true, whyLabel: "Why (needed to send back)" };
}

/** A Helper's command that waits for the person's OK: Allow once, Always (this command, for the rest of that chat), Deny. */
function PermissionInboxCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const { setProjectId } = useApp();
  const q = it.permission!;
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-perm-${q.id}`;
  // v0.10.0: Claude Code's acting tool (keel2 mcp --write) asks here too, with no KeelBot chat behind it
  const mcp = q.session === "mcp";
  const who = mcp ? "Claude Code" : "KeelBot";
  // KeelBot's chat is a column of the Code page, or its own page: "Open KeelBot" only when this keel has one (step 6)
  const code = useHasPage("repo");
  const keelbot = useHasPage("helper");
  const chat = code ? "repo" : keelbot ? "helper" : null;
  const answer = async (decision: "once" | "always" | "deny") => {
    setBusy(true);
    setErr(null);
    try {
      await inboxApi.decide(q.id, decision, why.trim());
      await onDone(decision === "deny" ? `Refused: ${who} will not run it.` : `Allowed: ${who} runs it now.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="inbox-item k-permission" aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="warn">{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {who}{mcp ? " (keel MCP)" : ` · ${it.flow}`}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      <pre className="code inbox-cmd">{q.command}</pre>
      <div className="field">
        <label htmlFor={`${hid}-why`}>Why not (optional, {who} reads it)</label>
        <input id={`${hid}-why`} value={why} onChange={(e) => setWhy(e.target.value)} />
      </div>
      <div className="row inbox-actions">
        <button className="btn sm warn" type="button" disabled={busy} onClick={() => void answer("once")}>{mcp ? "Allow" : "Allow once"}</button>
        {!mcp && <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("always")}>Always for this command</button>}
        <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("deny")}>Deny</button>
        {!mcp && chat && <button className="btn sm ghost inbox-open" type="button" onClick={() => { setProjectId(it.project_id); go(chat); }}
          aria-label={`Open KeelBot in ${it.project_name}`}>Open KeelBot ▸</button>}
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}

/** keel 0.18.22 a stack's setup commands (apt packages, run once, each worktree) wait for the person's OK: the commands as
 *  code, the engine's warning, then Approve once / Always / Deny. Once and Always both approve these exact commands. */
function StackSetupCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const p = (it.payload ?? {}) as { command?: string; warning?: string };
  const commands = p.command || it.detail;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-stack-${it.id}`;
  const answer = async (decision: "once" | "always" | "deny") => {
    setBusy(true);
    setErr(null);
    try {
      await inboxApi.decide(it.id ?? "", decision, decision === "deny" ? "The person said no to these setup commands." : "");
      await onDone(decision === "deny" ? "Refused: the setup commands will not run." : "Approved: the setup commands run now.");
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="inbox-item k-stack-setup" aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="warn">{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b></span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      {p.warning && <p className="hint" role="note">{p.warning}</p>}
      <pre className="code inbox-cmd">{commands}</pre>
      <div className="row inbox-actions">
        <button className="btn sm warn" type="button" disabled={busy} onClick={() => void answer("once")}>Approve once</button>
        <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("always")}>Always for these commands</button>
        <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("deny")}>Deny</button>
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}

function InboxCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const { setProjectId } = useApp();
  const b = buttonsFor(it);
  // keel 0.18.64 the unsent answer is kept, the same draft as the Flow page's gate card (another page and back: still
  // there; answered: gone)
  const qkey = questionKey(it);
  const place = useMemo(() => ({ project: it.project_id, thread: it.thread_id, question: qkey }), [it.project_id, it.thread_id, qkey]);
  useEffect(() => dropOtherDrafts(it.project_id, it.thread_id, qkey), [it.project_id, it.thread_id, qkey]);
  const [why, setWhy, keptWhy] = useAnswerDraft<string>(place, "why", "");
  // a kept reason shows its box again
  const [askWhy, setAskWhy] = useState(() => !!why.trim());
  const [picked, setPicked, keptPicked] = useAnswerDraft<ClarifyAnswers>(place, "picked", {}, noKeys);
  const [typed, setTyped, keptTyped] = useAnswerDraft<ClarifyAnswers>(place, "typed", {});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-${it.thread_id}`;
  const whyId = `${hid}-why`;

  // keel 0.18.13 the engine says what each named choice means: one card each (ChoiceForm), its own reason box
  const info = (it.kind === "gate" || it.kind === "fix" || it.kind === "usage") && it.choice_info?.length ? it.choice_info : null;
  const act = async (decision: "approve" | "reject", payload?: Record<string, unknown>, msg?: string, said = why) => {
    // a gate's exit named "reject" sends back too: the engine wants the reason for it as well
    if ((decision === "reject" && b.needWhy || (payload?.choice === "reject" && !info)) && !said.trim()) {
      setAskWhy(true);
      window.setTimeout(() => document.getElementById(whyId)?.focus(), 0);
      return;
    }
    // B9 a ticked step of a skip menu needs its reason before the answer goes
    const missing = decision === "approve" && it.questions?.length ? reasonsMissing(it.questions, picked, typed) : "";
    if (missing) {
      setErr({ message: missing });
      return;
    }
    setBusy(true);
    setErr(null);
    const body: InboxAnswer = { decision, id: it.id ?? undefined, ...(said.trim() ? { why: said.trim() } : {}) };
    // keel 0.18.13 a gate may ask questions next to its choices (hunt lenses, a skip menu): both go back together
    if (it.questions?.length) body.payload = { ...(payload ?? {}), ...questionsPayload(it.questions, picked, typed) };
    else if (payload) body.payload = payload;
    try {
      await inboxApi.act(it.thread_id, body);
      clearDraft(place);
      await onDone(msg ?? (decision === "approve" ? `${it.project_name}: ${b.approve}. The flow goes on.` : `${it.project_name}: ${b.reject}.`));
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  const openFlow = () => {
    setProjectId(it.project_id);
    go("flow");
  };

  const choices = it.choices ?? [];
  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone={it.kind === "fix" || it.kind === "budget" || it.kind === "usage" ? "bad" : "warn"}>{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {it.flow}{it.workflow_id ? <> · <span className="mono">{it.workflow_id}</span></> : null}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      <RunModeNote mode={it.run_mode} kind={it.kind} title={it.title} auto={{ count: it.auto_approved ?? 0, last: it.last_auto }} />
      {it.kind === "clarify" && it.questions?.length ? (
        <ClarifyForm questions={it.questions} picked={picked} typed={typed}
          onPick={(id, value) => setPicked((p) => ({ ...p, [id]: value }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
      ) : it.detail ? (
        <Detail text={it.detail} id={`${hid}-detail`} more={it.more}
          full={async () => (await api.flowOf(it.project_id, it.thread_id)).thread?.waiting?.detail ?? null} />
      ) : null}
      {it.kind !== "clarify" && it.questions?.length ? (
        <ClarifyForm questions={it.questions} picked={picked} typed={typed}
          onPick={(id, value) => setPicked((p) => ({ ...p, [id]: value }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
      ) : null}
      {!info && (askWhy || !b.needWhy) && (
        <div className="field">
          <label htmlFor={whyId}>{b.whyLabel}</label>
          <textarea id={whyId} rows={2} value={why} onChange={(e) => setWhy(e.target.value)} />
        </div>
      )}
      {info && (
        <ChoiceForm id={hid} choices={info} busy={busy} draft={place}
          onSend={(choice, said) => act("approve", { choice }, `${it.project_name}: ${info.find((c) => c.name === choice)?.label || choice}.`, said)} />
      )}
      <div className="row inbox-actions">
        {info ? null : it.kind === "usage" && choices.length ? (
          <>
            {choices.includes("continue") && <button className="btn warn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "continue" })}>Continue anyway</button>}
            {choices.includes("wait") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "wait" })}>Wait for the reset</button>}
            {choices.includes("cheaper") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "cheaper" })}>Use the cheaper model</button>}
            {choices.includes("stop") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>Stop here</button>}
          </>
        ) : (it.kind === "gate" || it.kind === "fix") && choices.length ? (
          <>
            {choices.map((c, n) => (
              <button key={c} className={`btn sm${n === 0 ? " warn" : ""}`} type="button" disabled={busy}
                onClick={() => act("approve", { choice: c }, `${it.project_name}: ${c}.`)}>{c}</button>
            ))}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>{b.reject}</button>}
          </>
        ) : (
          <>
            {it.options.includes("approve") && <button className="btn warn sm" type="button" disabled={busy} onClick={() => act("approve")}>{b.approve}</button>}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>{b.reject}</button>}
          </>
        )}
        <button className="btn sm ghost inbox-open" type="button" onClick={openFlow} aria-label={`Open flow ${it.flow} in ${it.project_name}`}>Open flow ▸</button>
      </div>
      <DraftKept shown={keptWhy || keptPicked || keptTyped} />
      {err && <ErrorBox error={err} />}
    </article>
  );
}

export function InboxPage() {
  const { toast, reloadProjects, projects: known } = useApp();
  // the parts' cards, by the kinds they show (live: a plugin's web part registers after keel started)
  const cards = useSlot<InboxCardItem>(SLOTS.inboxCard);
  const cardOf = (kind: string) => cards.find((c) => Object.hasOwn(c.kinds, kind)) ?? null;
  const labelOf = (kind: string) => KIND_LABEL[kind] ?? cardOf(kind)?.kinds[kind] ?? kind;
  const route = useRoute();
  const { arg } = route;
  // a link to one item (#/inbox/<project>?item=task-item-12, the task drawer's "Open it in the Inbox"): it comes into view
  const wanted = queryValue(route, "item");
  const shown = useRef("");
  const r = useLoad("inbox", () => inboxApi.list());
  const [project, setProject] = useState(arg ?? "");
  useEffect(() => setProject(arg ?? ""), [arg]);
  const [kind, setKind] = useState("");
  const items = useMemo(
    () => (r.data?.items ?? []).filter((it) => (!project || it.project_id === project) && (!kind || it.kind === kind)),
    [r.data, project, kind],
  );
  // v0.7.x: when several flows of one project wait at once, each flow's items sit under its own heading
  const groups = useMemo(() => {
    const flowsOf = new Map<string, Set<string>>();
    for (const it of items) {
      if (it.task || it.permission || it.kind === PLUGIN_INSTALL || it.kind === STACK_SETUP || isApproval(it)) continue;
      flowsOf.set(it.project_id, (flowsOf.get(it.project_id) ?? new Set<string>()).add(it.thread_id));
    }
    if (![...flowsOf.values()].some((x) => x.size > 1)) return null;
    const order: string[] = [];
    const by = new Map<string, InboxItem[]>();
    for (const it of items) {
      const g = it.task || it.permission || it.kind === PLUGIN_INSTALL || it.kind === STACK_SETUP || isApproval(it) ? `other:${it.project_id}` : `${it.project_id}:${it.thread_id}`;
      if (!by.has(g)) { by.set(g, []); order.push(g); }
      by.get(g)!.push(it);
    }
    return order.map((g) => {
      const first = by.get(g)![0];
      return { key: g, items: by.get(g)!, title: g.startsWith("other:") ? `${first.project_name} · tasks and KeelBot` : `${first.project_name} · ${first.flow}` };
    });
  }, [items]);
  const total = r.data?.count ?? 0;
  // a project from the link that has nothing waiting still shows in the filter
  const projectOpts = useMemo(() => {
    const l = r.data?.projects ?? [];
    return project && !l.some((p) => p.id === project) ? [...l, { id: project, name: known.find((p) => p.id === project)?.name ?? project, count: 0 }] : l;
  }, [r.data, project, known]);

  // after an answer: focus the item that came after it (or before it, or the empty state)
  const listRef = useRef<HTMLDivElement>(null);
  const emptyRef = useRef<HTMLDivElement>(null);
  const focusNext = useRef<{ key: string | null; index: number } | null>(null);
  useEffect(() => {
    const f = focusNext.current;
    if (!f) return;
    focusNext.current = null;
    const els = [...(listRef.current?.querySelectorAll<HTMLElement>("[data-testid=inbox-item]") ?? [])];
    const target = els.find((el) => el.dataset.key === f.key) ?? els[Math.min(f.index, els.length - 1)] ?? emptyRef.current;
    target?.focus();
  }, [items]);

  useEffect(() => {
    if (!wanted || shown.current === wanted) return;
    const it = items.find((x) => x.id === wanted);
    const el = it && [...(listRef.current?.querySelectorAll<HTMLElement>("[data-testid=inbox-item]") ?? [])].find((e) => e.dataset.key === keyOf(it));
    if (!el) return;
    shown.current = wanted;
    el.scrollIntoView?.({ block: "center" });
    el.focus();
  }, [items, wanted]);

  const done = async (msg: string, key: string) => {
    const i = items.findIndex((x) => keyOf(x) === key);
    const next = items[i + 1] ?? items[i - 1] ?? null;
    focusNext.current = { key: next ? keyOf(next) : null, index: Math.max(0, i) };
    toast(msg);
    await Promise.all([r.reload(), reloadProjects()]);
  };

  const card = (it: InboxItem) => {
    const Card = cardOf(it.kind)?.component;
    if (Card) return <Card key={keyOf(it)} item={it} cardKey={keyOf(it)} kindLabel={labelOf(it.kind)} onDone={(msg) => done(msg, keyOf(it))} />;
    if (it.permission) return <PermissionInboxCard key={keyOf(it)} it={it} onDone={(msg) => done(msg, keyOf(it))} />;
    if (it.kind === STACK_SETUP) return <StackSetupCard key={keyOf(it)} it={it} onDone={(msg) => done(msg, keyOf(it))} />;
    if (it.kind === PLUGIN_INSTALL) return <InstallRequestCard key={keyOf(it)} it={it} cardKey={keyOf(it)} onDone={(msg) => done(msg, keyOf(it))} />;
    if (isApproval(it)) return <ApprovalCard key={keyOf(it)} it={it} cardKey={keyOf(it)} kindLabel={labelOf(it.kind)} onDone={(msg) => done(msg, keyOf(it))} />;
    // a task's item whose part has not loaded yet: it shows when its card registers
    if (it.task) return null;
    return <InboxCard key={keyOf(it)} it={it} onDone={(msg) => done(msg, keyOf(it))} />;
  };

  return (
    <>
      <PageHead title="Inbox" sub="Everything that waits for you, in every project: gates, questions, and tasks to confirm. Answer it here or open its flow or task." />
      <div className="row inbox-filters" role="group" aria-label="Filters">
        <label className="row" htmlFor="inbox-project"><span className="sub">In project</span>
          <select id="inbox-project" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">All projects</option>
            {projectOpts.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.count})</option>)}
          </select>
        </label>
        <label className="row" htmlFor="inbox-kind"><span className="sub">Kind</span>
          <select id="inbox-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All kinds</option>
            {(r.data?.kinds ?? []).map((k) => <option key={k} value={k}>{labelOf(k)}</option>)}
          </select>
        </label>
        {r.data && <span className="hint" aria-live="polite">{items.length === total ? `${total} waiting` : `${items.length} of ${total} waiting`}</span>}
      </div>
      {r.error && !r.data ? <ErrorBox error={r.error} onRetry={() => void r.reload()} />
        : !r.data ? <Loading what="Loading the inbox" />
          : !items.length ? (
            <div className="panel">
              <EmptyState ref={emptyRef} className="inbox-empty" title="Nothing is waiting for you"
                action={total ? <button className="btn sm" type="button" onClick={() => { setProject(""); setKind(""); }}>Show everything</button>
                  : <a className="btn sm" href="#/projects">See your projects</a>}>
                {total ? "Nothing matches these filters." : "When a flow stops at a gate or needs a decision, it shows up here."}
              </EmptyState>
            </div>
          ) : (
            <div className="inbox-list" ref={listRef}>
              {groups
                ? groups.map((g) => (
                  <section key={g.key} className="inbox-group" aria-label={g.title}>
                    <h2 className="inbox-group-h">{g.title} <span className="sub">{g.items.length}</span></h2>
                    {g.items.map(card)}
                  </section>
                ))
                : items.map(card)}
            </div>
          )}
    </>
  );
}
