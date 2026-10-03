// Provider · model · effort · runs-on pickers, used by Agents and Settings.

import type { Mode, Model, Provider } from "../api";
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

export function ModelPicker({ value, onChange, id, effort = true, compact }: {
  value: Model; onChange: (m: Model) => void; id: string; effort?: boolean; compact?: boolean;
}) {
  const p = value.provider;
  return (
    <div className="row">
      <select aria-label="Provider" id={`${id}-p`} value={p} onChange={(e) => {
        const np = e.target.value as Provider;
        onChange({ ...value, provider: np, model: MODEL_SUGGESTIONS[np][0], mode: MODES_FOR[np].includes(value.mode) ? value.mode : MODES_FOR[np][0] });
      }}>
        {(Object.keys(MODEL_SUGGESTIONS) as Provider[]).map((k) => <option key={k} value={k}>{PROV[k]}</option>)}
      </select>
      <input type="text" aria-label="Model" list={`${id}-models`} value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })}
        style={{ width: compact ? 110 : 140, border: "1px solid var(--border)", background: "var(--bg)", borderRadius: 7, padding: "6px 8px" }} />
      <datalist id={`${id}-models`}>{MODEL_SUGGESTIONS[p]?.map((m) => <option key={m} value={m} />)}</datalist>
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
