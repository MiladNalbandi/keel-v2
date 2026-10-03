// Provider · model · effort · runs-on pickers, used by Agents and Settings.

import { useEffect, useState } from "react";
import { api, type Mode, type Model, type Provider, type ProviderModels } from "../api";
import { MODE_LABEL, PROV } from "../format";

export const MODEL_SUGGESTIONS: Record<Provider, string[]> = {
  claude: ["opus", "sonnet", "haiku"],
  codex: ["gpt-5.6-sol", "gpt-5", "gpt-5-mini"],
  copilot: ["gpt-5", "claude-sonnet", "gpt-5-mini"],
  fake: ["fake"],
};
export const MODES_FOR: Record<Provider, Mode[]> = {
  claude: ["subscription", "api"],
  codex: ["subscription", "api"],
  copilot: ["subscription", "opencode", "api"],
  fake: ["api"],
};
const MODE_COPILOT: Record<string, string> = { subscription: "Copilot CLI", opencode: "OpenCode", api: "API key (GitHub Models)" };
export const modeLabel = (p: Provider | string, m: Mode | string) => (p === "copilot" ? MODE_COPILOT[m] : MODE_LABEL[m]) ?? m;

// One request per page load: GET /api/providers/models (proxied from the engine). If it fails, the
// built-in suggestions above stay, and the model field is free text either way.
let modelsPromise: Promise<ProviderModels> | null = null;
let modelsCache: ProviderModels | null = null;
export function resetProviderModels() {
  modelsPromise = null;
  modelsCache = null;
}

export function useProviderModels(): ProviderModels | null {
  const [m, setM] = useState<ProviderModels | null>(modelsCache);
  useEffect(() => {
    if (modelsCache) return;
    let live = true;
    modelsPromise ??= api.providerModels().then((r) => {
      modelsCache = r && typeof r === "object" ? r : {};
      return modelsCache;
    }, (e) => {
      modelsPromise = null;
      throw e;
    });
    modelsPromise.then((r) => live && setM(r), () => undefined);
    return () => {
      live = false;
    };
  }, []);
  return m;
}

/** Model options for one provider: from the engine when it answered, else the built-in suggestions. */
export function modelOptions(models: ProviderModels | null, p: Provider): { id: string; label: string }[] {
  const list = models?.[p];
  if (list?.length) return list.map((x) => ({ id: x.id, label: x.label || x.id }));
  return (MODEL_SUGGESTIONS[p] ?? []).map((id) => ({ id, label: id }));
}

export function ModelPicker({ value, onChange, id, effort = true, compact }: {
  value: Model; onChange: (m: Model) => void; id: string; effort?: boolean; compact?: boolean;
}) {
  const p = value.provider;
  const models = useProviderModels();
  const opts = modelOptions(models, p);
  return (
    <div className="row">
      <select aria-label="Provider" id={`${id}-p`} value={p} onChange={(e) => {
        const np = e.target.value as Provider;
        onChange({ ...value, provider: np, model: modelOptions(models, np)[0]?.id ?? "", mode: MODES_FOR[np].includes(value.mode) ? value.mode : MODES_FOR[np][0] });
      }}>
        {(Object.keys(MODEL_SUGGESTIONS) as Provider[]).map((k) => <option key={k} value={k}>{PROV[k]}</option>)}
      </select>
      <input type="text" aria-label="Model" list={`${id}-models`} value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })}
        style={{ width: compact ? 110 : 140, border: "1px solid var(--border)", background: "var(--bg)", borderRadius: 7, padding: "6px 8px" }} />
      <datalist id={`${id}-models`} data-testid={`${id}-models`}>{opts.map((m) => <option key={m.id} value={m.id} label={m.label !== m.id ? m.label : undefined} />)}</datalist>
      {effort && (
        <select aria-label="Effort" value={value.effort ?? ""} onChange={(e) => onChange({ ...value, effort: e.target.value || undefined })}>
          <option value="">effort: default</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="max">max</option>
        </select>
      )}
      {!compact && (
        <select aria-label="Runs on" value={value.mode} onChange={(e) => onChange({ ...value, mode: e.target.value as Mode })}>
          {MODES_FOR[p]?.map((m) => <option key={m} value={m}>{modeLabel(p, m)}</option>)}
        </select>
      )}
    </div>
  );
}
