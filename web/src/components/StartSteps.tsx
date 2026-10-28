// keel 0.18.66 the five set-up steps, as keel really sees them (api GET /api/help/start). Help › Start here lists them
// and the first-run welcome walks through them; both read the same state (useStartStatus) and draw step 3 with the
// same McpSetup.
//
//   1 a project added · 2 a model connected · 3 an AI tool connected over MCP (the first call keel sees)
//   4 init done for the current project · 5 a first flow started (not init)
//
// Step 3 is done when keel's MCP clients list (api McpClients: every call with an MCP level token, reads too) shows an
// AI tool; until then this asks keel every 3 s, so the dot turns green by itself.

import { useEffect, useRef, useState } from "react";
import { copyText } from "../clipboard";
import { helpApi, type McpClientSeen, type StartView } from "../helpApi";
import { useApp, useLoad } from "../state";

export type StepId = "project" | "model" | "mcp" | "init" | "flow";
export type StepStatus = { id: StepId; n: number; title: string; done: boolean; line: string };

export const STEP_TITLES: Record<StepId, string> = {
  project: "Add a project",
  model: "Connect a model",
  mcp: "Connect your AI coding tool to keel (MCP)",
  init: "Let keel learn the project (init)",
  flow: "Your first flow",
};

/** Step 5's three first flows: one line each. */
export const FIRST_FLOWS: { id: string; line: string }[] = [
  { id: "clarify", line: "not sure what to build yet: keel asks you questions first" },
  { id: "small", line: "a small change: a test first, then the code, one gate" },
  { id: "feature", line: "something bigger: a spec, criteria, a gate per criterion" },
];

const MCP_POLL_MS = 3000;

/** The five steps' state: the start view, the AI tools seen (asked again every 3 s until one is there), and each step
 *  with done and one line. `paused`: no polling (a welcome screen that does not show step 3). */
export function useStartStatus(opts: { poll?: boolean } = {}) {
  const { pid, projects } = useApp();
  // the project list changes (added, init wrote its config, a flow started): read the steps again
  const sig = projects.map((p) => `${p.id}:${p.configured ? 1 : 0}:${p.flow ?? ""}`).join(",");
  const start = useLoad(`help-start:${pid ?? ""}:${sig}`, () => helpApi.start(pid), { live: false });
  const [clients, setClients] = useState<McpClientSeen[] | null>(null);
  const mcp = (clients ?? start.data?.mcp ?? []).filter((c) => c.via === "mcp");
  const waiting = opts.poll !== false && !mcp.length;
  useEffect(() => {
    if (!waiting) return;
    let gone = false;
    const ask = () =>
      helpApi.mcpClients().then(
        (r) => !gone && setClients(r.clients),
        () => undefined,
      );
    const t = window.setInterval(ask, MCP_POLL_MS);
    return () => {
      gone = true;
      window.clearInterval(t);
    };
  }, [waiting]);
  const steps = stepsOf(start.data, mcp);
  return { data: start.data, error: start.error, reload: start.reload, steps, mcp, done: steps.filter((s) => s.done).length };
}

export function stepsOf(d: StartView | null, mcp: McpClientSeen[]): StepStatus[] {
  const p = d?.project ?? null;
  const model = d?.models[0];
  const tool = mcp[0];
  const rows: [StepId, boolean, string][] = [
    ["project", (d?.projects ?? 0) > 0, p ? `Done: ${p.name}${(d?.projects ?? 0) > 1 ? ` and ${(d?.projects ?? 1) - 1} more` : ""}.` : "Pick a git repository on this computer."],
    ["model", !!model, model ? `Done: ${model.label}.` : "Use a subscription you already have, or an API key."],
    ["mcp", !!tool, tool ? `Done: keel saw ${tool.client} (${tool.level}).` : "Optional: your editor's AI talks to keel."],
    ["init", !!p?.configured, !p ? "Add a project first." : p.configured ? `Done: keel knows ${p.name}.` : p.init_running ? `init runs now on ${p.name}.` : `keel does not know ${p.name} yet.`],
    ["flow", !!d?.flow, d?.flow ? `Done: ${d.flow.title || d.flow.workflow || "a flow"}${d.flow.workflow ? ` (${d.flow.workflow})` : ""}.` : "Say what you want in your words."],
  ];
  return rows.map(([id, done, line], i) => ({ id, n: i + 1, title: STEP_TITLES[id], done, line }));
}

// ---------- step 3: connect an AI tool ----------

export type McpTool = "claude" | "opencode" | "codex" | "cursor";
export type McpLevel = "read" | "write" | "admin";

export const MCP_TOOLS: { id: McpTool; label: string }[] = [
  { id: "claude", label: "Claude Code" },
  { id: "opencode", label: "opencode" },
  { id: "codex", label: "Codex" },
  { id: "cursor", label: "Cursor / VS Code" },
];

export const MCP_LEVELS: { id: McpLevel; label: string; line: string }[] = [
  { id: "read", label: "Read only", line: "look, never change" },
  { id: "write", label: "Write", line: "answer gates, start flows" },
  { id: "admin", label: "Admin", line: "also delete and settings" },
];

/** keel2 mcp's arguments for a level (read is the default: no flag). */
const args = (level: McpLevel): string[] => (level === "read" ? ["mcp"] : ["mcp", `--${level}`]);
const json = (v: unknown) => JSON.stringify(v, null, 2);

/** One way to add keel to a tool: where it goes, the text to run or paste, and how to see it worked. */
export type McpSnippet = { where: string; text: string; lang: "bash" | "json" | "toml"; then: string };

/** The one-time command or config for a tool and level. Claude Code's is keel's own (keel2 mcp --print-config); the
 *  others follow each tool's own MCP config format. */
export function mcpSnippets(tool: McpTool, level: McpLevel): McpSnippet[] {
  const a = args(level);
  switch (tool) {
    case "claude":
      return [{ where: "Run this once in a terminal on this computer:", lang: "bash", text: `claude mcp add keel -- keel2 ${a.join(" ")}`,
        then: "In Claude Code, type /mcp: keel shows as connected." }];
    case "opencode":
      return [{ where: "Put this in opencode.json (in the project, or ~/.config/opencode/opencode.json for every project):", lang: "json",
        text: json({ $schema: "https://opencode.ai/config.json", mcp: { keel: { type: "local", command: ["keel2", ...a], enabled: true } } }),
        then: "Start opencode again: keel's tools are in its list." }];
    case "codex":
      return [{ where: "Run this once in a terminal on this computer:", lang: "bash", text: `codex mcp add keel -- keel2 ${a.join(" ")}`,
        then: "In Codex, type /mcp: keel is in the list." },
      { where: "Or put this in ~/.codex/config.toml:", lang: "toml",
        text: `[mcp_servers.keel]\ncommand = "keel2"\nargs = [${a.map((x) => `"${x}"`).join(", ")}]`, then: "" }];
    case "cursor":
      return [{ where: "Cursor: put this in ~/.cursor/mcp.json (or .cursor/mcp.json in the project):", lang: "json",
        text: json({ mcpServers: { keel: { command: "keel2", args: a } } }),
        then: "Cursor Settings › MCP shows keel: turn it on." },
      { where: "VS Code: put this in .vscode/mcp.json in the project:", lang: "json",
        text: json({ servers: { keel: { type: "stdio", command: "keel2", args: a } } }),
        then: "In the Chat view (agent mode), the tools list shows keel." }];
  }
}

/** A snippet with its Copy button ("Copied" for a moment). */
export function CopyBlock({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  const t = useRef<number>(0);
  useEffect(() => () => window.clearTimeout(t.current), []);
  const copy = async () => {
    const ok = await copyText(text);
    setCopied(ok ? "yes" : "no");
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setCopied(null), 2000);
  };
  return (
    <div className="hs-copy">
      <pre className="hs-pre" aria-label={label}><code>{text}</code></pre>
      <button type="button" className="btn hs-copy-b" onClick={() => void copy()} aria-label={`Copy: ${label}`}>
        {copied === "yes" ? "Copied" : copied === "no" ? "Select and copy" : "Copy"}
      </button>
    </div>
  );
}

/** The live dot: grey and pulsing while keel waits for the first call, green once it saw one. */
export function McpWaiting({ tools, toolLabel }: { tools: McpClientSeen[]; toolLabel: string }) {
  const seen = tools[0];
  return (
    <div className={`hs-live${seen ? " on" : ""}`} role="status" aria-live="polite" data-testid="mcp-live">
      <span className="hs-dot" aria-hidden="true" />
      {seen ? (
        <span>keel saw <b>{seen.client}</b> ({seen.level}){seen.last_tool ? <> · {seen.last_tool}</> : null}. Connected.</span>
      ) : (
        <span>Waiting for the first call from {toolLabel}…</span>
      )}
    </div>
  );
}

/** Step 3's body: the tool tabs, the level, the command or config to copy, the live dot and "It does not connect". */
export function McpSetup({ tools, compact, live = true }: { tools: McpClientSeen[]; compact?: boolean; live?: boolean }) {
  const [tool, setTool] = useState<McpTool>("claude");
  const [level, setLevel] = useState<McpLevel>("write");
  const label = MCP_TOOLS.find((t) => t.id === tool)!.label;
  const snippets = mcpSnippets(tool, level);
  return (
    <div className={`hs-mcp${compact ? " compact" : ""}`}>
      <div role="tablist" aria-label="Your tool" className="hs-tabs">
        {MCP_TOOLS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === tool} className="hs-tab" onClick={() => setTool(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      <ol className="hs-ol" role="tabpanel" aria-label={label}>
        <li>
          <span>Pick what it may do:</span>
          <div role="radiogroup" aria-label="Level" className="hs-levels">
            {MCP_LEVELS.map((l) => (
              <label key={l.id} className={`hs-level${l.id === level ? " on" : ""}`}>
                <input type="radio" name={`mcp-level${compact ? "-w" : ""}`} checked={l.id === level} onChange={() => setLevel(l.id)} />
                <span><b>{l.label}</b> · {l.line}</span>
              </label>
            ))}
          </div>
          {level === "admin" && (
            <p className="hs-warn" role="note">
              Admin can also delete flows, add and remove projects, and change workflows and rules. keel still asks you
              first for risky actions. Pick it only when you need it.
            </p>
          )}
        </li>
        {snippets.map((s, i) => (
          <li key={`${tool}-${i}`} className={i > 0 ? "hs-or" : undefined}>
            <span>{s.where}</span>
            <CopyBlock text={s.text} label={`${label} ${s.lang === "bash" ? "command" : "config"}${snippets.length > 1 ? ` ${i + 1}` : ""}`} />
            {s.then && <span className="hs-then">{s.then}</span>}
          </li>
        ))}
      </ol>
      {live && <McpWaiting tools={tools} toolLabel={label} />}
      <details className="hs-fold">
        <summary>It does not connect</summary>
        <p>
          Run <code>keel2 doctor</code> in a terminal: it checks Docker, the port and the image, and says what to fix. Is{" "}
          <code>keel2</code> on the tool's PATH? Then use its full path (<code>command -v keel2</code>).
        </p>
        <p>
          A tool without forms asks you to OK risky actions in keel's Inbox: it answers "This needs the person's OK", and the
          same question waits in the Inbox (the dashboard, keel TUI, the phone app). Approve it there, then ask the tool again.
        </p>
      </details>
    </div>
  );
}
