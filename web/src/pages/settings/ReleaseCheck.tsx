// Settings › General › Flow and gates: "Whole test suite before push", one project's own. Required (keel's default):
// ship runs verify release and a push waits for a fresh, passing release verdict. Off: CI runs the whole suite, ship
// skips the step and a push only shows a warning. keel writes release.required in the project's .keel/config.yml
// (PUT /projects/{pid}/release-check); agents never edit that file.

import { useEffect, useState } from "react";
import { api, errorParts, type ReleaseCheck } from "../../api";
import { Seg } from "../../components/settingsUi";
import { ErrorBox } from "../../components/ui";
import { settingsHash } from "../../routes";
import type { SettingsSectionProps } from "../../sdk/slots";
import { useApp } from "../../state";

const LABEL = "Whole test suite before push";

function help(v: ReleaseCheck | null): string {
  if (!v) return "Reading the project's .keel/config.yml…";
  if (!v.configured) return "This project has no .keel/config.yml yet: keel init writes it, then this switch works.";
  return v.required
    ? "keel runs the whole test suite before a push, and the push waits until it passes."
    : "Off: your CI runs the whole suite. Ship skips verify release and a push shows a warning instead of waiting.";
}

export function ReleaseCheckRow({ pid, scope }: SettingsSectionProps) {
  const { projects, toast } = useApp();
  const [view, setView] = useState<ReleaseCheck | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const own = scope === "project" && !!pid;
  useEffect(() => {
    let gone = false;
    setView(null);
    setErr(null);
    if (!own) return;
    api.releaseCheck(pid).then((v) => { if (!gone) setView(v); }).catch((e) => { if (!gone) setErr(e); });
    return () => { gone = true; };
  }, [pid, own]);

  if (!own) {
    // a setting of one project (its .keel/config.yml), never of Every project
    const name = projects.find((p) => p.id === pid)?.name ?? pid;
    return (
      <div className="ss-set" data-testid="row-release_check">
        <div className="ss-set-l">
          <b>{LABEL}</b>
          <span className="ss-help">
            Set per project, in its .keel/config.yml.
            {pid && <> <a href={settingsHash("general", pid)}>Change it for {name}</a>.</>}
          </span>
        </div>
      </div>
    );
  }

  const save = (required: boolean) => {
    if (!view || required === view.required) return;
    setBusy(true);
    setErr(null);
    api.setReleaseCheck(pid, required)
      .then((v) => {
        setView(v);
        toast(v.required ? "Saved: the whole test suite runs before a push" : "Saved: the whole test suite is off for pushes (CI runs it)");
      })
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  return (
    <div className={`ss-set${view && !view.required ? " is-warn" : ""}`} data-key="release_check" data-testid="row-release_check">
      <div className="ss-set-l">
        <span className="ss-title-row"><b id="set-release_check-lab">{LABEL}</b></span>
        <span className="ss-help" id="set-release_check-help">{help(view)}</span>
        <span className="ss-set-src">saved in .keel/config.yml (release.required)</span>
      </div>
      <div className="ss-set-c" aria-busy={busy || undefined}>
        {view?.configured && (
          <Seg
            label={LABEL}
            value={view.required ? "required" : "off"}
            options={[["required", "required"], ["off", "off (CI runs it)"]]}
            onChange={(v) => save(v === "required")}
            disabled={busy}
          />
        )}
        {err != null && <ErrorBox error={errorParts(err)} />}
      </div>
    </div>
  );
}
