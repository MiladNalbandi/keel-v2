// Settings (Control): General settings for every project, and per-project overrides. In the project tab
// each row says "from General" or "this project"; "Use general" removes the override (PUT { key: null }).

import { Fragment, useEffect, useState, type ReactNode } from "react";
import { api, errorParts, type Model, type ProjectSettings, type Settings } from "../api";
import { ModelPicker } from "../components/ModelPicker";
import { ErrorBox, Loading, PageHead, Panel, Tabs } from "../components/ui";
import { kfmt, parseTokens, PROV } from "../format";
import { useApp, useLoad } from "../state";

type Key = keyof Settings;
type Kind =
  | { t: "select"; opts: [string, string][] }
  | { t: "bool"; on?: string; off?: string }
  | { t: "model" }
  | { t: "tokens" }
  | { t: "text"; suggest?: string[] }
  | { t: "list" };
type Row = { key: Key; label: string; kind: Kind };

export const SECTIONS: [string, Row[]][] = [
  ["Flow and gates", [
    { key: "gates_mode", label: "Gate mode", kind: { t: "select", opts: [["every-ac", "every AC"], ["end-of-lane", "end of lane"], ["end", "end of flow"]] } },
    { key: "keel_rules", label: "keel rules", kind: { t: "bool" } },
    { key: "fix_attempts", label: "Fix attempts per ladder rung", kind: { t: "select", opts: [["1", "1"], ["3", "3"], ["5", "5"]] } },
    { key: "coverage_min", label: "Coverage needed before push", kind: { t: "select", opts: [["0", "off"], ["70", "70%"], ["80", "80%"], ["90", "90%"]] } },
  ]],
  ["Models", [
    { key: "default_model", label: "Default model", kind: { t: "model" } },
    { key: "implementer_model", label: "implementer model", kind: { t: "model" } },
    { key: "reviewer_model", label: "Reviewers model", kind: { t: "model" } },
    { key: "cheaper_model", label: "Cheaper model when a cap is near", kind: { t: "model" } },
  ]],
  ["Budget", [
    { key: "cap_tokens", label: "Cap per flow (tokens)", kind: { t: "tokens" } },
    { key: "on_cap", label: "When a cap is hit", kind: { t: "select", opts: [["pause", "pause and ask me"], ["cheaper", "switch to cheaper model"], ["stop", "stop"]] } },
  ]],
  ["Git", [
    { key: "branch_pattern", label: "Branch name", kind: { t: "text", suggest: ["feat/{slug}", "{user}/{slug}", "keel/{flow}-{slug}"] } },
    { key: "web_lane_worktree", label: "Web lane in its own worktree", kind: { t: "bool", on: "yes", off: "no" } },
    { key: "push_pr", label: "Push and open PR at ship", kind: { t: "select", opts: [["ask", "ask me"], ["auto", "automatic"], ["never", "never"]] } },
  ]],
  ["Notifications", [
    { key: "notify", label: "Notify me about this project", kind: { t: "select", opts: [["all", "all kinds"], ["needs_you", "only needs you"], ["none", "nothing"]] } },
  ]],
  ["Environment", [
    { key: "env_names", label: "Variables agents may see (names only)", kind: { t: "list" } },
    { key: "mcp", label: "MCP servers", kind: { t: "list" } },
  ]],
];

export function show(row: Row, v: unknown): string {
  if (v === undefined || v === null) return "—";
  switch (row.kind.t) {
    case "select": return row.kind.opts.find(([k]) => k === String(v))?.[1] ?? String(v);
    case "bool": return v ? row.kind.on ?? "on" : row.kind.off ?? "off";
    case "model": { const m = v as Model; return `${PROV[m.provider] ?? m.provider} ${m.model}`; }
    case "tokens": return `${kfmt(Number(v))} tokens`;
    case "list": return (v as string[]).length ? (v as string[]).join(", ") : "none";
    default: return String(v);
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
    onKeyDown: (e: React.KeyboardEvent) => e.key === "Enter" && commit(),
    style: { border: "1px solid var(--border)", background: "var(--bg)", borderRadius: 7, padding: "5px 8px", width: "100%" },
  };
  switch (k.t) {
    case "select":
      return (
        <select id={id} data-set={row.key} value={String(value)} onChange={(e) => onSave(typeof value === "number" ? Number(e.target.value) : e.target.value)}>
          {!k.opts.some(([o]) => o === String(value)) && <option value={String(value)}>{String(value)}</option>}
          {k.opts.map(([o, l]) => <option key={o} value={o}>{l}</option>)}
        </select>
      );
    case "bool":
      return (
        <select id={id} data-set={row.key} value={value ? "1" : "0"} onChange={(e) => onSave(e.target.value === "1")}>
          <option value="1">{k.on ?? "on"}</option><option value="0">{k.off ?? "off"}</option>
        </select>
      );
    case "model":
      return <ModelPicker id={id} value={value as Model} onChange={(m) => onSave(m)} effort={false} />;
    case "text":
      return <><input {...textProps} list={`${id}-l`} /><datalist id={`${id}-l`}>{k.suggest?.map((s) => <option key={s} value={s} />)}</datalist></>;
    default:
      return <input {...textProps} placeholder={k.t === "list" ? "comma separated" : undefined} />;
  }
}

function SettingRow({ row, value, source, wide }: { row: Row; value: unknown; source: ReactNode; wide?: boolean; }) {
  return (
    <div className="setrow" style={wide ? { gridTemplateColumns: "minmax(0,1fr)" } : undefined}>
      <label htmlFor={`set-${row.key}`}>{row.label}</label>
      {value as ReactNode}
      <span className="setsrc">{source}</span>
    </div>
  );
}

export function SettingsPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const [scope, setScope] = useState<"general" | "project">(pid ? "project" : "general");
  const general = useLoad("settings:general", () => api.generalSettings(), { live: false });
  const proj = useLoad(pid ? `settings:${pid}` : null, () => api.projectSettings(pid), { live: false });
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const isProj = scope === "project" && !!pid;
  const name = project?.name ?? pid;

  const saveProject = async (key: Key, v: unknown, label: string, row: Row) => {
    setErr(null);
    const g = proj.data?.general[key];
    const value = JSON.stringify(v) === JSON.stringify(g) ? null : v;
    try {
      const out: ProjectSettings = await api.saveProjectSettings(pid, { [key]: value });
      if (out?.effective) proj.setData(out);
      else await proj.reload();
      toast(value === null ? `${label} follows General again` : `${label}: ${show(row, v)} for ${name}`);
    } catch (e) {
      setErr(errorParts(e));
    }
  };
  const saveGeneral = async (key: Key, v: unknown, label: string, row: Row) => {
    setErr(null);
    try {
      const out = await api.saveGeneralSettings({ [key]: v } as Partial<Settings>);
      general.setData(out && typeof out === "object" && key in out ? out : (g) => (g ? { ...g, [key]: v } : g));
      if (pid) void proj.reload();
      toast(`${label}: ${show(row, v)} for all projects that do not override it`);
    } catch (e) {
      setErr(errorParts(e));
    }
  };

  const data = isProj ? proj : general;
  const overrides = isProj && proj.data ? Object.keys(proj.data.overrides ?? {}).filter((k) => (proj.data!.overrides as Record<string, unknown>)[k] !== null && (proj.data!.overrides as Record<string, unknown>)[k] !== undefined) : [];

  return (
    <>
      <PageHead title="Settings" sub={isProj ? `Settings for ${name}. Anything you do not change here comes from General.` : "General settings for every project. A project can override any of them."} />
      <div className="row" style={{ marginBottom: 12, justifyContent: "space-between" }}>
        <Tabs value={scope} onChange={setScope} label="Settings scope" options={pid ? [["general", "General"], ["project", name]] : [["general", "General"]]} />
        {isProj && proj.data && <span className="hint" id="ovcount">{overrides.length} setting{overrides.length === 1 ? "" : "s"} differ from General</span>}
      </div>
      {err && <div style={{ marginBottom: 12 }}><ErrorBox error={err} /></div>}
      {data.error ? <ErrorBox error={data.error} onRetry={() => void data.reload()} /> : !data.data ? <Loading what="Loading settings" /> : (
        <div className="grid g2 setgrid">
          {SECTIONS.map(([title, rows]) => (
            <Panel key={title} title={title} body="grid" style={{ alignSelf: "start" }}>
              <div className="grid" style={{ gap: 0 }}>
                {rows.map((row) => {
                  const wide = row.kind.t === "model";
                  if (isProj) {
                    const p = proj.data!;
                    const ov = (p.overrides as Record<string, unknown>)[row.key];
                    const has = ov !== undefined && ov !== null;
                    const value = has ? ov : p.general[row.key];
                    return (
                      <SettingRow key={row.key} row={row} wide={wide}
                        value={<Control row={row} value={value} onSave={(v) => saveProject(row.key, v, row.label, row)} />}
                        source={has
                          ? <><span className="tag star">this project</span><button className="btn sm ghost" type="button" aria-label={`Use general for ${row.label}`} onClick={() => saveProject(row.key, p.general[row.key], row.label, row)}>Use general</button></>
                          : <span className="sub">from General: {show(row, p.general[row.key])}</span>} />
                    );
                  }
                  const g = general.data!;
                  return (
                    <SettingRow key={row.key} row={row} wide={wide}
                      value={<Control row={row} value={g[row.key]} onSave={(v) => saveGeneral(row.key, v, row.label, row)} />} source={null} />
                  );
                })}
              </div>
            </Panel>
          ))}
        </div>
      )}
      {isProj && project && (
        <Panel title="Project" body="kv" style={{ marginTop: 16 }}>
          <Fragment>
            <span>Folder</span><b className="mono">{project.root}</b>
            <span>Branch</span><b className="mono">{project.branch}</b>
            <span>Saved in</span><b className="mono">.keel/config.yml (shared with the team) + keel's data folder</b>
          </Fragment>
        </Panel>
      )}
    </>
  );
}
