// Connections (Control): which accounts the agents use — mode per provider, API keys (stored encrypted,
// never shown again), a test call per provider, and what is installed on this machine.

import { useState } from "react";
import { api, errorParts, type Connections, type Mode } from "../api";
import { Async, PageHead, Panel, Prov } from "../components/ui";
import { useApp, useLoad } from "../state";

const SECRET: Record<string, string> = { claude: "ANTHROPIC_API_KEY", codex: "OPENAI_API_KEY", copilot: "GITHUB_TOKEN" };
// How to get each CLI login; it is stored encrypted in keel's database and handed to the CLI inside the container.
const LOGIN_HELP: Record<string, { label: string; how: string; multiline?: boolean }> = {
  claude: { label: "Claude login token", how: "On your computer run `claude setup-token` and paste the token it prints." },
  codex: { label: "Codex login (auth.json)", how: "On your computer run `codex login`, then paste the content of ~/.codex/auth.json.", multiline: true },
  copilot: { label: "GitHub token for Copilot", how: "A GitHub token with Copilot access (github.com › Settings › Developer settings › Tokens)." },
};

/** One secret field: paste, save (encrypted), remove. Shows only the hint once saved. */
function SecretField({ id, name, label, how, set, hint, multiline }: { id: string; name: string; label: string; how: string; set: boolean; hint?: string | null; multiline?: boolean }) {
  const { toast } = useApp();
  const [value, setValue] = useState("");
  const [isSet, setIsSet] = useState(set);
  const [shown, setShown] = useState(hint ?? "");
  const save = async () => {
    if (!value.trim()) return;
    try {
      const r = await api.setSecret(name, value.trim());
      setValue("");
      setShown(r?.hint ?? "");
      setIsSet(true);
      toast("Saved and encrypted.");
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const remove = async () => {
    try {
      await api.deleteSecret(name);
      setIsSet(false);
      setShown("");
      toast("Removed.");
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const placeholder = isSet ? `${shown || "••••"} (saved)` : multiline ? "paste here" : "paste the token";
  return (
    <div className="field">
      <label htmlFor={id}>{label} <span className="mono sub">{name}</span></label>
      <div className="row">
        {multiline
          ? <textarea id={id} value={value} onChange={(e) => setValue(e.target.value)} placeholder={placeholder} rows={3} style={{ flex: "1 1 220px", minHeight: 0 }} />
          : <input type="password" id={id} value={value} onChange={(e) => setValue(e.target.value)} autoComplete="off" placeholder={placeholder}
              style={{ flex: "1 1 220px" }} onKeyDown={(e) => e.key === "Enter" && void save()} />}
        <button className="btn sm" type="button" onClick={save} disabled={!value.trim()}>Save</button>
        {isSet && <button className="btn sm ghost" type="button" onClick={remove}>Remove</button>}
      </div>
      <span className="hint">{how} Stored encrypted in keel's database; only the last characters are shown.</span>
    </div>
  );
}

const DEFAULT_MODEL: Record<string, string> = { claude: "sonnet", codex: "gpt-5", copilot: "gpt-5", fake: "fake" };
const USE: Record<string, string> = { claude: "research, tests, review", codex: "planning, review", copilot: "implementer, custom agents", fake: "tests and demos — no network" };

function Provider({ p, onChanged }: { p: Connections["providers"][number]; onChanged: () => void }) {
  const { toast } = useApp();
  const [key, setKey] = useState("");
  const [hint, setHint] = useState(p.key_hint ?? "");
  const [keySet, setKeySet] = useState(p.key_set);
  const [mode, setMode] = useState<Mode>(p.selected);
  const [test, setTest] = useState<{ ok?: boolean; text: string } | null>(null);
  const secret = SECRET[p.id];
  const pick = async (m: Mode) => {
    const old = mode;
    setMode(m);
    try {
      await api.setMode(p.id, m);
      toast(`${p.label}: ${p.modes.find((x) => x.id === m)?.label ?? m}`);
      onChanged();
    } catch (e) {
      setMode(old);
      toast(errorParts(e).message);
    }
  };
  const saveKey = async () => {
    if (!key.trim() || !secret) return;
    try {
      const r = await api.setSecret(secret, key.trim());
      setKey("");
      setHint(r?.hint ?? "");
      setKeySet(true);
      toast("Saved and encrypted.");
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const useForAll = async () => {
    const name = DEFAULT_MODEL[p.id] ?? "default";
    const m = { provider: p.id, mode, model: name };
    try {
      await api.saveGeneralSettings({ default_model: m, implementer_model: m, reviewer_model: m });
      toast(`All agents now use ${p.label} (${p.modes.find((x) => x.id === mode)?.label ?? mode}). Change single agents in Agents.`);
      onChanged();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const runTest = async () => {
    setTest({ text: "sending “Reply with exactly: OK”…" });
    try {
      const r = await api.testConnection(p.id);
      setTest(r.ok ? { ok: true, text: `${r.text ?? "OK"} · ${(r.ms / 1000).toFixed(1)}s` } : { ok: false, text: r.error ?? "no answer" });
    } catch (e) {
      const er = errorParts(e);
      setTest({ ok: false, text: er.hint ? `${er.message} ${er.hint}` : er.message });
    }
  };
  return (
    <div className="conn">
      <div><h3><Prov p={p.id} /></h3><span className="sub">{USE[p.id] ?? p.label}</span></div>
      <div className="modes">
        {p.modes.map((m) => (
          <label key={m.id} className="radio">
            <input type="radio" name={`mode-${p.id}`} value={m.id} checked={mode === m.id} onChange={() => void pick(m.id)} />
            <span><b>{m.label}</b> — {m.detail} {m.ready ? <span className="amber" style={{ color: "var(--ok)" }}>✓</span> : <span className="hint">not ready</span>}</span>
          </label>
        ))}
        {p.login_secret && LOGIN_HELP[p.id] && (
          <SecretField id={`login-${p.id}`} name={p.login_secret} label={LOGIN_HELP[p.id].label} how={LOGIN_HELP[p.id].how}
            set={!!p.login_set} hint={p.login_hint} multiline={LOGIN_HELP[p.id].multiline} />
        )}
        {secret && (
          <div className="field">
            <label htmlFor={`key-${p.id}`}>{secret}</label>
            <div className="row">
              <input type="password" id={`key-${p.id}`} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off"
                placeholder={keySet ? `${hint || "••••"} (saved)` : "paste the key"} style={{ flex: "1 1 220px" }}
                onKeyDown={(e) => e.key === "Enter" && void saveKey()} />
              <button className="btn sm" type="button" onClick={saveKey} disabled={!key.trim()}>Save</button>
              {keySet && <button className="btn sm ghost" type="button" onClick={async () => {
                try {
                  await api.deleteSecret(secret);
                  setKeySet(false);
                  setHint("");
                  toast("Key removed.");
                } catch (e) {
                  toast(errorParts(e).message);
                }
              }}>Remove</button>}
            </div>
            <span className="hint">Stored encrypted; only the last characters are shown. Subscription mode removes it from the CLI's environment.</span>
          </div>
        )}
        <div className="row">
          <button className="btn sm" type="button" onClick={runTest}>Test</button>
          {p.id !== "fake" && <button className="btn sm primary" type="button" onClick={useForAll}>Use for all agents</button>}
          {test && <span className="hint" role="status">{test.ok === true ? <b style={{ color: "var(--ok)" }}>OK</b> : test.ok === false ? <b style={{ color: "var(--bad)" }}>Failed</b> : null} {test.text}</span>}
        </div>
      </div>
    </div>
  );
}

export function ConnectionsPage(_: { pid: string }) {
  const conns = useLoad("connections", () => api.connections(), { live: false });
  return (
    <>
      <PageHead title="Connections" sub="Which accounts the agents use. Keys are encrypted and never shown again in full." />
      <Async r={conns} what="Checking connections">
        {(c) => (
          <>
            <div className="panel">
              {c.providers.map((p) => <Provider key={p.id} p={p} onChanged={() => void conns.reload()} />)}
              {!c.providers.length && <div className="panel-body empty">No provider known.</div>}
            </div>
            <Panel title="This machine" body="checks" style={{ marginTop: 16 }}>
              {c.machine.map((m) => (
                <span key={m.name} className="check">
                  <span className={m.ok ? "ok" : "no"}>{m.ok ? "✓" : "!"}</span>{m.name}
                  {m.version && <> <span className="mono sub">{m.version}</span></>}
                  {!m.ok && !m.version && <> <span className="mono sub">not installed</span></>}
                </span>
              ))}
            </Panel>
          </>
        )}
      </Async>
    </>
  );
}
