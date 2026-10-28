// keel 0.18.66 Settings › Access: who can open keel, and from where. For the whole keel.
//   Where keel answers   this computer, the Wi-Fi (its addresses) or Tailscale, as keel2 told keel (api GET /api/access).
//                        keel cannot restart itself on another address yet (that needs the host helper of a later
//                        release): the choice shows the exact keel2 command to run, with Copy.
//   Devices              what Settings › Devices was (0.18.57–0.18.65; #/settings/devices opens this tab): pair a phone,
//                        the paired phones, sign out this browser or every browser (0.18.64)
//   AI tools (MCP) and the terminal   the tools that called keel (api McpClients): level, last call, calls today
//   Updates              the daily release check (api updates/ReleaseCheck): the newest version, check every day, the
//                        channel (stable :<v> / product beta :<v>-product-beta), the command that updates this keel

import { useEffect, useState } from "react";
import { api, errorParts } from "../../api";
import { DevicesBody, useDevices } from "../../components/Devices";
import { RadioCards } from "../../components/settingsUi";
import { CopyBlock } from "../../components/StartSteps";
import { agoText } from "../../format";
import { forgetAllDrafts } from "../../drafts";
import { helpApi, type AccessView, type McpClientSeen } from "../../helpApi";
import { queryValue } from "../../routes";
import { signInNeeded } from "../../signIn";
import { useLoad, useRoute } from "../../state";
import { SettingsFrame } from "./frame";

/** Sign out: the drafts kept in this browser go first, then the session; then the sign-in screen shows. */
export async function signOut(everywhere: boolean): Promise<void> {
  forgetAllDrafts();
  await api.webSignOut(everywhere);
  signInNeeded();
}

type Mode = AccessView["mode"];

/** A folder as one shell word: plain when it is safe, else in single quotes. */
export const shellWord = (s: string) => (/^[A-Za-z0-9_./~:@%+-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`);

/** The keel2 command(s) that make keel answer there, from the way it answers now. */
export function accessCommand(want: Mode, a: Pick<AccessView, "mode" | "folder" | "native" | "tailscale" | "tunnel" | "wifi" | "bind">): string {
  const dir = a.folder ? ` ${shellWord(a.folder)}` : "";
  const native = a.native ? " --native" : "";
  const onNetwork = a.bind ? a.bind !== "127.0.0.1" : a.wifi.length > 0;
  const lines: string[] = [];
  if (want === "wifi") lines.push(`keel2 start${native} --wifi${dir}`);
  if (want === "local" || (want === "tailscale" && onNetwork)) lines.push(`keel2 start${native}${dir}`);
  if (want === "tailscale") lines.push("keel2 share tailscale");
  if (want === "local" && (a.tailscale || a.tunnel)) lines.push("keel2 share off");
  return lines.join("\n");
}

function Where() {
  const r = useLoad("access", () => helpApi.access(), { live: false });
  const a = r.data;
  const [want, setWant] = useState<Mode | null>(null);
  const pick = want ?? a?.mode ?? "local";
  const now = (m: Mode) => (a?.mode === m ? <span className="ss-tag acc">now</span> : undefined);
  const wifi = a ? [...a.names, ...a.wifi] : [];
  return (
    <section className="ss-cardp" aria-labelledby="acc-where">
      <h2 id="acc-where" className="ss-h2">Where keel answers</h2>
      {r.error && <p className="ss-note t-err" role="alert">{r.error.message}</p>}
      <RadioCards<Mode>
        label="Where keel answers"
        value={pick}
        onChange={setWant}
        options={[
          { id: "local", title: "This computer only", sub: "the safest; a phone cannot reach keel", side: now("local") },
          {
            id: "wifi",
            title: <>My Wi-Fi{wifi.length ? <> · <span className="ss-mono">{wifi.join(" and ")}</span></> : null}</>,
            sub: <span className="ss-warn-text">plain http inside your network: others on it could read the traffic. Phones pair first.</span>,
            side: now("wifi"),
          },
          {
            id: "tailscale",
            title: <>Anywhere, through Tailscale{a?.tailscale ? <> · <span className="ss-mono">{a.tailscale}</span></> : " · https, only your devices"}</>,
            sub: "needs Tailscale on this computer",
            side: now("tailscale"),
          },
        ]}
      />
      <span className="ss-meta">How each one works, and Tailscale's set-up: <a href="#/help/phone">Help › Use keel from your phone</a></span>
      {a && pick !== a.mode && (
        <div className="acc-cmd">
          <span className="ss-label">Run this in a terminal on this computer:</span>
          <CopyBlock text={accessCommand(pick, a)} label="keel2 command" />
        </div>
      )}
      {a && pick === a.mode && (
        <p className="ss-sub" role="status">
          keel answers here now.{a.mode === "local" && a.tailscale ? <> Tailscale is on too: {a.tailscale}.</> : null}
          {a.tunnel ? <> A public tunnel is on: <span className="ss-mono">{a.tunnel}</span> (<code>keel2 share off tunnel</code> stops it).</> : null}
        </p>
      )}
      <div className="ss-actions">
        <button type="button" className="ss-btn p" disabled title="Needs keel's host helper (a later release)">Save and restart keel</button>
        <span className="ss-meta">Not from here yet: keel cannot restart itself on another address. Run the command above (about 30 s; running flows go on after it).</span>
      </div>
    </section>
  );
}

function Browsers() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (everywhere: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await signOut(everywhere);
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="field">
      <label>Browsers</label>
      <span className="hint">Each browser signs in with its own link (keel2 open). Signing out forgets the drafts kept in this browser.</span>
      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <button className="ss-btn" type="button" disabled={busy} onClick={() => void run(false)}>Sign out this browser</button>
        <button className="ss-btn d" type="button" disabled={busy} onClick={() => void run(true)}>Sign out every browser</button>
      </div>
      {err && <span className="hint">{err}</span>}
    </div>
  );
}

function Devices() {
  const st = useDevices();
  return (
    <section className="ss-sec" aria-labelledby="acc-devices">
      <div className="ss-sechead">
        <div><h2 id="acc-devices" className="ss-h2">Devices</h2><p className="ss-sub">Phones paired with this keel (keel's Android app), and the browsers signed in.</p></div>
        {!st.pairing && (
          <button className="ss-btn" type="button" onClick={() => void st.startPairing()} disabled={st.busy}>Pair a phone (QR code)</button>
        )}
      </div>
      <DevicesBody st={st} />
      <Browsers />
    </section>
  );
}

const who = (c: McpClientSeen) => (c.via === "mcp" ? c.client : c.via === "tui" ? c.client || "keel TUI" : c.client);
const levelWord = (c: McpClientSeen) => (c.level === "you" ? "as you" : c.level);

function Tools() {
  const r = useLoad("mcp-clients", () => helpApi.mcpClients(), { live: false });
  const list = r.data?.clients ?? null;
  return (
    <section className="ss-list" aria-labelledby="acc-mcp">
      <div className="ss-row0" style={{ padding: "16px 20px" }}><h2 id="acc-mcp" className="ss-h2" style={{ flex: 1 }}>AI tools (MCP) and the terminal</h2></div>
      {list === null && !r.error && <p className="ss-row ss-meta" style={{ margin: 0 }}>Loading…</p>}
      {r.error && <p className="ss-row ss-meta" style={{ margin: 0 }}>{r.error.message}</p>}
      {list && !list.length && <p className="ss-row ss-meta" style={{ margin: 0 }}>No AI tool or terminal app called keel yet.</p>}
      {list && list.length > 0 && (
        <ul aria-label="AI tools and terminal apps" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {list.map((c) => (
            <li key={`${c.via}|${c.client}|${c.level}`} className="ss-row">
              <span className="ss-grow">
                <span><b style={{ fontWeight: 600 }}>{who(c)}</b> · {levelWord(c)}</span>
                <span className="ss-meta">
                  {c.via === "mcp" ? "MCP" : c.via === "tui" ? "keel TUI" : c.via} · last call {agoText(c.last_at)}
                  {c.today ? ` · ${c.today} call${c.today === 1 ? "" : "s"} today` : ""}
                  {c.last_tool ? <> · <span className="ss-mono">{c.last_tool}</span></> : null}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="ss-row" style={{ margin: 0 }}><a href="#/help">Connect another tool (Help › Start here, step 3) ›</a></p>
    </section>
  );
}

function Updates() {
  const r = useLoad("updates", () => helpApi.updates(), { live: false });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const u = r.data;
  const save = async (body: { check_daily: boolean; channel?: string | null }) => {
    setErr(null);
    try {
      r.setData(await helpApi.saveUpdates(body));
    } catch (e) {
      setErr(errorParts(e).message);
    }
  };
  const check = async () => {
    setBusy(true);
    setErr(null);
    try {
      r.setData(await helpApi.checkUpdates());
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="ss-cardp" id="acc-updates" aria-labelledby="acc-updates-h">
      <h2 id="acc-updates-h" className="ss-h2">Updates</h2>
      {r.error && <p className="ss-note t-err" role="alert">{r.error.message}</p>}
      {u && (
        <>
          <div className="acc-upd">
            <span>
              keel {u.current}
              {u.newer && u.latest ? <> · a newer one: <b>{u.latest}</b> (<a href={u.url} target="_blank" rel="noreferrer">what is new</a>)</>
                : u.latest ? " · the newest" : ""}
            </span>
            <label className="acc-check">
              <input type="checkbox" checked={u.check_daily} onChange={(e) => void save({ check_daily: e.target.checked, channel: u.channel })} /> Check every day
            </label>
            <label className="acc-check">
              Channel{" "}
              <select className="ss-input" value={u.channel} onChange={(e) => void save({ check_daily: u.check_daily, channel: e.target.value })}>
                <option value="stable">stable</option>
                <option value="product">product beta</option>
              </select>
            </label>
            <button type="button" className="ss-btn sm" onClick={() => void check()} disabled={busy}>{busy ? "Checking…" : "Check now"}</button>
          </div>
          <p className="ss-meta" role="status">
            {u.error ? <>Could not check: {u.error}{u.checked_at ? ` (${agoText(u.checked_at)})` : ""}</>
              : u.checked_at ? <>Checked {agoText(u.checked_at)}.</>
              : u.check_daily ? "Not checked yet: keel looks once a day." : "Off: keel makes no network call for this. Check now asks once."}
          </p>
          {err && <p className="ss-note t-err" role="alert">{err}</p>}
          <div className="acc-cmd">
            <span className="ss-label">The update command (a terminal on this computer):</span>
            <CopyBlock text={u.command} label="update command" />
            <span className="ss-meta">{u.command_why}</span>
            <span className="ss-meta">
              {u.channel === "product" ? <>Product beta images are tagged <code>&lt;version&gt;-product-beta</code></> : <>Stable images are tagged <code>&lt;version&gt;</code> and <code>latest</code></>}
              {u.image ? <>; keel runs <span className="ss-mono">{u.image}</span></> : null}. <code>keel2 update</code> keeps the image keel runs; a pinned
              tag needs the new one named: <code>KEEL_IMAGE=&lt;image&gt; keel2 update</code>.
            </span>
          </div>
        </>
      )}
    </section>
  );
}

export function AccessTab() {
  const route = useRoute();
  const at = queryValue(route, "at");
  // #/settings/access?at=updates (the update notice's link) opens at that part
  useEffect(() => {
    if (!at) return;
    const t = window.setTimeout(() => document.getElementById(`acc-${at}`)?.scrollIntoView?.({ block: "start" }), 300);
    return () => window.clearTimeout(t);
  }, [at]);
  return (
    <SettingsFrame tab="access" title="Access" line="Who can open keel, from where: this computer, your Wi-Fi or Tailscale; phones, browsers, AI tools and updates. For the whole keel.">
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <Where />
        <Devices />
        <Tools />
        <Updates />
      </div>
    </SettingsFrame>
  );
}
