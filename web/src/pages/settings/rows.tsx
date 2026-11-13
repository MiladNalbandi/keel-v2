// keel 0.18.57 one setting as a row of the Settings page: its name and what it does on the left, the control on the
// right (a segmented choice, a text field with ideas, a token count, a model), and — in one project's scope — where
// its value comes from ("changed for shop", Every project's value and Reset). The saving is useScopedSettings: Every
// project (PUT /settings/general) or one project's own (PUT /projects/<id>/settings, null = follow Every project).
// General, Models & keys and Budget all use these, so a setting looks and saves the same on every tab.

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  api,
  errorParts,
  type Model,
  type ProjectSettings,
  type Settings,
} from "../../api";
import { ModelPicker } from "../../components/ModelPicker";
import { Seg } from "../../components/settingsUi";
import { kfmt, parseTokens, PROV } from "../../format";
import type { SettingRow } from "../../sdk/slots";
import { useApp, useLoad } from "../../state";

/** A row: its key, label, control (kind), one line of help, the value before the api has one, a warning. */
export type Row = SettingRow & { key: string };

export const helpOf = (row: Row, v: unknown) =>
  typeof row.help === "function" ? row.help(v) : row.help;

/** A value in words, for "Every project: 500k tokens" and the saved line. */
export function show(row: Row, v: unknown): string {
  if (v === undefined || v === null) return "—";
  switch (row.kind.t) {
    case "select":
      return row.kind.opts.find(([k]) => k === String(v))?.[1] ?? String(v);
    case "bool":
      return v ? (row.kind.on ?? "on") : (row.kind.off ?? "off");
    case "model": {
      const m = v as Model;
      return `${PROV[m.provider] ?? m.provider} ${m.model}${m.effort ? ` (${m.effort})` : ""}`;
    }
    case "tokens":
      return `${kfmt(Number(v))} tokens`;
    case "list":
      return (v as string[]).length ? (v as string[]).join(", ") : "none";
    default:
      return String(v) || "empty";
  }
}

/** The control of one row. A choice saves at once; a text saves on Enter or when you leave the field (once). */
export function SettingControl({
  row,
  value,
  onSave,
}: {
  row: Row;
  value: unknown;
  onSave: (v: unknown) => void;
}) {
  const id = `set-${row.key}`;
  const k = row.kind;
  const [text, setText] = useState("");
  // What Enter just saved: the blur that follows (Enter, then Tab or a click) must not save the same text again.
  const sent = useRef<string | null>(null);
  useEffect(() => {
    sent.current = null;
    if (k.t === "tokens") setText(kfmt(Number(value)));
    else if (k.t === "list") setText(((value as string[]) ?? []).join(", "));
    else if (k.t === "text") setText(String(value ?? ""));
  }, [value, k.t]);
  const commit = () => {
    if (sent.current === text) return;
    const save = (v: unknown) => {
      sent.current = text;
      onSave(v);
    };
    if (k.t === "tokens") {
      const n = parseTokens(text);
      if (n && n !== value) save(n);
    } else if (k.t === "list") {
      const l = text
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (JSON.stringify(l) !== JSON.stringify(value)) save(l);
    } else if (text !== value) save(text);
  };
  const input = (
    extra: { placeholder?: string; mono?: boolean; width?: number } = {},
  ) => (
    <input
      type="text"
      id={id}
      data-set={row.key}
      className={`ss-input${extra.mono === false ? "" : " mono"}`}
      value={text}
      aria-label={row.label}
      aria-describedby={`${id}-help`}
      placeholder={extra.placeholder}
      spellCheck={false}
      style={extra.width ? { width: extra.width } : undefined}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
    />
  );
  switch (k.t) {
    case "select": {
      const opts: [string, string][] = k.opts.some(([o]) => o === String(value))
        ? k.opts
        : [[String(value), String(value)], ...k.opts];
      return (
        <Seg
          label={row.label}
          value={String(value)}
          options={opts}
          onChange={(v) => onSave(typeof value === "number" ? Number(v) : v)}
        />
      );
    }
    case "bool":
      return (
        <Seg
          label={row.label}
          value={!!value}
          options={[
            [true, k.on ?? "on"],
            [false, k.off ?? "off"],
          ]}
          onChange={(v) => onSave(v)}
        />
      );
    case "model":
      return (
        <ModelPicker
          id={id}
          value={value as Model}
          onChange={(m) => onSave(m)}
        />
      );
    case "tokens":
      return (
        <span className="ss-inline">
          {input({ width: 120 })}
          <span className="ss-meta">tokens</span>
        </span>
      );
    case "text":
      return (
        <>
          {input({ placeholder: k.placeholder })}
          {!!k.suggest?.length && (
            <span
              className="ss-ideas"
              role="group"
              aria-label={`Ideas for ${row.label}`}
            >
              {k.suggest.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => s !== value && onSave(s)}
                >
                  {s}
                </button>
              ))}
            </span>
          )}
        </>
      );
    default:
      return input({ placeholder: "comma separated" });
  }
}

/** One setting: name, help and (in a project) where the value comes from on the left; the control on the right. */
export function SettingLine({
  row,
  value,
  control,
  changedFor,
  from,
  onReset,
  fromGeneral,
}: {
  row: Row;
  value: unknown;
  control: ReactNode;
  /** the project's name when this project changes the value */
  changedFor?: string;
  /** Every project's value, in words (when changed) */
  from?: string;
  onReset?: () => void;
  /** in a project's scope, the value comes from Every project */
  fromGeneral?: boolean;
}) {
  const id = `set-${row.key}`;
  const warn = row.warn?.(value);
  return (
    <div
      className={`ss-set${warn ? " is-warn" : ""}${changedFor ? " is-changed" : ""}`}
      data-key={row.key}
      data-testid={`row-${row.key}`}
    >
      <div className="ss-set-l">
        <span className="ss-title-row">
          <b id={`${id}-lab`}>{row.label}</b>
          {changedFor && (
            <span className="ss-tag acc">changed for {changedFor}</span>
          )}
        </span>
        <span className={`ss-help${warn ? " is-warn" : ""}`} id={`${id}-help`}>
          {helpOf(row, value)}
        </span>
        {changedFor && (
          <span className="ss-set-src">
            Every project: {from} ·{" "}
            <button
              type="button"
              className="ss-btn link"
              aria-label={`Reset ${row.label} to Every project`}
              onClick={onReset}
            >
              Reset to Every project
            </button>
          </span>
        )}
        {fromGeneral && <span className="ss-set-src">from Every project</span>}
      </div>
      <div className="ss-set-c">{control}</div>
    </div>
  );
}

export type SaveState = {
  state: "idle" | "saving" | "saved" | "error";
  text: string;
};

/** Every project's settings and (with `projectId`) that project's own, and how to save either. Each save says what it
 *  did in a toast and in `save` (Saving… / Saved / Not saved). */
export function useScopedSettings(projectId: string, projectName: string) {
  const { toast } = useApp();
  const general = useLoad("settings:general", () => api.generalSettings(), {
    live: false,
  });
  const proj = useLoad(
    projectId ? `settings:${projectId}` : null,
    () => api.projectSettings(projectId),
    { live: false },
  );
  const [save, setSave] = useState<SaveState>({ state: "idle", text: "" });
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const saved = (text: string) => {
    setSave({ state: "saved", text });
    toast(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(
      () =>
        setSave((s) => (s.state === "saved" ? { state: "idle", text: "" } : s)),
      5000,
    );
  };
  const failed = (e: unknown) => {
    const p = errorParts(e);
    setErr(p);
    setSave({ state: "error", text: `Not saved: ${p.message}` });
  };
  const ov = (proj.data?.overrides ?? {}) as Record<string, unknown>;
  const isChanged = (k: string) => ov[k] !== null && ov[k] !== undefined;

  /** One project's own value; Every project's value (or null) makes it follow Every project again. */
  const saveProject = async (key: string, v: unknown, row: Row) => {
    setErr(null);
    setSave({ state: "saving", text: "" });
    const g = (proj.data?.general as Record<string, unknown> | undefined)?.[
      key
    ];
    const value =
      v === null || JSON.stringify(v) === JSON.stringify(g) ? null : v;
    try {
      const out: ProjectSettings = await api.saveProjectSettings(projectId, {
        [key]: value,
      });
      if (out?.effective) proj.setData(out);
      else await proj.reload();
      saved(
        value === null
          ? `Saved: ${row.label} follows Every project again`
          : `Saved: ${row.label} is ${show(row, v)} for ${projectName}`,
      );
    } catch (e) {
      failed(e);
    }
  };
  /** Every project's value (a project that changes it keeps its own). */
  const saveGeneral = async (
    key: string,
    v: unknown,
    row: Row,
    text?: string,
  ) => {
    setErr(null);
    setSave({ state: "saving", text: "" });
    try {
      const out = await api.saveGeneralSettings({
        [key]: v,
      } as Partial<Settings>);
      general.setData(
        out && typeof out === "object" && key in out
          ? out
          : (g) => (g ? { ...g, [key]: v } : g),
      );
      if (projectId) void proj.reload();
      saved(
        text ??
          `Saved: ${row.label} is ${show(row, v)} for every project that does not change it`,
      );
    } catch (e) {
      failed(e);
    }
  };
  return {
    general,
    proj,
    ov,
    isChanged,
    saveProject,
    saveGeneral,
    save,
    err,
    setErr,
    saved,
    failed,
  };
}
export type Scoped = ReturnType<typeof useScopedSettings>;

/** A setting of Every project on a tab without a scope switch (Models & keys, Budget): the row, and — when the chosen
 *  project changes it — that project's own value with Reset. */
export function GeneralRow({
  row,
  s,
  projectName,
  control,
}: {
  row: Row;
  s: Scoped;
  projectName: string;
  control?: (value: unknown, onSave: (v: unknown) => void) => ReactNode;
}) {
  const g = s.general.data as Record<string, unknown> | null;
  const value = (g?.[row.key] ?? row.def) as unknown;
  const onSave = (v: unknown) => void s.saveGeneral(row.key, v, row);
  const own = s.isChanged(row.key) ? s.ov[row.key] : undefined;
  return (
    <div
      className={`ss-set${row.warn?.(value) ? " is-warn" : ""}`}
      data-key={row.key}
      data-testid={`row-${row.key}`}
    >
      <div className="ss-set-l">
        <b id={`set-${row.key}-lab`}>{row.label}</b>
        <span
          className={`ss-help${row.warn?.(value) ? " is-warn" : ""}`}
          id={`set-${row.key}-help`}
        >
          {helpOf(row, value)}
        </span>
        {own !== undefined && (
          <span className="ss-set-src">
            <span className="ss-tag acc">changed for {projectName}</span>{" "}
            {projectName} uses {show(row, own)} ·{" "}
            <button
              type="button"
              className="ss-btn link"
              aria-label={`Reset ${row.label} for ${projectName} to Every project`}
              onClick={() => void s.saveProject(row.key, null, row)}
            >
              Reset to Every project
            </button>
          </span>
        )}
      </div>
      <div className="ss-set-c">
        {g ? (
          control ? (
            control(value, onSave)
          ) : (
            <SettingControl row={row} value={value} onSave={onSave} />
          )
        ) : (
          <span className="ss-meta">Loading…</span>
        )}
      </div>
    </div>
  );
}
