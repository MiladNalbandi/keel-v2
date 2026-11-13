// v0.10.0 Connections › GitHub: the token keel's Git plugin pushes and opens pull requests with. #101 GitHub Models
// (the GitHub Copilot row of Settings › Models & keys) uses it too when no GITHUB_TOKEN is saved there. Saved encrypted; only its last 3 characters are ever shown. It is keel's core (step 3): a flow's ship
// step opens the pull request with it too, with or without the Git plugin (corePages.ts puts it in Connections).
// v0.18.30 "Sign in with GitHub": a one-time code and a link (the device flow) instead of a
// pasted token; the result is who is connected (login, scopes, host). Pasting a token stays as the second way.
// v0.18.31 the sign-in opens a right-side panel of its own (LoginDrawer); the card shows only state.
// keel 0.18.57 Settings › Connections › GitHub: one card — its state and Sign in / Sign out, then "or paste a token".

import { useState } from "react";
import { api, errorParts, type GitHubLoginView } from "../api";
import { useApp, useLoad } from "../state";
import { LoginDrawer, type LoginAdapter } from "./LoginDrawer";
import { TonePill } from "./settingsUi";

/** The drawer for "Sign in with GitHub": the shared login steps with the GitHub host field and the SSO hint. */
function GitHubLoginHelper({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [host, setHost] = useState("github.com");
  const adapter: LoginAdapter<GitHubLoginView> = {
    start: () => api.githubLogin(host.trim() || "github.com"),
    poll: (id) => api.githubLoginState(id),
    cancel: (id) => api.githubLoginCancel(id),
  };
  return (
    <LoginDrawer
      title="Set up GitHub login"
      intro="keel asks GitHub for a one-time code. You open the link, type the code and allow access. keel saves the token by itself. A GitHub Enterprise host works too."
      note="If your organisation uses SSO, authorize it when GitHub asks."
      adapter={adapter}
      titles={["Start the login", "Open this page and sign in", "Type this code on that page", "Saved"]}
      doneText={(v) => `connected as ${v.login ?? "GitHub"}${v.scopes.length ? ` · ${v.scopes.join(", ")}` : ""} · ${v.host}`}
      onDone={onSaved}
      onClose={onClose}
      before={(login) => (
        <label className="ss-label" htmlFor="gh-host">
          GitHub host
          <input id="gh-host" className="ss-input mono" placeholder="github.com" autoComplete="off" value={host}
            disabled={!!login && !["failed", "cancelled"].includes(login.status)} onChange={(e) => setHost(e.target.value)} />
        </label>
      )}
    />
  );
}

export function GitHubSection() {
  const gh = useLoad("github", () => api.github(), { live: false });
  const { toast } = useApp();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [helper, setHelper] = useState(false);

  const signOut = async () => {
    try {
      await api.githubSignOut();
      toast("Signed out of GitHub. keel's token is removed.");
      void gh.reload();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const save = async () => {
    setBusy(true);
    try {
      const r = await api.setSecret("GITHUB_REPO_TOKEN", value);
      setValue("");
      toast(`GitHub token saved (${r.hint}).`);
      void gh.reload();
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    try {
      await api.deleteSecret("GITHUB_REPO_TOKEN");
      toast("GitHub token removed.");
      void gh.reload();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const d = gh.data;
  const who = d?.login;
  const detail = !d ? "Checking…"
    : who ? `connected as ${who}${d.scopes?.length ? ` · ${d.scopes.join(", ")}` : ""} · ${d.host ?? "github.com"}`
      : d.set ? `token set${d.hint ? ` ${d.hint}` : ""}${d.from === "env" ? " (from the environment)" : ""}`
        : "Flows cannot push or open pull requests yet.";
  return (
    <section className="ss-cardp" aria-labelledby="c-gh" data-testid="github">
      {helper && <GitHubLoginHelper onClose={() => setHelper(false)} onSaved={() => void gh.reload()} />}
      <div className="ss-sechead">
        <div>
          <h2 id="c-gh" className="ss-h2 ss-title-row">GitHub <span className="ss-tag acc">every project</span></h2>
          <p className="ss-sub">The token keel pushes branches and opens pull requests with (Git plugin, and a flow's PR step). It needs repo access.</p>
        </div>
      </div>
      <div className="ss-inline" style={{ gap: "8px 12px" }}>
        {d && (d.set ? <TonePill tone="ok">connected</TonePill> : <TonePill tone="off">no token</TonePill>)}
        <span className="ss-meta" style={{ flex: "1 1 240px", minWidth: 0 }} data-testid="github-state">{detail}</span>
        {d?.from === "keel" && who && <button type="button" className="ss-btn sm q" onClick={() => void signOut()}>Sign out</button>}
        {d && !who && (
          <button type="button" className={`ss-btn sm${d.set ? "" : " p"}`} onClick={() => setHelper(true)}>Sign in with GitHub</button>
        )}
      </div>
      <div className="ss-inline" style={{ paddingTop: 12, borderTop: "1px solid var(--border)" }}>
        <label htmlFor="gh-token" className="ss-meta">or paste a token</label>
        <input type="password" className="ss-input mono" id="gh-token" aria-label="GitHub token" placeholder="ghp_… or github_pat_…" autoComplete="off"
          value={value} onChange={(e) => setValue(e.target.value)} style={{ flex: "1 1 220px", maxWidth: 360 }}
          onKeyDown={(e) => e.key === "Enter" && value.trim() && void save()} />
        <button type="button" className="ss-btn sm" style={{ minHeight: 40 }} disabled={busy || !value.trim()} onClick={() => void save()}>Save</button>
        {d?.from === "keel" && !who && <button type="button" className="ss-btn sm q" onClick={() => void remove()}>Remove</button>}
      </div>
    </section>
  );
}
