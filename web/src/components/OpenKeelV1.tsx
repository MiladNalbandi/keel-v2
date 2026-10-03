// "Open in keel v1": asks the api to start keel v1's dashboard in the container, then opens it in a new tab.

import { useState } from "react";
import { api, errorParts } from "../api";
import { useApp } from "../state";

export const KEEL_V1_URL = "/keel-v1/";

export function OpenKeelV1Button({ className = "btn" }: { className?: string }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const open = async () => {
    // Open the tab now (inside the click) so the browser does not block it, then point it at the dashboard.
    let tab: Window | null = null;
    try {
      tab = window.open("about:blank", "_blank");
    } catch {
      tab = null;
    }
    setBusy(true);
    try {
      const r = await api.keelDashboard();
      const url = r?.url || KEEL_V1_URL;
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      } else {
        window.open(url, "_blank", "noopener");
      }
    } catch (e) {
      tab?.close();
      const p = errorParts(e);
      toast(`keel v1 dashboard did not start: ${p.message}${p.hint ? ` ${p.hint}` : ""}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className={className} type="button" onClick={open} disabled={busy} title="keel v1's own dashboard, for this project">
      {busy ? "Opening…" : "Open in keel v1"}
    </button>
  );
}
