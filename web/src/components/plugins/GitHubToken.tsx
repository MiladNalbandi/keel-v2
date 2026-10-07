// v0.10.0 Connections › GitHub: the token keel's Git plugin pushes and opens pull requests with (its own, not the
// Copilot login). Saved encrypted; only its last 3 characters are ever shown.

import { useState } from "react";
import { api, errorParts } from "../../api";
import { useApp, useLoad } from "../../state";
import { Section } from "../page";
import { Pill } from "../ui";

export function GitHubSection() {
  const gh = useLoad("github", () => api.github(), { live: false });
  const { toast } = useApp();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
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
  return (
    <Section
      title="GitHub"
      sub="The token keel pushes branches and opens pull requests with (Git plugin, and a flow's PR step). It needs repo access."
    >
      <div className="panel">
        <div className="panel-body gh-row">
          {gh.data?.set ? (
            <Pill tone="ok">
              token set{gh.data.hint ? ` ${gh.data.hint}` : ""}
              {gh.data.from === "env" ? " (from the environment)" : ""}
            </Pill>
          ) : (
            <Pill tone="idle">no token</Pill>
          )}
          <input
            type="password"
            className="inline-input"
            id="gh-token"
            aria-label="GitHub token"
            placeholder="ghp_… or github_pat_…"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <button
            type="button"
            className="btn sm primary"
            disabled={busy || !value.trim()}
            onClick={() => void save()}
          >
            Save
          </button>
          {gh.data?.from === "keel" && (
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => void remove()}
            >
              Remove
            </button>
          )}
        </div>
      </div>
    </Section>
  );
}
