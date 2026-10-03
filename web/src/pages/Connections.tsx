// Connections (Control): which accounts the agents use — mode per provider, API keys (stored encrypted,
// never shown again), a test call per provider, and what is installed on this machine.

import { useState } from "react";
import { api, errorParts, type Connections, type Mode } from "../api";
import { Async, PageHead, Panel, Prov } from "../components/ui";
import { useApp, useLoad } from "../state";

const SECRET: Record<string, string> = { claude: "ANTHROPIC_API_KEY", codex: "OPENAI_API_KEY", copilot: "GITHUB_TOKEN" };
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
