// v0.16.0 the Inbox's "Agent install request" (docs/plugins/mockup.html): an agent, a workflow or keel itself asks to
// install (or update) a plugin. The card shows who asks and when, the plugin (icon, version, publisher, trust), the
// reason in the asker's words, what it will be allowed to do (each permission with its level: high, medium, low), what
// happens when it is approved (what it needs, the signature, the restart), then Approve and install / Deny. Approve
// installs it (it loads when keel restarts); Deny installs nothing and the asker is told. An agent never installs by itself.

import { useState } from "react";
import { errorParts } from "../api";
import { inboxApi, type InboxItem } from "../inboxApi";
import { permissionLines, whoAsked, type InstallRequest } from "../marketplaceApi";
import { PluginIcon, pluginMark } from "./CoreOnly";
import { TrustTag } from "./Permissions";
import { Key } from "./Key";
import { ErrorBox, Pill, Since } from "./ui";

/** How many permissions show before "See all". */
const FIRST = 3;

/** The askers whoAsked names (KeelBot, a workflow, Claude Code, keel, an agent); any other source is a flow's agent. */
const NAMED = new Set(["keelbot", "workflow", "mcp", "keel", "", "agent"]);
const named = (s: string | null | undefined) => NAMED.has((s ?? "").toLowerCase());
/** Who asks, in the header: "KeelBot", or "agent investigator". */
const asker = (s: string | null | undefined) => (named(s) ? whoAsked(s) : `agent ${s}`);
/** Who says the reason: "KeelBot says", or "The agent says". */
const sayer = (s: string | null | undefined) => (named(s) ? `${whoAsked(s)} says` : "The agent says");

export function InstallRequestCard({ it, cardKey, onDone }: {
  it: InboxItem;
  cardKey: string;
  onDone: (msg: string) => Promise<void>;
}) {
  const p = (it.payload ?? {}) as Partial<InstallRequest>;
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-install-${it.id}`;
  const name = p.name || "";
  const title = p.title || p.name || "the plugin";
  const reasons = p.reasons?.length ? p.reasons : p.reason ? [{ reason: p.reason, source: p.source }] : [];
  const who = asker(p.source ?? it.flow);
  const installs = p.installs ?? [];
  const brought = new Set(installs.map((x) => x.name));
  const needsHere = (p.needs ?? []).filter((n) => !brought.has(n));
  const lines = permissionLines(p.permissions);
  const shown = all ? lines : lines.slice(0, FIRST);
  const update = !!p.update;

  const answer = async (decision: "approve" | "deny") => {
    setBusy(true);
    setErr(null);
    try {
      await inboxApi.decide(it.id ?? "", decision, why.trim());
      const done = update ? "updated" : "installed";
      await onDone(decision === "approve" ? `${title} is ${done}. Restart keel to use it.` : `Denied: ${title} is not ${done}.`);
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };

  return (
    <article className="inbox-item k-plugin-install req" aria-label={it.title} data-testid="inbox-item" data-key={cardKey} tabIndex={-1}>
      <div className="req-h">
        <div className="req-who">
          <Pill tone="warn">Waiting for you</Pill>
          <b>{update ? "Update request" : "Install request"}</b>
          <span className="sub">
            from {who}
            {it.project_name ? <> · project <span className="mono">{it.project_name}</span></> : null}
          </span>
        </div>
        {it.since && <span className="sub num">waiting <Since from={it.since} /></span>}
      </div>

      <div className="req-b">
        <div className="req-title">
          <PluginIcon name={name} size="lg" />
          <div>
            <h2 id={hid}>
              {title}
              {(p.version || p.installed) && (
                <span className="mono req-v">{update && p.installed ? `${p.installed} → ${p.version ?? "new"}` : p.version}</span>
              )}
              {p.verified && <span className="req-tick" title="Verified publisher" aria-label="verified">✓</span>}
              {p.publisher && <span className="sub req-by">by {p.publisher}{p.verified === false ? " (not verified)" : ""}</span>}
            </h2>
            <div className="req-line">
              <TrustTag trust={p.trust} />
              {p.summary && <span className="sub">{p.summary}</span>}
            </div>
          </div>
        </div>

        {reasons.length > 0 && (
          <div className="req-quote" aria-label="Why it asks">
            <span className="sub">{reasons.length > 1 ? "They say" : sayer(reasons[0].source)}</span>
            {reasons.map((r, i) => (
              <p key={i}>“{r.reason}”{reasons.length > 1 ? <span className="sub"> · {asker(r.source)}</span> : null}</p>
            ))}
          </div>
        )}

        <div className="req-cols">
          <div>
            {!!p.more?.length && (
              <>
                <p className="req-mini">New permissions</p>
                <ul className="req-diff" aria-label="New permissions">
                  {p.more.map((m, i) => <li key={i} className="mono">{m}</li>)}
                </ul>
              </>
            )}
            <p className="req-mini">It will be allowed to</p>
            {lines.length ? (
              <ul className="perms compact req-perms" aria-label="Permissions">
                {shown.map((l, i) => (
                  <li key={i} className="perm">
                    <span className={`lvl ${l.level}`}>{l.level}</span>
                    <b>{l.text}</b>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="sub perm-none">It asks for no permissions.</p>
            )}
            {lines.length > FIRST && (
              <button className="linkish" type="button" aria-expanded={all} onClick={() => setAll((a) => !a)}>
                {all ? "Show fewer" : `See all ${lines.length} permissions`}
              </button>
            )}
          </div>
          <div>
            <p className="req-mini">What happens</p>
            <ul className="req-checks" aria-label="What happens">
              {needsHere.map((n) => (
                <li key={n}><span className="ok-mark" aria-hidden="true">✓</span><span>Needs {pluginMark(n).title} · installed</span></li>
              ))}
              {installs.length > 0 && (
                <li>
                  <span className="go-mark" aria-hidden="true">→</span>
                  <span>Also installs {installs.map((x) => x.title || x.name).join(", ")}: {title} needs {installs.length === 1 ? "it" : "them"}.</span>
                </li>
              )}
              {p.verified ? (
                <li><span className="ok-mark" aria-hidden="true">✓</span><span>Signed by {p.publisher || "its publisher"}</span></li>
              ) : (
                <li><span className="warn-mark" aria-hidden="true">!</span><span>keel checks its signature first</span></li>
              )}
              <li>
                <span className="go-mark" aria-hidden="true">→</span>
                <span>
                  {update ? `keel updates it${p.installed ? ` from ${p.installed}` : ""}${p.version ? ` to ${p.version}` : ""}` : "keel installs it"}. Nothing runs yet.
                </span>
              </li>
              <li><span className="go-mark" aria-hidden="true">→</span><span>It loads when keel restarts the api and the engine, about 30 s.</span></li>
            </ul>
          </div>
        </div>

        <div className="req-acts">
          <button className="btn primary" type="button" disabled={busy} aria-keyshortcuts="A" onClick={() => void answer("approve")}>
            {update ? "Approve and update" : "Approve and install"} <Key k="A" />
          </button>
          <button className="btn" type="button" disabled={busy} onClick={() => void answer("deny")}>
            {update && p.installed ? `Keep ${p.installed}` : "Deny"}
          </button>
          <input className="req-note" type="text" aria-label="Note (optional)" aria-keyshortcuts="R" value={why} onChange={(e) => setWhy(e.target.value)}
            placeholder={who === "keel" ? "A note for the log (optional)" : `A note for ${who} (optional)`} />
          <span className="sub">An agent cannot install anything by itself.</span>
        </div>
        {err && <ErrorBox error={err} />}
      </div>
    </article>
  );
}
