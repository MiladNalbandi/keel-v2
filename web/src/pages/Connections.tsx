// Connections (Control): which accounts the agents use — mode per provider, API keys (stored encrypted,
// never shown again), a test call per provider, and what is installed on this machine. Each provider's card shows at
// once with its own "Checking…" and fills in when the check answers; a card that misses a login says so first.

import { useEffect, useRef, useState } from "react";
import { api, errorParts, type Connections, type LoginView, type Mode, type Model } from "../api";
import { defaultModel, ModelPicker, modeLabel, useCatalog } from "../components/ModelPicker";
import { EmptyState, Section, Spinner } from "../components/page";
import { Drawer, ErrorBox, PageHead, Panel, Pill, Prov, type PillTone } from "../components/ui";
import { useApp, useLoad } from "../state";
import { UsageLine } from "../components/UsageStrip";
import { JiraSection } from "../components/JiraCard";

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

const USE: Record<string, string> = { claude: "research, tests, review", codex: "planning, review", copilot: "implementer, custom agents", fake: "tests and demos — no network" };


// ---- Login helper: log the CLI in from here (inside the container), or paste a token ----
const HELP: Record<string, { name: string; here: string; paste: { steps: string[]; link?: [string, string] } }> = {
  claude: {
    name: "Claude",
    here: "keel starts `claude setup-token` inside the container. You sign in on claude.com; the page then shows a code that you paste below. keel saves the long-lived token.",
    paste: { steps: ["On your computer run `claude setup-token`.", "Copy the whole token it prints (starts with sk-ant-oat01-).", "Paste it below and press Save."] },
  },
  codex: {
    name: "GPT / Codex",
    here: "keel starts `codex login --device-auth` inside the container. You open a link, sign in with your ChatGPT account and type a short code. keel saves the login by itself.",
    paste: { steps: ["On your computer run `codex login`.", "Open ~/.codex/auth.json (for example: cat ~/.codex/auth.json | pbcopy).", "Paste the whole file below and press Save."] },
  },
  copilot: {
    name: "GitHub Copilot",
    here: "keel starts `copilot login --device-code` inside the container. You open github.com/login/device and type a short code. keel saves the GitHub token by itself.",
    paste: { steps: ["Create a fine-grained token on GitHub with the permission “Copilot Requests”. (Classic ghp_ tokens do not work.)", "Copy it.", "Paste it below and press Save."],
      link: ["https://github.com/settings/personal-access-tokens/new", "Create a token on GitHub"] },
  },
};

function copy(text: string, toast: (m: string) => void) {
  navigator.clipboard?.writeText(text).then(() => toast("Copied."), () => toast("Select the text to copy it."));
}

function LoginHelper({ p, onClose, onSaved }: { p: Connections["providers"][number]; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const h = HELP[p.id];
  const [way, setWay] = useState<"here" | "paste">("here");
  const [login, setLogin] = useState<LoginView | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);

  // poll while the login runs
  useEffect(() => {
    if (!login || ["done", "failed", "cancelled"].includes(login.status)) return;
    const t = window.setTimeout(() => {
      api.login(login.id).then((v) => live.current && setLogin(v), () => undefined);
    }, 2000);
    return () => window.clearTimeout(t);
  }, [login]);

  // when it is saved: refresh the page behind and run the test once
  useEffect(() => {
    if (login?.status !== "done") return;
    onSaved();
    if (p.selected !== "api") {
      api.testConnection(p.id).then((r) => live.current && setTest({ ok: r.ok, text: r.ok ? `${r.text ?? "OK"} · ${(r.ms / 1000).toFixed(1)}s` : (r.error ?? "no answer") }),
        (e) => live.current && setTest({ ok: false, text: errorParts(e).message }));
    }
  }, [login?.status]);

  const start = async () => {
    setBusy(true); setErr(null); setTest(null); setCode("");
    try { setLogin(await api.startLogin(p.id)); } catch (e) { setErr(errorParts(e).message); } finally { setBusy(false); }
  };
  const send = async () => {
    if (!login || !code.trim()) return;
    setBusy(true); setErr(null);
    try { setLogin(await api.loginCode(login.id, code.trim())); setCode(""); } catch (e) { setErr(errorParts(e).message); } finally { setBusy(false); }
  };
  const close = () => {
    if (login && ["starting", "waiting", "code_needed"].includes(login.status)) void api.cancelLogin(login.id).catch(() => undefined);
    onClose();
  };

  return (
    <Drawer title={`Set up ${h.name} login`} onClose={close} footer={<button className="btn" type="button" onClick={close}>Close</button>}>
      <div className="tabs" role="tablist">
        <button role="tab" type="button" aria-selected={way === "here"} onClick={() => setWay("here")}>Log in here</button>
        <button role="tab" type="button" aria-selected={way === "paste"} onClick={() => setWay("paste")}>Paste a token</button>
      </div>

      {way === "here" ? (
        <div className="grid" style={{ gap: 12 }}>
          <p className="sub" style={{ margin: 0 }}>{h.here}</p>
          {(!login || ["failed", "cancelled"].includes(login.status)) && (
            <button className="btn primary" type="button" onClick={start} disabled={busy}>{busy ? "Starting…" : login ? "Start again" : "Start login"}</button>
          )}
          {login && (
            <ol className="steps">
              {login.url && (
                <li>
                  <span>Open this page{p.id === "claude" ? " and sign in with your Claude account" : ""}:</span>
                  <div className="row">
                    <a className="btn sm primary" href={login.url} target="_blank" rel="noreferrer">Open {new URL(login.url).host}</a>
                    <button className="btn sm ghost" type="button" onClick={() => copy(login.url!, toast)}>Copy link</button>
                  </div>
                </li>
              )}
              {login.code && (
                <li>
                  <span>Type this code on that page:</span>
                  <div className="row"><b className="devcode">{login.code}</b><button className="btn sm ghost" type="button" onClick={() => copy(login.code!, toast)}>Copy code</button></div>
                </li>
              )}
              {p.id === "claude" && login.status === "code_needed" && (
                <li>
                  <label htmlFor="login-code">After you sign in, the page shows a code. Paste it here:</label>
                  <div className="row">
                    <input id="login-code" type="text" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" placeholder="code from the Claude page"
                      style={{ flex: "1 1 220px" }} onKeyDown={(e) => e.key === "Enter" && void send()} />
                    <button className="btn sm primary" type="button" onClick={send} disabled={busy || !code.trim()}>Send</button>
                  </div>
                </li>
              )}
              <li>
                <span role="status" className={login.status === "done" ? "okc" : login.status === "failed" ? "badc" : "sub"}>
                  {login.status === "done" ? `✓ ${login.message} (${login.hint ?? "saved"})` : login.status === "failed" ? `✕ ${login.message}` : `… ${login.message}`}
                </span>
                {test && <div className={test.ok ? "okc" : "badc"}>{test.ok ? "Test: " : "Test failed: "}{test.text}</div>}
              </li>
            </ol>
          )}
          {err && <span className="badc">{err}</span>}
        </div>
      ) : (
        <div className="grid" style={{ gap: 12 }}>
          <ol className="steps">{h.paste.steps.map((x) => <li key={x}>{x}</li>)}</ol>
          {h.paste.link && <a className="btn sm" href={h.paste.link[0]} target="_blank" rel="noreferrer">{h.paste.link[1]}</a>}
          {p.login_secret && (
            <SecretField id={`helper-${p.id}`} name={p.login_secret} label={LOGIN_HELP[p.id]?.label ?? "Login"} how="" set={!!p.login_set}
              hint={p.login_hint} multiline={LOGIN_HELP[p.id]?.multiline} />
          )}
        </div>
      )}
    </Drawer>
  );
}

type Conn = Connections["providers"][number];
type State = { tone: PillTone; text: string; need?: "login" | "key" | "cli" };
const CLI: Record<string, string> = { claude: "claude CLI", codex: "codex CLI", copilot: "Copilot CLI" };

/** Can this provider's agents run now, and if not, what is missing (a login, a key, the CLI)? */
export function connState(p: Conn): State {
  if (p.id === "fake") return { tone: "ok", text: "Ready, no network" };
  const m = p.modes.find((x) => x.id === p.selected);
  if (p.selected === "api") return p.key_set ? { tone: "ok", text: "API key saved" } : { tone: "warn", text: "No API key", need: "key" };
  if (m && !m.ready) return { tone: "bad", text: `${p.selected === "opencode" ? "OpenCode" : CLI[p.id] ?? "CLI"} not installed`, need: "cli" };
  if (p.login_secret && !p.login_set) return { tone: "warn", text: "No login saved", need: "login" };
  return { tone: "ok", text: p.login_set ? "Ready, logged in" : "Ready" };
}

function Provider({ p, onChanged }: { p: Conn; onChanged: () => void }) {
  const { toast } = useApp();
  const [key, setKey] = useState("");
  const [hint, setHint] = useState(p.key_hint ?? "");
  const [keySet, setKeySet] = useState(p.key_set);
  const [mode, setMode] = useState<Mode>(p.selected);
  const [test, setTest] = useState<{ ok?: boolean; text: string } | null>(null);
  const [helper, setHelper] = useState(false);
  const catalog = useCatalog();
  // The model "Use for all agents" sets: this provider, the mode picked above, a model and effort from the catalog.
  const [allModel, setAllModel] = useState<Model | null>(null);
  const forAll: Model = allModel && allModel.mode === mode ? allModel : defaultModel(catalog, p.id, mode);
  const secret = SECRET[p.id];
  const state = connState({ ...p, selected: mode, key_set: keySet });
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
    const m: Model = { ...forAll, provider: p.id, mode, model: forAll.model.trim() || defaultModel(catalog, p.id, mode).model };
    try {
      await api.saveGeneralSettings({ default_model: m, implementer_model: m, reviewer_model: m });
      toast(`All agents now use ${p.label} ${m.model}${m.effort ? ` (${m.effort})` : ""} · ${p.modes.find((x) => x.id === mode)?.label ?? modeLabel(p.id, mode)}. Change single agents in Agents.`);
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
  const focusKey = () => document.getElementById(`key-${p.id}`)?.focus();
  const head = `conn-${p.id}`;
  return (
    <article className={`panel cn-card${state.need ? " is-need" : ""}`} aria-labelledby={head} data-testid={`conn-${p.id}`}>
      {helper && <LoginHelper p={p} onClose={() => setHelper(false)} onSaved={onChanged} />}
      <header className="cn-head">
        <div className="cn-id">
          <h2 id={head}><Prov p={p.id} /></h2>
          <span className="sub">{USE[p.id] ?? p.label}</span>
        </div>
        <div className="cn-state">
          <Pill tone={state.tone}>{state.text}</Pill>
          {test && <span className="hint cn-test" role="status">{test.ok === true ? <b className="okc">Test OK</b> : test.ok === false ? <b className="badc">Test failed</b> : null} {test.text}</span>}
        </div>
        <div className="cn-actions">
          {HELP[p.id] && (
            <button className={`btn sm${state.need === "login" ? " primary" : ""}`} type="button" onClick={() => setHelper(true)}>{p.login_set ? "Log in again" : "Set up login"}</button>
          )}
          {state.need === "key" && secret && <button className="btn sm primary" type="button" onClick={focusKey}>Add the key</button>}
          <button className="btn sm" type="button" onClick={runTest}>Test</button>
        </div>
      </header>
      {state.need === "login" && <p className="cn-need">keel has no {HELP[p.id]?.name ?? p.label} login yet, so its agents cannot use your subscription. <b>Set up login</b> signs in from here, or paste a token.</p>}
      {state.need === "cli" && <p className="cn-need">The {p.modes.find((x) => x.id === mode)?.label ?? "CLI"} is not installed in keel's image. Pick another way below, or build the image with INSTALL_CLIS=1.</p>}
      <div className="cn-body">
        <fieldset className="cn-modes">
          <legend className="lab">How agents reach {HELP[p.id]?.name ?? p.label}</legend>
          {p.modes.map((m) => (
            <label key={m.id} className="radio">
              <input type="radio" name={`mode-${p.id}`} value={m.id} checked={mode === m.id} onChange={() => void pick(m.id)} />
              <span className="cn-mode"><b>{m.label}</b><span className="sub">{m.detail}</span>{m.ready ? <span className="okc cn-ready">ready</span> : <span className="hint">not ready</span>}</span>
            </label>
          ))}
        </fieldset>
        <div className="cn-side">
          {p.login_secret && LOGIN_HELP[p.id] && (
            <SecretField id={`login-${p.id}`} name={p.login_secret} label={LOGIN_HELP[p.id].label} how={LOGIN_HELP[p.id].how}
              set={!!p.login_set} hint={p.login_hint} multiline={LOGIN_HELP[p.id].multiline} />
          )}
          {secret && (
            <div className="field">
              <label htmlFor={`key-${p.id}`}>{secret}</label>
              <div className="row">
                <input type="password" id={`key-${p.id}`} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off"
                  placeholder={keySet ? `${hint || "••••"} (saved)` : "paste the key"} style={{ flex: "1 1 200px" }}
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
              <span className="hint">For the API key way. Stored encrypted; only the last characters are shown. Subscription mode removes it from the CLI's environment.</span>
            </div>
          )}
          {p.id !== "fake" && (
            <div className="field">
              <span className="lab">Model for all agents</span>
              <div className="row">
                <ModelPicker id={`all-${p.id}`} value={forAll} onChange={setAllModel} provider={false} mode={false} />
                <button className="btn sm" type="button" onClick={useForAll}>Use for all agents</button>
              </div>
              <span className="hint">Sets the default, implementer and reviewer model, on the way chosen here.</span>
            </div>
          )}
          {p.id !== "fake" && <UsageLine provider={p.id} />}
        </div>
      </div>
    </article>
  );
}

/** Known providers in the order the page shows them; a card shows at once and fills in when the check answers. */
const ORDER = ["claude", "codex", "copilot", "fake"];
const order = (a: Conn, b: Conn) => (ORDER.indexOf(a.id) + 1 || 99) - (ORDER.indexOf(b.id) + 1 || 99);

function CheckingCard({ id }: { id: string }) {
  return (
    <article className="panel cn-card is-loading" aria-busy="true" data-testid={`conn-${id}`}>
      <header className="cn-head">
        <div className="cn-id"><h2><Prov p={id} /></h2><span className="sub">{USE[id]}</span></div>
        <div className="cn-state"><Spinner>Checking</Spinner></div>
      </header>
      <div className="cn-body"><span className="pg-skel" aria-hidden="true" /><span className="pg-skel" style={{ width: "70%" }} aria-hidden="true" /></div>
    </article>
  );
}

export function ConnectionsPage(_: { pid: string }) {
  const conns = useLoad("connections", () => api.connections(), { live: false });
  const c = conns.data;
  const providers = c ? [...c.providers].sort(order) : null;
  const missing = providers?.filter((p) => connState(p).need).length ?? 0;
  return (
    <>
      <PageHead title="Connections" sub="Which accounts the agents use, and what is installed. Keys are encrypted and never shown again in full." />
      {conns.error && <div style={{ marginBottom: 16 }}><ErrorBox error={conns.error} onRetry={() => void conns.reload()} /></div>}
      <Section title="Providers" sub={!providers ? "Checking each provider: which CLIs are installed and which logins and keys are saved."
        : missing ? `${missing} provider${missing === 1 ? " needs" : "s need"} a login or a key before its agents can run.` : "Every provider is ready."}>
        <div className="cn-list">
          {providers ? providers.map((p) => <Provider key={p.id} p={p} onChanged={() => void conns.reload()} />)
            : !conns.error && ORDER.map((id) => <CheckingCard key={id} id={id} />)}
          {providers && !providers.length && <div className="panel"><EmptyState title="No provider known">The keel api lists none. Update keel and reload.</EmptyState></div>}
        </div>
      </Section>
      <JiraSection />
      <Section title="This machine" sub="Programs keel and its agents find in the container.">
        <Panel body="checks">
          {c ? c.machine.map((m) => (
            <span key={m.name} className="check">
              <span className={m.ok ? "ok" : "no"}>{m.ok ? "✓" : "!"}</span>{m.name}
              {m.version && <> <span className="mono sub">{m.version}</span></>}
              {!m.ok && !m.version && <> <span className="mono sub">not installed</span></>}
            </span>
          )) : !conns.error ? <Spinner>Checking what is installed</Spinner> : <span className="sub">Not checked.</span>}
        </Panel>
      </Section>
    </>
  );
}
