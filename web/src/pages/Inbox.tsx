// Inbox (Run): everything that waits for a person, in every project — gates, the explorer's questions, failing checks,
// budget pauses, new dependencies. Each item can be answered here (the same resume as the Flow page) or opened on its
// Flow page. The list follows the app's event stream (gate events send project.changed to every tab).
// #/inbox/<project> opens it filtered to one project (the Answer link on All projects). Long details fold with
// "Show more"; after you answer, the focus moves to the next item so the keyboard can go on.
// keel 0.28 (the redesign) a list on the left (kind, project, age, title, one line) and the selected item's card on the
// right; keys answer it (J K move, A approve, R to the reason box, 1–9 a choice, O open the flow, L later on a task),
// and after an answer the next item is selected. The filters are chips with counts.
// A part shows the items of its own kinds with its own card (the slot inbox.card: the Tasks plugin's task items).
// v0.16.0 an agent's (or a workflow's) request to install a plugin is keel's own card: Approve and install, or Deny.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts } from "../api";
import { ClarifyForm, type ClarifyAnswers } from "../components/ClarifyForm";
import { questionsPayload, reasonsMissing } from "../questions";
import { ChoiceForm } from "../components/ChoiceForm";
import { clearDraft, DraftKept, draftKey, dropOtherDrafts, noKeys, questionKey, useAnswerDraft } from "../drafts";
import { writeStored } from "../storage";
import { RunModeNote } from "../components/RunMode";
import { EmptyState } from "../components/EmptyState";
import { GateDetail } from "./Flow";
import { InstallRequestCard } from "../components/InstallRequestCard";
import { ApprovalCard } from "../components/ApprovalCard";
import { ErrorBox, Loading, PageHead, Pill, Since, Ticks } from "../components/ui";
import { Key } from "../components/Key";
import { isTyping, modalOpen } from "../keys";
import { inboxApi, type InboxAnswer, type InboxItem } from "../inboxApi";
import { useHasPage, useSlot } from "../sdk/registry";
import { SLOTS, type InboxCardItem, type InboxFill, type InboxNoteItem } from "../sdk/slots";
import { queryValue } from "../routes";
import { go, useApp, useLoad, useRoute } from "../state";
import "../components/inbox.css";

const KIND_LABEL: Record<string, string> = {
  gate: "gate", clarify: "questions", fix: "needs a fix", crash: "step failed", budget: "budget", dependency: "new dependency",
  permission: "may it run?", "plugin-install": "install request", "stack-setup": "stack setup", "product-jira-send": "send to Jira",
  session: "session gone",
};
const kindLabel = (k: string) => KIND_LABEL[k] ?? k;
/** The pauses keel answers with named choices: a gate, a stuck step (fix), #116 a step that crashed (crash) and, 0.29, a
 *  step whose connected session is gone (session: keep waiting, keel's harness, stop). */
const NAMED = new Set(["gate", "fix", "crash", "session"]);
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
  // #116 the flow doctor's pause for a step that crashed (not an agent step): Retry the step / Stop the flow
  if (it.kind === "crash") return { approve: l.approve ?? "Retry the step", reject: l.reject ?? "Stop the flow", needWhy: false, whyLabel: "Note (optional)" };
  // 0.29 a step that waits for a connected session that is gone: no reason needed to stop
  if (it.kind === "session") return { approve: l.approve ?? "Keep waiting", reject: l.reject ?? "Stop the flow", needWhy: false, whyLabel: "Note (optional)" };
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
        <input id={`${hid}-why`} value={why} aria-keyshortcuts="R" onChange={(e) => setWhy(e.target.value)} />
      </div>
      <div className="row inbox-actions">
        <button className="btn sm warn" type="button" disabled={busy} aria-keyshortcuts="A" onClick={() => void answer("once")}>{mcp ? "Allow" : "Allow once"} <Key k="A" /></button>
        {!mcp && <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("always")}>Always for this command</button>}
        <button className="btn sm" type="button" disabled={busy} onClick={() => void answer("deny")}>Deny</button>
        {!mcp && chat && <button className="btn sm ghost inbox-open" type="button" onClick={() => { setProjectId(it.project_id); go(chat); }}
          aria-label={`Open KeelBot in ${it.project_name}`} aria-keyshortcuts="O">Open KeelBot ▸ <Key k="O" /></button>}
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
        <button className="btn sm warn" type="button" disabled={busy} aria-keyshortcuts="A" onClick={() => void answer("once")}>Approve once <Key k="A" /></button>
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
  const notes = useSlot<InboxNoteItem>(SLOTS.inboxNote);
  // a filled-in choice is a draft the choice form reads when it comes again
  const [choiceForm, setChoiceForm] = useState(0);

  // keel 0.18.13 the engine says what each named choice means: one card each (ChoiceForm), its own reason box
  const info = NAMED.has(it.kind) && it.choice_info?.length ? it.choice_info : null;
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

  // 0.21.0 a note's suggestion goes into the form; nothing is sent
  const fill = (f: InboxFill) => {
    if (f.answers) setPicked((p) => ({ ...p, ...f.answers }));
    if (info) {
      if (f.choice) writeStored(draftKey(place, "choice"), { v: f.choice, at: Date.now() }, "local");
      // a choice that takes no reason has no box: words the person cannot see never go out in their name
      const target = info.find((c) => c.name === f.choice) ?? info.find((c) => c.default) ?? info[0];
      if (f.why != null && target?.why !== "none") writeStored(draftKey(place, "choice-why"), { v: f.why, at: Date.now() }, "local");
      setChoiceForm((n) => n + 1);
    } else if (f.why != null) {
      setWhy(f.why);
      setAskWhy(true);
    }
  };

  const choices = it.choices ?? [];
  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={keyOf(it)} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone={it.kind === "fix" || it.kind === "crash" || it.kind === "budget" ? "bad" : "warn"}>{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {it.flow}{it.workflow_id ? <> · <span className="mono">{it.workflow_id}</span></> : null}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      {notes.map((n) => <n.component key={n.id} item={it} fill={fill} />)}
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
          <textarea id={whyId} rows={2} value={why} aria-keyshortcuts="R" onChange={(e) => setWhy(e.target.value)} />
        </div>
      )}
      {info && (
        <ChoiceForm key={choiceForm} id={hid} choices={info} busy={busy} draft={place} sendKey="A"
          onSend={(choice, said) => act("approve", { choice }, `${it.project_name}: ${info.find((c) => c.name === choice)?.label || choice}.`, said)} />
      )}
      <div className="row inbox-actions">
        {info ? null : NAMED.has(it.kind) && choices.length ? (
          <>
            {choices.map((c, n) => (
              <button key={c} className={`btn sm${n === 0 ? " warn" : ""}`} type="button" disabled={busy} aria-keyshortcuts={n < 9 ? String(n + 1) : undefined}
                onClick={() => act("approve", { choice: c }, `${it.project_name}: ${c}.`)}>{c}{n < 9 && <> <Key k={String(n + 1)} /></>}</button>
            ))}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} aria-keyshortcuts="R" onClick={() => act("reject")}>{b.reject} <Key k="R" /></button>}
          </>
        ) : (
          <>
            {it.options.includes("approve") && <button className="btn warn sm" type="button" disabled={busy} aria-keyshortcuts="A" onClick={() => act("approve")}>{b.approve} <Key k="A" /></button>}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} aria-keyshortcuts="R" onClick={() => act("reject")}>{b.reject} <Key k="R" /></button>}
          </>
        )}
        <button className="btn sm ghost inbox-open" type="button" onClick={openFlow} aria-label={`Open flow ${it.flow} in ${it.project_name}`}
          aria-keyshortcuts="O">Open flow ▸ <Key k="O" /></button>
      </div>
      <DraftKept shown={keptWhy || keptPicked || keptTyped} />
      {err && <ErrorBox error={err} />}
    </article>
  );
}

/** keel 0.28 how each kind looks in the list: a mark and a tone (the redesign's Inbox). */
const KIND_LOOK: Record<string, { mark: string; tone: "warn" | "acc" | "bad" | "run" }> = {
  gate: { mark: "◆", tone: "warn" }, clarify: { mark: "?", tone: "acc" }, fix: { mark: "!", tone: "bad" }, crash: { mark: "!", tone: "bad" },
  budget: { mark: "$", tone: "bad" }, usage: { mark: "$", tone: "bad" }, dependency: { mark: "+", tone: "warn" }, permission: { mark: "›", tone: "warn" },
  "plugin-install": { mark: "↓", tone: "run" }, "stack-setup": { mark: "#", tone: "warn" }, "product-jira-send": { mark: "↗", tone: "acc" },
};
const lookOf = (it: InboxItem) => KIND_LOOK[it.kind] ?? (it.task ? { mark: "☐", tone: "run" as const } : isApproval(it) ? { mark: "✓", tone: "acc" as const } : { mark: "•", tone: "acc" as const });

/** The row's one line: the command, the plugin's summary, or the first line of the detail (without Markdown marks). */
function lineOf(it: InboxItem): string {
  const p = (it.payload ?? {}) as { command?: string; summary?: string; reason?: string };
  if (it.permission) return it.permission.command;
  if (it.kind === STACK_SETUP && p.command) return p.command.split("\n")[0];
  if (it.kind === PLUGIN_INSTALL) return p.summary || p.reason || "";
  if (it.task) return it.task.key ? `${it.task.key} · ${it.task.title}` : it.task.title;
  const first = (it.detail ?? "").split("\n").map((l) => l.replace(/^[#>*\-\s]+|[`*_]/g, "").trim()).find(Boolean);
  return first ?? it.flow ?? "";
}

/** Where it comes from, after the project: the flow, or who asks (none for a part's own kind: its card says). */
function whereOf(it: InboxItem, partKind: boolean): string {
  if (it.permission) return it.permission.session === "mcp" ? "Claude Code" : "KeelBot";
  if (partKind || it.kind === PLUGIN_INSTALL || it.kind === STACK_SETUP || it.task) return "";
  return it.flow ?? "";
}

/** How long it waits, for the list: "now", "4 min", "2 h", "3 d" (the card says it to the second). */
function ageOf(since: string, now: number): string {
  const s = Math.max(0, (now - Date.parse(since)) / 1000);
  if (Number.isNaN(s)) return "";
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

/** The Inbox keys a page-wide handler reads (they go to the selected item's card, by its aria-keyshortcuts). */
const KEY_ROWS: { label: string; keys: string[] }[] = [
  { label: "Next / previous", keys: ["J", "K"] },
  { label: "Approve", keys: ["A"] },
  { label: "Send back: to the reason box", keys: ["R"] },
  { label: "Pick a choice", keys: ["1–9"] },
  { label: "Open the flow", keys: ["O"] },
  { label: "Later (a task)", keys: ["L"] },
];

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
  const all = r.data?.items ?? [];
  const items = useMemo(
    () => all.filter((it) => (!project || it.project_id === project) && (!kind || it.kind === kind)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  // the list's order (the groups' order when there are groups); a task's item whose part has not loaded yet has no card
  // and no row (it shows when its card registers)
  const rows = useMemo(() => (groups ? groups.flatMap((g) => g.items) : items).filter((it) => !it.task || !!cardOf(it.kind)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [groups, items, cards]);
  const total = r.data?.count ?? 0;
  // a project from the link that has nothing waiting still shows in the filter
  const projectOpts = useMemo(() => {
    const l = r.data?.projects ?? [];
    return project && !l.some((p) => p.id === project) ? [...l, { id: project, name: known.find((p) => p.id === project)?.name ?? project, count: 0 }] : l;
  }, [r.data, project, known]);
  // keel 0.28 the kind filter says how many wait of each kind (in the project picked)
  const kindCount = (k: string) => all.filter((it) => it.kind === k && (!project || it.project_id === project)).length;

  // keel 0.28 one item is selected: its card shows on the right. The others stay mounted (what you typed in them is
  // kept while you move), and only the selected one is shown.
  const [sel, setSel] = useState<string | null>(null);
  const lastIndex = useRef(0);
  const selIndex = (() => {
    const i = sel ? rows.findIndex((x) => keyOf(x) === sel) : -1;
    return i >= 0 ? i : rows.length ? Math.min(lastIndex.current, rows.length - 1) : -1;
  })();
  const current = selIndex >= 0 ? rows[selIndex] : null;
  const curKey = current ? keyOf(current) : null;
  useEffect(() => {
    if (selIndex >= 0) lastIndex.current = selIndex;
  }, [selIndex]);

  // the rows' ages move on each half minute
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, []);

  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const emptyRef = useRef<HTMLDivElement>(null);
  const cardEl = (key: string | null) =>
    key ? [...(detailRef.current?.querySelectorAll<HTMLElement>("[data-testid=inbox-item]") ?? [])].find((el) => el.dataset.key === key) ?? null : null;
  const rowEl = (key: string | null) =>
    key ? [...(listRef.current?.querySelectorAll<HTMLElement>("[data-row-key]") ?? [])].find((el) => el.dataset.rowKey === key) ?? null : null;

  // after an answer: select and focus the item that came after it (or before it, or the empty state)
  const focusNext = useRef<{ key: string | null; index: number } | null>(null);
  useEffect(() => {
    const f = focusNext.current;
    if (!f) return;
    focusNext.current = null;
    const target = cardEl(f.key) ?? (rows.length ? cardEl(keyOf(rows[Math.min(f.index, rows.length - 1)])) : null) ?? emptyRef.current;
    target?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  // J / K moved the selection: its row takes the focus (so the next key goes on from there)
  const focusRow = useRef<string | null>(null);
  useEffect(() => {
    const k = focusRow.current;
    if (!k) return;
    focusRow.current = null;
    const el = rowEl(k);
    el?.focus();
    el?.scrollIntoView?.({ block: "nearest" });
  });

  useEffect(() => {
    if (!wanted || shown.current === wanted) return;
    const it = rows.find((x) => x.id === wanted);
    if (!it) return;
    if (sel !== keyOf(it)) {
      setSel(keyOf(it));
      return;
    }
    const el = cardEl(keyOf(it));
    if (!el) return;
    shown.current = wanted;
    rowEl(keyOf(it))?.scrollIntoView?.({ block: "nearest" });
    el.scrollIntoView?.({ block: "center" });
    el.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, wanted, sel]);

  const done = async (msg: string, key: string) => {
    const i = rows.findIndex((x) => keyOf(x) === key);
    const next = rows[i + 1] ?? rows[i - 1] ?? null;
    focusNext.current = { key: next ? keyOf(next) : null, index: Math.max(0, i) };
    if (next) setSel(keyOf(next));
    toast(msg);
    await Promise.all([r.reload(), reloadProjects()]);
  };

  const pick = (it: InboxItem) => {
    setSel(keyOf(it));
    // stacked (a phone): the card is under the list, so it comes into view
    if (window.matchMedia?.("(max-width: 900px)").matches) {
      window.setTimeout(() => cardEl(keyOf(it))?.scrollIntoView?.({ behavior: "smooth", block: "start" }), 0);
    }
  };
  const move = (by: number) => {
    if (!rows.length) return;
    const to = rows[Math.max(0, Math.min(rows.length - 1, (selIndex < 0 ? 0 : selIndex) + by))];
    setSel(keyOf(to));
    focusRow.current = keyOf(to);
  };

  // the Inbox keys, while the focus is on this page (not in a text field, not under a dialog): J K move, and the rest
  // presses the selected card's control that carries that key (aria-keyshortcuts), so a part's card can take them too
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || isTyping(e.target) || modalOpen()) return;
    const t = e.target as HTMLElement | null;
    const root = rootRef.current;
    if (!root || !t || !(root.contains(t) || t === document.body || t.id === "main")) return;
    const k = e.key.length === 1 ? e.key.toUpperCase() : e.key;
    if (k === "J" || k === "K") {
      e.preventDefault();
      move(k === "J" ? 1 : -1);
      return;
    }
    const card = cardEl(curKey);
    if (!card || !/^[AROL1-9]$/.test(k)) return;
    const all = [...card.querySelectorAll<HTMLElement>(`[aria-keyshortcuts="${k}"]`)].filter((el) => !(el as HTMLButtonElement).disabled);
    if (!all.length) return;
    e.preventDefault();
    // R goes to the reason box when there is one (it never sends by itself); else it presses Send back, which opens it
    const field = k === "R" ? all.find((el) => isTyping(el)) : undefined;
    if (field) {
      field.focus();
      return;
    }
    const el = all[0];
    el.click();
    if (/^[1-9]$/.test(k)) el.focus();
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);
  // ↓ ↑ on the list do what J K do
  const onListKey = (e: React.KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      move(e.key === "ArrowDown" ? 1 : -1);
    }
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
  // each card in a box that shows only while it is selected
  const slot = (it: InboxItem) => {
    const c = card(it);
    return c && <div key={keyOf(it)} className={`rdinbox-card${keyOf(it) === curKey ? " on" : ""}`}>{c}</div>;
  };

  const row = (it: InboxItem, grouped = false) => {
    const k = keyOf(it);
    const look = lookOf(it);
    const on = k === curKey;
    // under a group's heading (project · flow) the row does not say the flow again
    const where = grouped ? "" : whereOf(it, !!cardOf(it.kind));
    // the line, unless it only says the title again (a request whose title is its first words)
    const raw = lineOf(it);
    const line = raw && !raw.startsWith(it.title.replace(/…$/, "").slice(0, 40)) ? raw : "";
    return (
      <li key={k}>
        <button type="button" className={`rdinbox-row${on ? " on" : ""}`} data-testid="inbox-row" data-row-key={k} aria-current={on ? "true" : undefined}
          tabIndex={on ? 0 : -1} onClick={() => pick(it)}>
          <span className={`rdinbox-mark t-${look.tone}`} aria-hidden="true">{look.mark}</span>
          <span className="rdinbox-row-b">
            <span className="rdinbox-row-m">
              <span className={`rdinbox-kind t-${look.tone}`}>{labelOf(it.kind)}</span>
              <span className="rdinbox-proj">{it.project_name}{where ? ` · ${where}` : ""}</span>
              {it.since && <span className="rdinbox-age">{ageOf(it.since, now)}</span>}
            </span>
            <span className="rdinbox-row-t"><Ticks text={it.title} /></span>
            {line && <span className="rdinbox-row-l">{line}</span>}
          </span>
        </button>
      </li>
    );
  };

  const filters = (
    <div className="rdinbox-filters" role="group" aria-label="Filters">
      <label className={`rdinbox-chip${project ? " on" : ""}`} htmlFor="inbox-project"><span className="rdinbox-chip-d">In project</span>
        <select id="inbox-project" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">All projects ({total})</option>
          {projectOpts.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.count})</option>)}
        </select>
      </label>
      <label className={`rdinbox-chip${kind ? " on" : ""}`} htmlFor="inbox-kind"><span className="rdinbox-chip-d">Kind</span>
        <select id="inbox-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {(r.data?.kinds ?? []).map((k) => <option key={k} value={k}>{labelOf(k)} ({kindCount(k)})</option>)}
        </select>
      </label>
      {r.data && <span className="hint rdinbox-count" aria-live="polite">{items.length === total ? `${total} waiting` : `${items.length} of ${total} waiting`}</span>}
    </div>
  );

  return (
    <div className="rdinbox">
      <PageHead title="Inbox" sub="Everything that waits for you, in every project: gates, questions, and tasks to confirm. Answer it here or open its flow or task." />
      {r.error && !r.data ? <ErrorBox error={r.error} onRetry={() => void r.reload()} />
        : !r.data ? <Loading what="Loading the inbox" />
          : (
            <div className={`rdinbox-split${rows.length ? "" : " is-empty"}`} ref={rootRef}>
              <section className="rdinbox-list rise r1" aria-label="Waiting for you" ref={listRef} onKeyDown={onListKey}>
                {filters}
                {!rows.length ? (
                  <EmptyState ref={emptyRef} className="inbox-empty" title="Nothing is waiting for you"
                    action={total ? <button className="btn sm" type="button" onClick={() => { setProject(""); setKind(""); }}>Show everything</button>
                      : <a className="btn sm" href="#/projects">See your projects</a>}>
                    {total ? "Nothing matches these filters." : "When a flow stops at a gate or needs a decision, it shows up here."}
                  </EmptyState>
                ) : groups ? (
                  groups.map((g) => {
                    const shownRows = g.items.filter((it) => rows.includes(it));
                    return shownRows.length ? (
                      <div key={g.key} className="rdinbox-grp">
                        <h2 className="rdinbox-grp-h">{g.title} <span>{shownRows.length}</span></h2>
                        <ul className="rdinbox-rows" aria-label={g.title}>{shownRows.map((it) => row(it, true))}</ul>
                      </div>
                    ) : null;
                  })
                ) : (
                  <ul className="rdinbox-rows" aria-label="Waiting items">{rows.map((it) => row(it))}</ul>
                )}
                {rows.length > 0 && <div className="rdinbox-keys" aria-label="Inbox keys" role="note">
                  <span className="rdinbox-keys-h">Inbox keys</span>
                  {KEY_ROWS.map((k) => (
                    <span key={k.label} className="rdinbox-key">{k.label} <span>{k.keys.map((x) => <kbd key={x}>{x}</kbd>)}</span></span>
                  ))}
                </div>}
              </section>
              {/* nothing to show (all answered, or nothing matches the filters): the list's empty state says it */}
              <section className="rdinbox-detail rise r2" aria-label="The selected item" ref={detailRef} hidden={!rows.length}>
                {groups ? (
                  groups.map((g) => (
                    <section key={g.key} className="inbox-group" aria-label={g.title}>{g.items.map(slot)}</section>
                  ))
                ) : (
                  items.map(slot)
                )}
              </section>
            </div>
          )}
    </div>
  );
}
