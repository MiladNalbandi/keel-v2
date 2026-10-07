// Settings (Control): General settings for every project, and per-project overrides. Settings come in sections, each
// with one line that says what it is for, and every row says what it does. In the project tab a changed row has a
// badge, the General value and "Reset to General" (PUT { key: null }). Every change saves at once; the bar at the top
// (a pill at the bottom on a phone) says Saving… / Saved / Not saved.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, errorParts, type Model, type ProjectSettings, type Settings } from "../api";
import { ModelPicker } from "../components/ModelPicker";
import { Skeleton } from "../components/page";
import { RUN_MODES, runModeAbout } from "../components/RunMode";
import { ErrorBox, PageHead, Tabs } from "../components/ui";
import { kfmt, parseTokens, PROV } from "../format";
import { useApp, useLoad } from "../state";

type Key = keyof Settings;
type Kind =
  | { t: "select"; opts: [string, string][] }
  | { t: "bool"; on?: string; off?: string }
  | { t: "model" }
  | { t: "tokens" }
  | { t: "text"; suggest?: string[]; placeholder?: string }
  | { t: "list" };
type Row = {
  key: Key; label: string; kind: Kind;
  /** One line: what the setting does (a function when it depends on the value). */
  help: string | ((v: unknown) => string);
  /** shown when the api has no value yet (an older api) */
  def?: unknown;
  /** The help reads as a warning for this value. */
  warn?: (v: unknown) => boolean;
};
type Sec = { id: string; title: string; sub: string; rows: Row[] };

export const SECTIONS: Sec[] = [
  { id: "flow", title: "Flow and gates", sub: "How much a flow does by itself, and when it stops for you.", rows: [
    { key: "run_mode", label: "Run mode (new flows)", def: "manual", kind: { t: "select", opts: RUN_MODES.map(([k, l]) => [k, l]) },
      help: (v) => `${runModeAbout(String(v ?? "manual"))} A running flow can change it on the Flow page.` },
    { key: "gates_mode", label: "Gate mode", kind: { t: "select", opts: [["every-ac", "every AC"], ["end-of-lane", "end of lane"], ["end", "end of flow"]] },
      help: "When the AC gate waits for you: after every criterion, at the end of each lane, or once at the end." },
    { key: "keel_rules", label: "keel rules", kind: { t: "bool" }, help: "keel's locked steps (spec, contract, guard, final review) stay in every workflow. Off lets a workflow remove them." },
    { key: "fix_attempts", label: "Fix attempts per ladder rung", kind: { t: "select", opts: [["1", "1"], ["3", "3"], ["5", "5"]] },
      help: "How many times an agent may try to fix a failing setup step before it asks you." },
    { key: "coverage_min", label: "Coverage needed before push", kind: { t: "select", opts: [["0", "off"], ["70", "70%"], ["80", "80%"], ["90", "90%"]] },
      help: "The test coverage the ship flow needs before it pushes." },
  ] },
  { id: "models", title: "Models", sub: "Which model each kind of agent runs on. One agent can still use its own (Agents page).", rows: [
    { key: "default_model", label: "Default model", kind: { t: "model" }, help: "Every agent that has no model of its own." },
    { key: "implementer_model", label: "Implementer model", kind: { t: "model" }, help: "Writes the code that makes a failing test pass." },
    { key: "reviewer_model", label: "Reviewer model", kind: { t: "model" }, help: "Reviews each criterion and the whole branch." },
    { key: "cheaper_model", label: "Cheaper model", kind: { t: "model" }, warn: (v) => (v as Model | undefined)?.provider === "fake",
      help: (v) => (v as Model | undefined)?.provider === "fake"
        ? "Not set: the fake model never runs a real flow, so keel pauses and asks instead. Claude Haiku is the usual choice."
        : "Used only when a cap or a plan window is nearly used and you choose to switch. Claude Haiku by default." },
  ] },
  { id: "budget", title: "Budget", sub: "When a flow stops for cost. Extra caps for one project are on the Budget page.", rows: [
    { key: "cap_tokens", label: "Cap per flow (tokens)", kind: { t: "tokens" }, help: "A flow stops at this many tokens and does what you pick below. Start a flow can set its own cap." },
    { key: "on_cap", label: "When a cap is hit", kind: { t: "select", opts: [["pause", "pause and ask me"], ["cheaper", "switch to cheaper model"], ["stop", "stop"]] },
      help: "Pause and ask you, switch to the cheaper model, or stop the flow." },
    { key: "usage_warn", label: "Warn when a plan window is", def: 0.8, kind: { t: "select", opts: [["0.7", "70% used"], ["0.8", "80% used"], ["0.9", "90% used"]] },
      help: "Before a subscription agent runs: a warning in the flow when the provider's window is this full." },
    { key: "usage_pause", label: "Pause before the next agent at", def: 0.95, kind: { t: "select", opts: [["0.9", "90% used"], ["0.95", "95% used"], ["0.99", "99% used"], ["1", "100% (only when full)"]] },
      help: "Above this, keel asks: go on, wait for the reset, use the cheaper model, or stop." },
  ] },
  { id: "git", title: "Git", sub: "Branch names, who keel's commits are by, and what happens at ship.", rows: [
    { key: "branch_pattern", label: "Branch name", kind: { t: "text", suggest: ["feat/{slug}", "{user}/{slug}", "keel/{flow}-{slug}"] },
      help: "{slug} is the flow's title, {user} is you, {flow} is the workflow." },
    { key: "web_lane_worktree", label: "Web lane in its own worktree", kind: { t: "bool", on: "yes", off: "no" }, help: "Web criteria run in a second checkout, next to the API lane." },
    { key: "commit_coauthor", label: "KeelBot as co-author", def: true, kind: { t: "bool", on: "yes", off: "no" },
      help: (v) => v === false
        ? "keel's commits name only their author."
        : "keel's commits end with Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>, so GitHub shows KeelBot next to you." },
    { key: "commit_author", label: "Commit author", def: "", kind: { t: "text", placeholder: "Ada Lovelace <ada@example.com>" },
      help: "Who keel's commits are by, as Name <email>. Empty: the project's git name; when it has none, KeelBot." },
    { key: "push_pr", label: "Push and open PR at ship", kind: { t: "select", opts: [["ask", "ask me"], ["auto", "automatic"], ["never", "never"]] },
      help: "Ask you first, do it by itself, or never. The Auto run mode never pushes." },
  ] },
  { id: "notify", title: "Notifications", sub: "What this project tells you about. Sound and pop-ups follow the bell's settings.", rows: [
    { key: "notify", label: "Notify me about this project", kind: { t: "select", opts: [["all", "all kinds"], ["needs_you", "only needs you"], ["none", "nothing"]] },
      help: "Everything, only what waits for you, or nothing." },
  ] },
  { id: "env", title: "Environment", sub: "What agents may see from this machine.", rows: [
    { key: "env_names", label: "Variables agents may see (names only)", kind: { t: "list" }, help: "Comma-separated names. Agents get these variables; every other one is hidden." },
    { key: "mcp", label: "MCP servers", kind: { t: "list" }, help: "Servers this project's agents may call. Which agent may use which is on the Tools page." },
  ] },
];

const helpOf = (row: Row, v: unknown) => (typeof row.help === "function" ? row.help(v) : row.help);

export function show(row: Row, v: unknown): string {
  if (v === undefined || v === null) return "—";
  switch (row.kind.t) {
    case "select": return row.kind.opts.find(([k]) => k === String(v))?.[1] ?? String(v);
    case "bool": return v ? row.kind.on ?? "on" : row.kind.off ?? "off";
    case "model": { const m = v as Model; return `${PROV[m.provider] ?? m.provider} ${m.model}${m.effort ? ` (${m.effort})` : ""}`; }
    case "tokens": return `${kfmt(Number(v))} tokens`;
    case "list": return (v as string[]).length ? (v as string[]).join(", ") : "none";
    default: return String(v) || "empty";
  }
}

function Control({ row, value, onSave }: { row: Row; value: unknown; onSave: (v: unknown) => void }) {
  const id = `set-${row.key}`;
  const k = row.kind;
  const [text, setText] = useState("");
  useEffect(() => {
    if (k.t === "tokens") setText(kfmt(Number(value)));
    else if (k.t === "list") setText(((value as string[]) ?? []).join(", "));
    else if (k.t === "text") setText(String(value ?? ""));
  }, [value, k.t]);
  const commit = () => {
    if (k.t === "tokens") { const n = parseTokens(text); if (n && n !== value) onSave(n); }
    else if (k.t === "list") { const l = text.split(",").map((s) => s.trim()).filter(Boolean); if (JSON.stringify(l) !== JSON.stringify(value)) onSave(l); }
    else if (text !== value) onSave(text);
  };
  const textProps = {
    type: "text", id, value: text, onChange: (e: React.ChangeEvent<HTMLInputElement>) => setText(e.target.value), onBlur: commit,
    onKeyDown: (e: React.KeyboardEvent) => e.key === "Enter" && commit(), className: "sg-input", "aria-describedby": `${id}-help`,
  };
  switch (k.t) {
    case "select":
      return (
        <select id={id} data-set={row.key} value={String(value)} aria-describedby={`${id}-help`} onChange={(e) => onSave(typeof value === "number" ? Number(e.target.value) : e.target.value)}>
          {!k.opts.some(([o]) => o === String(value)) && <option value={String(value)}>{String(value)}</option>}
          {k.opts.map(([o, l]) => <option key={o} value={o}>{l}</option>)}
        </select>
      );
    case "bool":
      return (
        <select id={id} data-set={row.key} value={value ? "1" : "0"} aria-describedby={`${id}-help`} onChange={(e) => onSave(e.target.value === "1")}>
          <option value="1">{k.on ?? "on"}</option><option value="0">{k.off ?? "off"}</option>
        </select>
      );
    case "model":
      return <ModelPicker id={id} value={value as Model} onChange={(m) => onSave(m)} />;
    case "text":
      return <><input {...textProps} placeholder={k.placeholder} list={`${id}-l`} /><datalist id={`${id}-l`}>{k.suggest?.map((s) => <option key={s} value={s} />)}</datalist></>;
    default:
      return <input {...textProps} placeholder={k.t === "list" ? "comma separated" : undefined} />;
  }
}

/** One setting: its name and what it does on the left, the control on the right; in a project, where the value comes from. */
function SettingRow({ row, value, control, source, changed }: { row: Row; value: unknown; control: ReactNode; source?: ReactNode; changed?: boolean }) {
  const id = `set-${row.key}`;
  const model = row.kind.t === "model";
  return (
    <div className={`sg-row${model ? " is-wide" : ""}${changed ? " is-changed" : ""}`} data-key={row.key}
      role={model ? "group" : undefined} aria-labelledby={model ? `${id}-lab` : undefined}>
      <div className="sg-lab">
        {model ? <span className="sg-name" id={`${id}-lab`}>{row.label}</span> : <label className="sg-name" htmlFor={id}>{row.label}</label>}
        <span className={`sg-help${row.warn?.(value) ? " is-warn" : ""}`} id={`${id}-help`}>{helpOf(row, value)}</span>
        {source && <span className="sg-src">{source}</span>}
      </div>
      <div className="sg-ctl">{control}</div>
    </div>
  );
}

type Save = { state: "idle" | "saving" | "saved" | "error"; text: string };

export function SettingsPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const [scope, setScope] = useState<"general" | "project">(pid ? "project" : "general");
  const general = useLoad("settings:general", () => api.generalSettings(), { live: false });
  const proj = useLoad(pid ? `settings:${pid}` : null, () => api.projectSettings(pid), { live: false });
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [save, setSave] = useState<Save>({ state: "idle", text: "" });
  const [only, setOnly] = useState(false);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const isProj = scope === "project" && !!pid;
  const name = project?.name ?? pid;

  const saved = (text: string) => {
    setSave({ state: "saved", text });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSave((s) => (s.state === "saved" ? { state: "idle", text: "" } : s)), 5000);
  };
  const failed = (e: unknown) => {
    const p = errorParts(e);
    setErr(p);
    setSave({ state: "error", text: `Not saved: ${p.message}` });
  };
  const saveProject = async (key: Key, v: unknown, row: Row) => {
    setErr(null);
    setSave({ state: "saving", text: "" });
    const g = proj.data?.general[key];
    const value = JSON.stringify(v) === JSON.stringify(g) ? null : v;
    try {
      const out: ProjectSettings = await api.saveProjectSettings(pid, { [key]: value });
      if (out?.effective) proj.setData(out);
      else await proj.reload();
      saved(value === null ? `Saved: ${row.label} follows General again` : `Saved: ${row.label} is ${show(row, v)} for ${name}`);
    } catch (e) {
      failed(e);
    }
  };
  const saveGeneral = async (key: Key, v: unknown, row: Row) => {
    setErr(null);
    setSave({ state: "saving", text: "" });
    try {
      const out = await api.saveGeneralSettings({ [key]: v } as Partial<Settings>);
      general.setData(out && typeof out === "object" && key in out ? out : (g) => (g ? { ...g, [key]: v } : g));
      if (pid) void proj.reload();
      saved(`Saved: ${row.label} is ${show(row, v)} for every project that does not change it`);
    } catch (e) {
      failed(e);
    }
  };

  const data = isProj ? proj : general;
  const ov = (proj.data?.overrides ?? {}) as Record<string, unknown>;
  const isChanged = (k: string) => ov[k] !== null && ov[k] !== undefined;
  const changed = isProj && proj.data ? Object.keys(ov).filter(isChanged) : [];
  const filter = isProj && only && changed.length > 0;
  const shown = SECTIONS.map((s) => ({ ...s, rows: filter ? s.rows.filter((r) => isChanged(r.key)) : s.rows })).filter((s) => s.rows.length);
  const status = save.state === "saving" ? "Saving…" : save.state === "idle" ? "Changes save as you make them" : save.text;

  return (
    <>
      <PageHead title="Settings" sub={isProj ? `Settings for ${name}. A value you do not change here comes from General.` : "General settings for every project. A project can change any of them for itself."} />
      <div className="sg-bar" role="region" aria-label="Settings scope and save state">
        <Tabs value={scope} onChange={(v) => { setScope(v); setOnly(false); }} label="Settings scope" options={pid ? [["general", "General"], ["project", name]] : [["general", "General"]]} />
        {isProj && proj.data && (
          <span className="sg-count" id="ovcount">{changed.length ? `${changed.length} setting${changed.length === 1 ? "" : "s"} changed for ${name}` : "Everything follows General"}</span>
        )}
        {isProj && changed.length > 0 && (
          <label className="chk sg-only"><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Only changed</label>
        )}
        <span className={`sg-save s-${save.state}`} role="status" aria-live="polite" data-testid="save-state">{status}</span>
      </div>
      {err && <div style={{ marginBottom: 12 }}><ErrorBox error={err} /></div>}
      {data.error ? <ErrorBox error={data.error} onRetry={() => void data.reload()} /> : (
        <div className="sg-layout">
          <div className="sg-main">
            {shown.map((sec) => (
              <section key={sec.id} className="panel sg-sec" id={`sg-${sec.id}`} aria-labelledby={`sg-${sec.id}-h`}>
                <header className="sg-sec-h">
                  <h2 id={`sg-${sec.id}-h`}>{sec.title}</h2>
                  <p>{sec.sub}</p>
                </header>
                {!data.data ? <Skeleton lines={sec.rows.length + 1} label="Loading settings" /> : sec.rows.map((row) => {
                  if (isProj) {
                    const p = proj.data!;
                    const has = isChanged(row.key);
                    const g = p.general[row.key] ?? row.def;
                    const value = (has ? ov[row.key] : p.general[row.key]) ?? row.def;
                    return (
                      <SettingRow key={row.key} row={row} value={value} changed={has}
                        control={<Control row={row} value={value} onSave={(v) => saveProject(row.key, v, row)} />}
                        source={has
                          ? <><span className="tag star">changed for {name}</span>
                            <span className="sub">General: {show(row, g)}</span>
                            <button className="btn sm ghost" type="button" aria-label={`Reset ${row.label} to General`} onClick={() => saveProject(row.key, g, row)}>Reset to General</button></>
                          : <span className="sub">from General</span>} />
                    );
                  }
                  const g = general.data!;
                  const value = g[row.key] ?? row.def;
                  return <SettingRow key={row.key} row={row} value={value} control={<Control row={row} value={value} onSave={(v) => saveGeneral(row.key, v, row)} />} />;
                })}
              </section>
            ))}
            {isProj && project && !filter && (
              <section className="panel sg-sec" aria-labelledby="sg-where-h">
                <header className="sg-sec-h"><h2 id="sg-where-h">Where these are saved</h2><p>This project's own values travel with the repo.</p></header>
                <div className="kv sg-kv">
                  <span>Folder</span><b className="mono pg-wrap">{project.root}</b>
                  <span>Branch</span><b className="mono">{project.branch}</b>
                  <span>Saved in</span><b className="mono pg-wrap">.keel/config.yml (shared with the team) + keel's data folder</b>
                </div>
              </section>
            )}
          </div>
          <nav className="sg-nav" aria-label="Settings sections">
            <span className="sg-nav-h">On this page</span>
            {shown.map((sec) => {
              const n = isProj ? sec.rows.filter((r) => isChanged(r.key)).length : 0;
              return (
                <button key={sec.id} type="button" onClick={() => document.getElementById(`sg-${sec.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}>
                  {sec.title}{n > 0 && <span className="sg-nav-n" title={`${n} changed for ${name}`}>{n}</span>}
                </button>
              );
            })}
          </nav>
        </div>
      )}
    </>
  );
}
