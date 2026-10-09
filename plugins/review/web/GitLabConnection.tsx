// v0.14.0 Connections › GitLab: the server (gitlab.com or your company's) and a personal access token with the api and
// read_user scopes, for the Code Review plugin's merge requests. The token is saved encrypted; only its last 3
// characters are ever shown. A project is on GitLab when its origin remote names this server.

import { useState } from "react";
import { errorParts, Pill, Section, useApp, useLoad } from "@keel/web-sdk";
import { reviewApi } from "./reviewApi";

export function GitLabSection() {
  const gl = useLoad("gitlab", () => reviewApi.gitlab(), { live: false });
  const { toast } = useApp();
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const shownUrl = url || gl.data?.url || "";
  const save = async () => {
    setBusy(true);
    try {
      const r = await reviewApi.saveGitlab(shownUrl, token || undefined);
      setToken("");
      setUrl("");
      toast(`GitLab saved: ${r.host}${r.hint ? ` (${r.hint})` : ""}.`);
      gl.setData(r);
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    try {
      gl.setData(await reviewApi.clearGitlab());
      toast("GitLab removed.");
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  return (
    <Section title="GitLab" sub="For merge requests in the Code Review plugin: the server address and a token with the api and read_user scopes (GitLab › Preferences › Access tokens).">
      <div className="panel">
        <div className="panel-body gh-row">
          {gl.data?.set ? <Pill tone="ok">connected to {gl.data.host}{gl.data.hint ? ` · token ${gl.data.hint}` : ""}</Pill> : <Pill tone="idle">not connected</Pill>}
          <input type="url" className="inline-input" id="gl-url" aria-label="GitLab server" placeholder="https://gitlab.yourcompany.com"
            value={shownUrl} onChange={(e) => setUrl(e.target.value)} />
          <input type="password" className="inline-input" id="gl-token" aria-label="GitLab token" placeholder={gl.data?.set ? "token (leave empty to keep it)" : "glpat-…"}
            autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
          <button type="button" className="btn sm primary" disabled={busy || !shownUrl.trim() || (!gl.data?.set && !token.trim())} onClick={() => void save()}>Save</button>
          {gl.data?.set && <button type="button" className="btn sm ghost" onClick={() => void remove()}>Remove</button>}
        </div>
      </div>
    </Section>
  );
}
