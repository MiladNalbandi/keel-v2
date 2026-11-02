// keel 0.20.1 Settings › Resources (engine runtime/resources.py): what keel uses and may use. The agents at once and their
// priority are keel's own and count at once. Memory and CPUs are Docker's: a container cannot change its own limits, so this
// page saves what you want, and keel2 takes it at its next start, or at once with `keel2 limits` on the computer that runs keel.
//
//   Now       Memory 1.0 GB used, limit 8.0 GB · CPUs 4 · Agents 1 running
//   Limits    Memory [6g] CPUs [4] [Save]    waits for keel2: `keel2 limits` applies it now
//   Agents    Agents at the same time [auto (4)] · low CPU priority

import { useEffect, useState } from "react";
import { errorParts } from "../../api";
import { CopyBlock } from "../../components/StartSteps";
import { helpApi, type ResourcesBody } from "../../helpApi";
import { useLoad } from "../../state";
import { SettingsFrame } from "./frame";

const gigabytes = (n: number) => `${(n / 1073741824).toFixed(1)} GB`;

export function ResourcesTab() {
  const r = useLoad("resources", () => helpApi.resources(), { live: false });
  const [err, setErr] = useState<string | null>(null);
  const [memory, setMemory] = useState("");
  const [cpus, setCpus] = useState("");
  const v = r.data;
  useEffect(() => {
    if (!v) return;
    setMemory(v.wanted?.memory ?? "");
    setCpus(v.wanted?.cpus ?? "");
  }, [v?.wanted?.memory, v?.wanted?.cpus]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (body: ResourcesBody) => {
    setErr(null);
    try {
      r.setData(await helpApi.saveResources(body));
    } catch (e) {
      setErr(errorParts(e).message);
    }
  };
  const waiting = v?.wanted && (v.wanted.memory || v.wanted.cpus);
  return (
    <SettingsFrame tab="resources" title="Resources" line="How much of this computer keel may use. For the whole keel, in every project.">
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {(r.error || err) && <p className="ss-note t-err" role="alert">{err ?? r.error?.message}</p>}
        {v && (
          <>
            <section className="ss-cardp" aria-labelledby="res-now-h">
              <h2 id="res-now-h" className="ss-h2">Now</h2>
              <p className="ss-row" style={{ margin: 0 }} data-testid="res-now">
                Memory: {v.memory_used != null ? gigabytes(v.memory_used) : "not known"} used, limit{" "}
                {v.memory_limit != null ? gigabytes(v.memory_limit) : "none"} · CPUs: {v.cpu_limit ?? "no limit"}
                {v.cpus ? ` (this machine has ${v.cpus})` : ""} · Agents: {v.running} running
                {v.waiting ? `, ${v.waiting} waiting` : ""}
              </p>
              {v.oom_kills > 0 && (
                <p className="ss-note t-err" role="alert">
                  The memory limit stopped a process {v.oom_kills} time{v.oom_kills === 1 ? "" : "s"} since keel started. An agent or a build may have failed
                  for that reason: raise the memory below, or run fewer agents at once.
                </p>
              )}
            </section>
            {v.in_container !== false && (
              <section className="ss-cardp" aria-labelledby="res-limits-h">
                <h2 id="res-limits-h" className="ss-h2">Memory and CPU limits</h2>
                <p className="ss-help">
                  Empty keeps what keel has now. <span className="ss-mono">off</span> means no limit. By default keel may use half of what Docker has.
                </p>
                <div className="ss-row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
                  <label className="ss-label">
                    Memory
                    <input className="ss-input" value={memory} placeholder="6g" onChange={(e) => setMemory(e.target.value)} />
                  </label>
                  <label className="ss-label">
                    CPUs
                    <input className="ss-input" value={cpus} placeholder="4" onChange={(e) => setCpus(e.target.value)} />
                  </label>
                  <button type="button" className="ss-btn p" onClick={() => void save({ memory, cpus })}>
                    Save the limits
                  </button>
                </div>
                {waiting ? (
                  <>
                    <p className="ss-row" role="status" style={{ margin: "12px 0 0" }}>
                      Saved, not applied yet. A container cannot change its own limits, so keel applies them the next time it starts (
                      <span className="ss-mono">keel2 restart</span> or <span className="ss-mono">keel2 update</span>). To apply them now, with no restart, run this
                      on the computer that runs keel:
                    </p>
                    <CopyBlock text="keel2 limits" label="keel2 limits command" />
                  </>
                ) : null}
              </section>
            )}
            <section className="ss-cardp" aria-labelledby="res-agents-h">
              <h2 id="res-agents-h" className="ss-h2">Agents</h2>
              <label className="ss-label">
                Agents at the same time
                <select className="ss-input" value={v.max_agents} onChange={(e) => void save({ max_agents: Number(e.target.value) })}>
                  <option value={0}>auto ({v.auto_agents})</option>
                  {Array.from({ length: 8 }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                  {v.max_agents > 8 && <option value={v.max_agents}>{v.max_agents}</option>}
                </select>
              </label>
              <span className="ss-help">
                Every flow and KeelBot together, on this keel. The others wait for a free place, and a flow takes longer. Each agent uses about 300-500 MB.
              </span>
              <label className="acc-check">
                <input type="checkbox" checked={v.low_priority} onChange={(e) => void save({ low_priority: e.target.checked })} />
                Run agents at a low CPU priority, so this computer stays quick
              </label>
            </section>
          </>
        )}
      </div>
    </SettingsFrame>
  );
}
