// Settings › Connections › Linear (the key, checked before it is kept, never shown again, and what can go to the model company), and a
// card on the Tools page that lists what agents get. It imports only react and @keel/web-sdk.

import { useState } from "react";
import { del, errorParts, get, Note, Panel, Pill, post, useApp, useLoad } from "@keel/web-sdk";

export type LinearView = { linked: boolean; url: string; who: string | null; at: string | null; hint: string | null; sends: string };

const PATH = "/plugins/links/linear";
export const linksApi = {
  view: () => get<LinearView>(PATH),
  save: (token: string) => post<LinearView>(PATH, { token }),
  forget: () => del<LinearView>(PATH),
  plugins: (pid: string) => get<{ name: string; enabled: boolean }[]>(`/projects/${encodeURIComponent(pid)}/plugins`),
};

export function LinearCard({ pid }: { pid: string }) {
  const { toast } = useApp();
  const view = useLoad("links-linear", () => linksApi.view());
  const plugins = useLoad(pid ? `plugins:${pid}` : null, () => linksApi.plugins(pid), { live: false });
  const [token, setToken] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      view.setData(await linksApi.save(token));
      setToken("");
      toast("Linear is linked.");
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  const v = view.data;
  const on = !!plugins.data?.find((p) => p.name === "links")?.enabled;
  return (
    <Panel title="Linear" extra={v?.linked ? <Pill tone="ok">linked{v.who ? ` as ${v.who}` : ""}</Pill> : <Pill tone="idle">not linked</Pill>}>
      <div className="grid" style={{ gap: 8 }}>
        {v && <Note tone="info"><b>What can go to the model company.</b> {v.sends}</Note>}
        {v?.linked ? (
          <>
            <p>The key ends in <span className="mono">{v.hint}</span>. keel keeps it encrypted and never shows it.</p>
            {pid && !on && <p className="sub">Links is off for this project, so its agents do not read Linear. Turn it on in <a href="#/settings/plugins">Settings › Plugins</a>.</p>}
            <div><button className="btn sm ghost" type="button" onClick={() => void linksApi.forget().then((x) => view.setData(x))}>Forget the key</button></div>
          </>
        ) : (
          <form onSubmit={(e) => void save(e)} aria-label="Link Linear" className="grid" style={{ gap: 8 }}>
            <div className="field"><label htmlFor="ln-f101">API key</label> <input id="ln-f101" type="password" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)} placeholder="lin_api_…" />
              <span className="hint">Linear › Settings › Security &amp; access › Personal API keys. keel asks Linear who it is first, and keeps it only if Linear takes it.</span></div>
            {problem && <Note tone="err" role="alert">{problem}</Note>}
            <div><button className="btn primary" type="submit" disabled={busy}>{busy ? "Checking…" : "Link Linear"}</button></div>
          </form>
        )}
      </div>
    </Panel>
  );
}

/** Tools: what agents get from Links. They get the tools on this page like any server's. */
export function LinksTools() {
  return (
    <Panel title="Links: Linear">
      <p><b>keel-links</b> reads with <span className="mono">links_search</span> and <span className="mono">links_issue</span>. <span className="mono">links_comment</span> and{" "}
        <span className="mono">links_create_issue</span> write, and each waits for your OK in the Inbox with the exact words. Add the key in <a href="#/settings/connections">Connections</a>.</p>
    </Panel>
  );
}
