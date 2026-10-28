// Settings › Devices: the phones paired with this keel (keel's Android app), Remove (asks first, in the page), and
// "Pair a phone": a one-time code as a QR code for the address the phone uses (Wi-Fi, Tailscale or a tunnel), with
// its countdown. The QR code is made here, in the page. When keel only answers on this computer, it says how to
// open it to a phone (keel2 start --wifi, keel2 share tailscale). keel 0.18.57: its own Settings tab — "Pair a phone"
// is the tab's button; 0.18.66 the devices are part of Settings › Access (pages/settings/Access). DevicesSection is the
// same content with the button in it.

import { useCallback, useEffect, useRef, useState } from "react";
import { errorParts } from "../api";
import {
  devicesApi,
  type Device,
  type Pairing,
  type PairUrl,
} from "../devicesApi";
import { agoText } from "../format";
import { QrCode } from "./QrCode";
import { Note, Tile } from "./settingsUi";
import { ErrorBox } from "./ui";

const KIND_LABEL: Record<string, string> = {
  wifi: "Wi-Fi",
  tailscale: "Tailscale",
  tunnel: "Tunnel (public)",
  local: "This computer",
};
const kindLabel = (k: string) => KIND_LABEL[k] ?? k;
const PLATFORM: Record<string, string> = { android: "Android", ios: "iOS" };

/** "9:41" left of a code, from its expiry. */
export function countdown(expiresAt: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((Date.parse(expiresAt) - now) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

type Err = { message: string; hint?: string } | null;

/** The devices, a pairing code and what the person does with them. */
export function useDevices() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [err, setErr] = useState<Err>(null);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [pick, setPick] = useState(0);
  const [asking, setAsking] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [paired, setPaired] = useState<string | null>(null);
  const known = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const list = await devicesApi.list();
      setDevices(list);
      setErr(null);
      return list;
    } catch (e) {
      setErr(errorParts(e));
      return null;
    }
  }, []);
  useEffect(() => {
    void load().then((l) => {
      if (l) known.current = new Set(l.map((d) => d.id));
    });
  }, [load]);

  // while a code is shown: the countdown, and the list now and then, so the new phone shows up when it pairs
  useEffect(() => {
    if (!pairing) return;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    const poll = window.setInterval(() => {
      void load().then((l) => {
        const fresh = l?.find((d) => !known.current.has(d.id));
        if (fresh) {
          known.current = new Set(l!.map((d) => d.id));
          setPairing(null);
          setPaired(fresh.name);
        }
      });
    }, 3000);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(poll);
    };
  }, [pairing, load]);

  const startPairing = async () => {
    setBusy(true);
    setPaired(null);
    try {
      const p = await devicesApi.pairing();
      known.current = new Set((devices ?? []).map((d) => d.id));
      setPairing(p);
      setPick(0);
      setNow(Date.now());
      setErr(null);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (d: Device) => {
    setBusy(true);
    try {
      await devicesApi.remove(d.id);
      setAsking(null);
      await load();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return { devices, err, pairing, setPairing, pick, setPick, asking, setAsking, busy, now, paired, load, startPairing, remove };
}
export type DevicesState = ReturnType<typeof useDevices>;

const PHONE = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
    <rect x="7" y="2" width="10" height="20" rx="2" /><path d="M11 18h2" />
  </svg>
);

/** The pairing card (while a code is shown) and the paired phones. `pairButton`: "Pair a phone" is in here (it is the
 *  tab's own button on the Settings page). */
export function DevicesBody({ st, pairButton }: { st: DevicesState; pairButton?: boolean }) {
  const { devices, err, pairing, setPairing, pick, setPick, asking, setAsking, busy, now, paired, load, startPairing, remove } = st;
  const url: PairUrl | undefined = pairing?.urls[Math.min(pick, (pairing?.urls.length ?? 1) - 1)];
  const left = pairing ? Date.parse(pairing.expiresAt) - now : 0;
  const expired = !!pairing && left <= 0;
  const reachable = pairing ? (pairing.reachable ?? pairing.urls.some((u) => u.kind !== "local")) : true;
  return (
    <div className="pdv" data-testid="devices" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {err && <ErrorBox error={err} onRetry={() => void load()} />}
      {paired && <Note tone="ok" role="status">Paired: {paired}</Note>}
      {pairButton && !pairing && (
        <button className="ss-btn p" type="button" style={{ alignSelf: "flex-start" }} onClick={() => void startPairing()} disabled={busy}>Pair a phone</button>
      )}
      {pairing && url && (
        <section className="ss-cardp" style={{ gap: 20 }} aria-label="Pair a phone" role="group">
          <h2 className="ss-h2">Pair a phone</h2>
          {!reachable && (
            <p className="ss-note t-warn" role="note">
              keel only answers on this computer (127.0.0.1), so a phone cannot reach it yet. Start keel with <code>keel2 start --wifi</code> or{" "}
              <code>keel2 share tailscale</code> so a phone can reach it, then make a new code.
            </p>
          )}
          {pairing.urls.length > 1 && (
            <div className="ss-label">
              <span id="pair-kind">Address for the phone</span>
              <div className="ss-seg" role="radiogroup" aria-labelledby="pair-kind" aria-label="Address for the phone">
                {pairing.urls.map((u, i) => (
                  <button key={u.url} type="button" role="radio" aria-checked={i === pick} onClick={() => setPick(i)}>{kindLabel(u.kind)}</button>
                ))}
              </div>
            </div>
          )}
          <div className="ss-inline" style={{ alignItems: "flex-start", gap: 24 }}>
            <div className="ss-qr">
              {expired ? <span role="status">This code expired. Make a new one.</span> : <QrCode text={url.qr} label={`Pairing QR code for ${url.url}`} />}
            </div>
            <div style={{ flex: "1 1 260px", minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
              <dl className="ss-facts">
                <dt>Address</dt><dd className="ss-mono" data-testid="pair-url">{url.url}</dd>
                <dt>Code</dt><dd className="ss-devcode">{pairing.code}</dd>
                <dt>Valid for</dt><dd data-testid="pair-left" style={expired ? { color: "var(--bad)" } : undefined}>{expired ? "expired" : countdown(pairing.expiresAt, now)}</dd>
              </dl>
              {url.kind === "tunnel" && <p className="ss-sub">This address is public: anyone with it reaches keel's sign-in and pairing, nothing more.</p>}
              {url.kind === "wifi" && <p className="ss-sub">Plain http inside your Wi-Fi. The phone must be on the same network.</p>}
              <p style={{ margin: 0, fontSize: 13 }}>In keel's Android app: Pair › scan this code. It works once.</p>
              <div className="ss-actions">
                <button className="ss-btn sm" type="button" onClick={() => void startPairing()} disabled={busy}>New code</button>
                <button className="ss-btn sm q" type="button" onClick={() => setPairing(null)}>Close</button>
              </div>
            </div>
          </div>
        </section>
      )}
      <section className="ss-list" aria-labelledby="d-list">
        <div className="ss-row0" style={{ padding: "16px 20px" }}><h2 id="d-list" className="ss-h2" style={{ flex: 1 }}>Paired devices</h2></div>
        {devices === null && !err && <p className="ss-row ss-meta" style={{ margin: 0 }}>Loading devices…</p>}
        {devices !== null && (devices.length === 0 ? (
          <p className="ss-row ss-meta" style={{ margin: 0 }}>No phone is paired yet.</p>
        ) : (
          <ul aria-label="Paired devices" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {devices.map((d) => (
              <li key={d.id} className="ss-row">
                <Tile>{PHONE}</Tile>
                <span className="ss-grow">
                  <b style={{ fontWeight: 600 }}>{d.name}</b>
                  <span className="ss-meta">
                    {PLATFORM[d.platform] ?? d.platform}{" · "}last seen {d.lastSeenAt ? agoText(d.lastSeenAt) : "never"}
                    {d.lastIp ? <>{" "}· <span className="ss-mono">{d.lastIp}</span></> : null}
                  </span>
                </span>
                {asking === d.id ? (
                  <div className="ss-confirm" role="group" aria-label="Confirm">
                    <span>Remove {d.name}? It is signed out at once and has to pair again.</span>
                    <button className="ss-btn sm dp" type="button" disabled={busy} onClick={() => void remove(d)}>{busy ? "Working…" : "Remove"}</button>
                    <button className="ss-btn sm" type="button" disabled={busy} onClick={() => setAsking(null)}>Cancel</button>
                  </div>
                ) : (
                  <button className="ss-btn sm" type="button" style={{ marginLeft: "auto" }} aria-label={`Remove ${d.name}`} onClick={() => setAsking(d.id)}>Remove</button>
                )}
              </li>
            ))}
          </ul>
        ))}
      </section>
    </div>
  );
}

/** The devices, with "Pair a phone" in it (a page or a test that has no Settings frame). */
export function DevicesSection() {
  const st = useDevices();
  return <DevicesBody st={st} pairButton />;
}
