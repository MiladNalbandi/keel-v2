// keel 0.18.57 Settings › Devices: the phones paired with this keel (keel's Android app), for the whole keel. "Pair a
// phone" is the tab's own button; the code, its QR and the list are components/Devices.tsx.
// keel 0.18.64 also the browsers: sign this one out, or every browser. Each browser has its own sign-in (api
// auth/WebSessions); signing out forgets the drafts kept here (unsent answers, KeelBot's chats).

import { useState } from "react";
import { api, errorParts } from "../../api";
import { DevicesBody, useDevices } from "../../components/Devices";
import { forgetAllDrafts } from "../../drafts";
import { signInNeeded } from "../../signIn";
import { SettingsFrame } from "./frame";

/** Sign out: the drafts kept in this browser go first, then the session; then the sign-in screen shows. */
export async function signOut(everywhere: boolean): Promise<void> {
  forgetAllDrafts();
  await api.webSignOut(everywhere);
  signInNeeded();
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
    <div className="field" style={{ marginTop: 24 }}>
      <label>Browsers</label>
      <span className="hint">Each browser signs in with its own link (keel2 open). Signing out forgets the drafts kept in this browser.</span>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="ss-btn" type="button" disabled={busy} onClick={() => void run(false)}>Sign out this browser</button>
        <button className="ss-btn" type="button" disabled={busy} onClick={() => void run(true)}>Sign out every browser</button>
      </div>
      {err && <span className="hint">{err}</span>}
    </div>
  );
}

export function DevicesTab() {
  const st = useDevices();
  return (
    <SettingsFrame tab="devices" title="Devices" line="Phones paired with this keel (keel's Android app). For the whole keel."
      actions={!st.pairing ? <button className="ss-btn p" type="button" onClick={() => void st.startPairing()} disabled={st.busy}>Pair a phone</button> : undefined}>
      <DevicesBody st={st} />
      <Browsers />
    </SettingsFrame>
  );
}
