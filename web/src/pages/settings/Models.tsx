// keel 0.18.57 Settings › Models & keys: which API keys the agents use, which model each kind of agent runs on, and
// what is installed. #101 keel runs every model in its own harness, on the provider's API: no CLI, no subscription
// login. One row per provider (Claude, GPT / Codex, GitHub Copilot, Vertex AI, Local models, Fake): its state ("API key
// saved", "No API key"), Add the key / Set up, and Test; open, it shows how agents reach it (the provider's API), its
// key (stored encrypted, only the last characters shown: add, change, remove), the model servers or the Google Cloud
// project, and "Use for all agents". GitHub Copilot runs on GitHub Models: a GitHub token works, and so does the token
// of Connections › GitHub's "Sign in with GitHub". Then the model of each kind of agent with its effort, "Local models
// only", and This machine (the programs keel finds).

import { useEffect, useState } from "react";
import { api, errorParts, type Connections, type Model } from "../../api";
import { LocalServers, VertexSettings } from "../../components/ModelServers";
import {
  defaultModel,
  effortsOf,
  ModelPicker,
  modeLabel,
  useCatalog,
} from "../../components/ModelPicker";
import { EmptyState, Spinner } from "../../components/page";
import { Note, Seg, Tile, TonePill, type Tone } from "../../components/settingsUi";
import { ErrorBox } from "../../components/ui";
import { useStored } from "../../storage";
import { useApp, useLoad } from "../../state";
import { SettingsFrame, setSettingsHint } from "./frame";
import { GeneralRow, useScopedSettings, type Row } from "./rows";

/** The secret that holds each provider's API key (GitHub Models: a GitHub token). */
const SECRET: Record<string, string> = {
  claude: "ANTHROPIC_API_KEY",
  codex: "OPENAI_API_KEY",
  copilot: "GITHUB_TOKEN",
};
/** Where each key comes from. */
const KEY_HELP: Record<string, { label: string; how: string }> = {
  claude: { label: "Anthropic API key", how: "Create one at console.anthropic.com › API keys." },
  codex: { label: "OpenAI API key", how: "Create one at platform.openai.com › API keys." },
  copilot: {
    label: "GitHub token for GitHub Models",
    how: "A GitHub token that may use GitHub Models (github.com › Settings › Developer settings › Tokens). Without one, keel uses the token of Connections › GitHub's “Sign in with GitHub”.",
  },
};

const USE: Record<string, string> = {
  claude: "research, tests, review",
  codex: "planning, review",
  copilot: "implementer, custom agents",
  vertex: "Claude and Gemini in your Google Cloud project",
  local: "Ollama, LM Studio, vLLM, llama.cpp — the code stays on your machines",
  fake: "tests and demos — no network",
};
/** 0.18.46 providers set up with a form in their row (servers, a project), not with one key */
const CONFIGURED = new Set(["vertex", "local"]);
const NAME: Record<string, string> = {
  claude: "Claude",
  codex: "GPT / Codex",
  copilot: "GitHub Copilot",
  vertex: "Vertex AI",
  local: "Local models",
  fake: "Fake",
};

type Conn = Connections["providers"][number];
const PILL_TONE: Record<string, Tone> = { ok: "ok", warn: "warn", bad: "bad" };

/** GitHub Models is ready with the token of Connections › GitHub's sign-in when no GITHUB_TOKEN is saved. */
const fromSignIn = (p: Pick<Conn, "id" | "key_set" | "key_hint">) =>
  p.id === "copilot" && p.key_set && /github sign-in/i.test(p.key_hint ?? "");

/** Can this provider's agents run now, and if not, what is missing (its key, or its setup)? */
export function connState(p: Conn): {
  tone: "ok" | "warn" | "bad";
  text: string;
  need?: "key";
} {
  if (p.id === "fake") return { tone: "ok", text: "Ready, no network" };
  if (CONFIGURED.has(p.id))
    return p.key_set
      ? {
          tone: "ok",
          text: p.id === "local" ? "Ready, on your machines" : "Ready",
        }
      : { tone: "warn", text: "Not set up", need: "key" };
  if (fromSignIn(p)) return { tone: "ok", text: "Ready, GitHub sign-in" };
  return p.key_set
    ? { tone: "ok", text: "API key saved" }
    : {
        tone: "warn",
        text: p.id === "copilot" ? "No GitHub token" : "No API key",
        need: "key",
      };
}

/** B8a the agents' models that are still the fake model (default, implementer, reviewer), in words. */
const AGENT_MODELS = [
  ["default_model", "the default model"],
  ["implementer_model", "the implementer"],
  ["reviewer_model", "the reviewer"],
] as const;
function fakeAgents(
  s: Partial<Record<string, unknown>> | null | undefined,
): string[] {
  if (!s) return [];
  return AGENT_MODELS.filter(
    ([k]) => (s[k] as Model | undefined)?.provider === "fake",
  ).map(([, words]) => words);
}

/** One secret: paste, Save (encrypted), Remove. Shows only the last characters once saved; a refused one says why. */
function SecretField({
  id,
  name,
  label,
  how,
  set,
  hint,
  onSaved,
  onRemoved,
}: {
  id: string;
  name: string;
  label: string;
  how: string;
  set: boolean;
  hint?: string | null;
  onSaved?: (hint: string) => void;
  onRemoved?: () => void;
}) {
  const { toast } = useApp();
  const [value, setValue] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (!value.trim()) return;
    setErr(null);
    try {
      const r = await api.setSecret(name, value.trim());
      setValue("");
      toast("Saved and encrypted.");
      onSaved?.(r?.hint ?? "");
    } catch (e) {
      const er = errorParts(e); // a refused key says why and how to copy it again
      setErr(er.hint ? `${er.message} ${er.hint}` : er.message);
    }
  };
  const remove = async () => {
    try {
      await api.deleteSecret(name);
      toast("Key removed.");
      onRemoved?.();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  return (
    <div className="ss-label">
      <label htmlFor={id}>
        {label} <span className="ss-mono ss-meta">{name}</span>
      </label>
      <span className="ss-actions">
        <input
          type="password"
          id={id}
          className="ss-input mono"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
          placeholder={set ? `${hint || "••••"} (saved)` : "paste the key"}
          aria-invalid={!!err}
          style={{ flex: "1 1 200px" }}
          onKeyDown={(e) => e.key === "Enter" && void save()}
        />
        <button
          className="ss-btn sm"
          type="button"
          onClick={save}
          disabled={!value.trim()}
          style={{ minHeight: 40 }}
        >
          {set ? "Change" : "Save"}
        </button>
        {set && (
          <button
            className="ss-btn sm q"
            type="button"
            onClick={remove}
            style={{ minHeight: 40 }}
          >
            Remove
          </button>
        )}
      </span>
      {err && (
        <Note tone="err" role="alert">
          {err}
        </Note>
      )}
      <span className="ss-help">
        {how ? `${how} ` : ""}Stored encrypted in keel's database; only the last
        characters are shown.
      </span>
    </div>
  );
}

function ProviderRow({
  p,
  open,
  onToggle,
  onChanged,
  fake = [],
}: {
  p: Conn;
  open: boolean;
  onToggle: (open: boolean) => void;
  onChanged: () => void;
  fake?: string[];
}) {
  const { toast } = useApp();
  const [hint, setHint] = useState(p.key_hint ?? "");
  const [keySet, setKeySet] = useState(p.key_set);
  const [test, setTest] = useState<{ ok?: boolean; text: string } | null>(null);
  const catalog = useCatalog();
  useEffect(() => {
    setKeySet(p.key_set);
    setHint(p.key_hint ?? "");
  }, [p.key_set, p.key_hint]);
  // The model "Use for all agents" sets: this provider, a model and effort from the catalog.
  const [allModel, setAllModel] = useState<Model | null>(null);
  const forAll: Model = allModel ?? defaultModel(catalog, p.id, "api");
  const secret = SECRET[p.id];
  const now = { ...p, key_set: keySet, key_hint: hint };
  const state = connState(now);
  const signIn = fromSignIn(now);
  const name = NAME[p.id] ?? p.label;
  // #101 one way only: the provider's API (copilot: GitHub Models; vertex: Google Cloud; local: the model servers)
  const way = p.modes.find((m) => m.id === "api") ?? p.modes[0];
  const wayLabel = way?.label ?? modeLabel(p.id);
  const useForAll = async () => {
    const m: Model = {
      ...forAll,
      provider: p.id,
      mode: "api",
      model: forAll.model.trim() || defaultModel(catalog, p.id, "api").model,
    };
    try {
      await api.saveGeneralSettings({
        default_model: m,
        implementer_model: m,
        reviewer_model: m,
      });
      toast(
        `All agents now use ${p.label} ${m.model}${m.effort ? ` (${m.effort})` : ""} · ${wayLabel}. Change single agents in Agents.`,
      );
      onChanged();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const runTest = async () => {
    setTest({ text: "sending “Reply with exactly: OK”…" });
    try {
      const r = await api.testConnection(p.id);
      setTest(
        r.ok
          ? {
              ok: true,
              text: `${r.text ?? "OK"} · ${(r.ms / 1000).toFixed(1)}s`,
            }
          : { ok: false, text: r.error ?? "no answer" },
      );
    } catch (e) {
      const er = errorParts(e);
      setTest({
        ok: false,
        text: er.hint ? `${er.message} ${er.hint}` : er.message,
      });
    }
  };
  // "Add the key" / "Set up": open the row and put the cursor where the key or the setup goes
  const focusIn = (sel: string) => {
    onToggle(true);
    window.setTimeout(
      () => document.querySelector<HTMLElement>(sel)?.focus(),
      0,
    );
  };
  const head = `conn-${p.id}`;
  const keyAct =
    state.need === "key" && secret
      ? "Add the key"
      : state.need === "key" && CONFIGURED.has(p.id)
        ? "Set up"
        : null;
  return (
    <div
      className={`ss-prow${open ? " open" : ""}`}
      data-testid={`conn-${p.id}`}
      role="group"
      aria-labelledby={head}
    >
      <div className="ss-row0">
        <Tile>{name[0]}</Tile>
        <button
          type="button"
          className="ss-pname"
          aria-expanded={open}
          aria-controls={`${head}-b`}
          onClick={() => onToggle(!open)}
        >
          <b id={head}>{name}</b>
          <span className="ss-meta">{USE[p.id] ?? p.label}</span>
        </button>
        <span className="ss-pstate">
          <TonePill tone={PILL_TONE[state.tone] ?? "off"}>
            {state.text}
          </TonePill>
          {test && (
            <span role="status" className="ss-small" style={{ fontSize: 13 }}>
              {test.ok === true ? (
                <b style={{ color: "var(--ok)", fontWeight: 600 }}>Test OK</b>
              ) : test.ok === false ? (
                <b style={{ color: "var(--bad)", fontWeight: 600 }}>
                  Test failed
                </b>
              ) : null}{" "}
              <span className="ss-meta">{test.text}</span>
            </span>
          )}
        </span>
        <span className="ss-push">
          {keyAct && (
            <button
              className="ss-btn sm p"
              type="button"
              onClick={() =>
                focusIn(
                  secret
                    ? `#key-${p.id}`
                    : `#conn-${p.id}-setup input, #conn-${p.id}-setup button`,
                )
              }
            >
              {keyAct}
            </button>
          )}
          <button className="ss-btn sm" type="button" onClick={runTest}>
            Test
          </button>
          <button
            type="button"
            className="ss-arrow"
            aria-label={`${open ? "Close" : "Open"} ${name}`}
            aria-expanded={open}
            aria-controls={`${head}-b`}
            onClick={() => onToggle(!open)}
          >
            {open ? "▾" : "▸"}
          </button>
        </span>
        {p.id === "copilot" && (
          <span className="ss-help ss-full" data-testid="copilot-token-line">
            {signIn ? "Ready with the token of " : "A GitHub token works, or the token of "}
            <a href="#/settings/connections">Connections › GitHub</a>'s “Sign in with GitHub”
            {signIn ? "." : ": either one runs GitHub Models."}
          </span>
        )}
        {p.id !== "fake" && fake.length > 0 && state.tone === "ok" && (
          <Note tone="info" role="note" className="ss-full">
            {name} is ready, but {fake.join(", ")} still use
            {fake.length === 1 ? "s" : ""} the fake model, so those agents do no
            real work.{" "}
            <button className="ss-btn sm p" type="button" onClick={useForAll}>
              Use {p.label} for all agents
            </button>
          </Note>
        )}
      </div>
      {open && (
        <div className="ss-prow-b" id={`${head}-b`}>
          <div
            style={{
              flex: "1 1 280px",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 500 }}>
              How agents reach {name}
            </span>
            <span data-testid={`way-${p.id}`}>
              <b style={{ fontWeight: 600 }}>{wayLabel}</b>
              {way?.detail ? (
                <span className="ss-meta"> · {way.detail}</span>
              ) : null}
            </span>
            <span className="ss-help">
              keel runs every model in its own agent harness, on the provider's
              API.
            </span>
          </div>
          <div
            style={{
              flex: "1 1 300px",
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            {CONFIGURED.has(p.id) && (
              <div id={`conn-${p.id}-setup`}>
                {p.id === "local" ? (
                  <LocalServers onChanged={onChanged} />
                ) : (
                  <VertexSettings onChanged={onChanged} />
                )}
              </div>
            )}
            {secret && (
              <SecretField
                id={`key-${p.id}`}
                name={secret}
                label={KEY_HELP[p.id]?.label ?? "API key"}
                how={KEY_HELP[p.id]?.how ?? ""}
                set={keySet && !signIn}
                hint={hint}
                onSaved={(h) => {
                  setKeySet(true);
                  setHint(h);
                }}
                onRemoved={() => {
                  setKeySet(false);
                  setHint("");
                  onChanged();
                }}
              />
            )}
            {p.id !== "fake" && (
              <div
                className="ss-label"
                role="group"
                aria-labelledby={`all-${p.id}-lab`}
              >
                <span id={`all-${p.id}-lab`}>Model for all agents</span>
                <span className="ss-actions">
                  <ModelPicker
                    id={`all-${p.id}`}
                    value={forAll}
                    onChange={setAllModel}
                    provider={false}
                    mode={false}
                  />
                  <button
                    className="ss-btn sm"
                    type="button"
                    onClick={useForAll}
                  >
                    Use for all agents
                  </button>
                </span>
                <span className="ss-help">
                  Sets the default, implementer and reviewer model.
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Known providers in the order the page shows them; a row shows at once and fills in when the check answers. */
const ORDER = ["claude", "codex", "copilot", "vertex", "local", "fake"];
const order = (a: Conn, b: Conn) =>
  (ORDER.indexOf(a.id) + 1 || 99) - (ORDER.indexOf(b.id) + 1 || 99);

function CheckingRow({ id }: { id: string }) {
  return (
    <div className="ss-prow" aria-busy="true" data-testid={`conn-${id}`}>
      <div className="ss-row0">
        <Tile>{(NAME[id] ?? id)[0]}</Tile>
        <span className="ss-grow">
          <b style={{ fontWeight: 600 }}>{NAME[id] ?? id}</b>
          <span className="ss-meta">{USE[id]}</span>
        </span>
        <span className="ss-pstate">
          <Spinner>Checking</Spinner>
        </span>
      </div>
    </div>
  );
}

/** The model rows: each kind of agent, and whether this project's agents run on local models only. */
export const MODEL_ROWS: Row[] = [
  {
    key: "default_model",
    label: "Default model",
    kind: { t: "model" },
    help: "Every agent that has no model of its own.",
  },
  {
    key: "implementer_model",
    label: "Implementer model",
    kind: { t: "model" },
    help: "Writes the code that makes a failing test pass.",
  },
  {
    key: "reviewer_model",
    label: "Reviewer model",
    kind: { t: "model" },
    help: "Reviews each criterion and the whole branch.",
  },
  {
    key: "cheaper_model",
    label: "Cheaper model",
    kind: { t: "model" },
    warn: (v) => (v as Model | undefined)?.provider === "fake",
    help: (v) =>
      (v as Model | undefined)?.provider === "fake"
        ? "Not set: the fake model never runs a real flow, so keel pauses and asks instead. Claude Haiku is the usual choice."
        : "Used only when a cap is nearly used and you choose to switch. Claude Haiku by default.",
  },
  {
    key: "local_only",
    label: "Local models only",
    def: false,
    kind: { t: "bool", on: "yes", off: "no" },
    help: (v) =>
      v === true
        ? "This project's agents, KeelBot and the review check run only on local models: keel refuses a cloud model, so the code stays on your machines."
        : "Any model may run this project's agents. Turn it on for code that may not leave your machines (local models: Local models above).",
  },
];

/** A model and its effort: the model's dropdown (grouped by provider) and the effort as a choice. */
function ModelControl({
  id,
  value,
  onSave,
}: {
  id: string;
  value: Model;
  onSave: (m: Model) => void;
}) {
  const catalog = useCatalog();
  const efforts = value
    ? effortsOf(catalog, value.provider, value.mode, value.model)
    : [];
  return (
    <span className="ss-model">
      <ModelPicker id={id} value={value} onChange={onSave} effort={false} />
      {efforts.length > 0 && (
        <Seg
          label="Effort"
          value={value.effort ?? ""}
          onChange={(e) =>
            onSave(
              e
                ? { ...value, mode: "api", effort: e }
                : { provider: value.provider, mode: "api", model: value.model },
            )
          }
          options={[
            ["", "default"],
            ...efforts.map((x) => [x, x] as [string, string]),
          ]}
        />
      )}
    </span>
  );
}

export function ModelsTab({ pid }: { pid: string }) {
  const { project } = useApp();
  const name = project?.name ?? pid;
  const conns = useLoad("connections", () => api.connections(), {
    live: false,
  });
  const s = useScopedSettings(pid, name);
  const fake = fakeAgents(
    s.general.data as Partial<Record<string, unknown>> | null,
  );
  const c = conns.data;
  const providers = c ? [...c.providers].sort(order) : null;
  const missing =
    providers?.filter((p) => connState(p).need === "key" && !!SECRET[p.id])
      .length ?? 0;
  const firstNeed = providers?.find((p) => connState(p).need)?.id ?? "";
  const [opened, setOpened] = useStored<string | null>(
    "keel2.settings.provider",
    null,
  );
  const open = opened ?? firstNeed;
  useEffect(() => {
    if (providers) setSettingsHint("models", missing ? String(missing) : null);
  }, [providers, missing]);
  const changed = () => {
    void conns.reload();
    void s.general.reload();
    if (pid) void s.proj.reload();
  };
  return (
    <SettingsFrame
      tab="models"
      title="Models & keys"
      line="Which API keys the agents use, which model each kind of agent runs on, and what is installed. Keys are encrypted and never shown again in full."
    >
      {conns.error && (
        <ErrorBox error={conns.error} onRetry={() => void conns.reload()} />
      )}
      <section className="ss-list" aria-labelledby="m-acc">
        <div className="ss-row0" style={{ padding: "16px 20px" }}>
          <div className="ss-grow">
            <h2 id="m-acc" className="ss-h2">
              Providers
            </h2>
            <p
              className="ss-sub"
              style={missing ? { color: "var(--warn)" } : undefined}
            >
              {!providers
                ? "Checking each provider: which keys are saved."
                : missing
                  ? `${missing} provider${missing === 1 ? " needs" : "s need"} a key before its agents can run.`
                  : "Every provider is ready."}
            </p>
          </div>
        </div>
        {providers
          ? providers.map((p) => (
              <ProviderRow
                key={p.id}
                p={p}
                fake={fake}
                open={open === p.id}
                onToggle={(o) => setOpened(o ? p.id : "")}
                onChanged={changed}
              />
            ))
          : !conns.error && ORDER.map((id) => <CheckingRow key={id} id={id} />)}
        {providers && !providers.length && (
          <div className="ss-row">
            <EmptyState title="No provider known">
              The keel api lists none. Update keel and reload.
            </EmptyState>
          </div>
        )}
      </section>

      <section className="ss-list" aria-labelledby="m-roles">
        <div className="ss-row0" style={{ padding: "16px 20px" }}>
          <div className="ss-grow">
            <h2 id="m-roles" className="ss-h2">
              Models
            </h2>
            <p className="ss-sub">
              Which model each kind of agent runs on. One agent can still use
              its own (<a href="#/agents">Agents page</a>).
            </p>
          </div>
        </div>
        {s.general.error ? (
          <div className="ss-row">
            <ErrorBox
              error={s.general.error}
              onRetry={() => void s.general.reload()}
            />
          </div>
        ) : (
          <div style={{ padding: "0 20px" }}>
            {MODEL_ROWS.map((row) => (
              <GeneralRow
                key={row.key}
                row={row}
                s={s}
                projectName={name}
                control={
                  row.kind.t === "model"
                    ? (v, save) => (
                        <ModelControl
                          id={`set-${row.key}`}
                          value={v as Model}
                          onSave={save}
                        />
                      )
                    : undefined
                }
              />
            ))}
          </div>
        )}
      </section>

      <section className="ss-cardp" aria-labelledby="m-machine">
        <div>
          <h2 id="m-machine" className="ss-h2">
            This machine
          </h2>
          <p className="ss-sub">
            Programs keel and its agents find in the container.
          </p>
        </div>
        <div className="ss-chipline" aria-label="Installed programs">
          {c ? (
            c.machine.map((m) => (
              <span key={m.name} className={`ss-tag check${m.ok ? "" : " no"}`}>
                <span aria-hidden="true">{m.ok ? "✓" : "!"}</span> {m.name}
                {m.version ? (
                  <span className="ss-mono ss-small">{m.version}</span>
                ) : !m.ok ? (
                  <span className="ss-small">not installed</span>
                ) : null}
              </span>
            ))
          ) : !conns.error ? (
            <Spinner>Checking what is installed</Spinner>
          ) : (
            <span className="ss-meta">Not checked.</span>
          )}
        </div>
      </section>
    </SettingsFrame>
  );
}
