// keel 0.28 Add features: what this keel can do, as cards. Each feature (a plugin) says what it adds, where it is on
// (Off · This project · All projects, the same control as Settings › Plugins), and pins its page to the menu. At the
// top, Build your own feature starts a flow with keel's "plugin" workflow: keel writes the plugin, you approve each
// step, and it shows here and in the menu like keel's own.
//
//   ┌ Build your own feature: [name] [what it should do] [Build it ↩] ┐
//   PROJECT  ┌ Review ─────────── [Off|This project|All] ┐ ┌ Map … ┐
//            │ Review a branch or a pull request…         │
//            │ Open · ◇ Pin to menu                        │

import { useMemo, useState } from "react";
import { api, errorParts, type Plugin } from "../api";
import { marketApi, pendingOf, type InstalledPlugin } from "../marketplaceApi";
import { openStartFlow, useMenu } from "../components/ShellNav";
import { usePins } from "../nav";
import { ProjectPlace } from "./PluginProjects";
import { hashFor } from "../routes";
import type { PageProps } from "../sdk/registry";
import { useApp, useLoad, type Loaded } from "../state";

/** a plugin's page id when it is not the plugin's own name */
const PAGE_OF: Record<string, string> = { code: "repo", keelbot: "helper", product: "initiatives" };
/** what kind of feature, for the groups */
const KIND_OF: Record<string, string> = {
  code: "Project", keelbot: "Project", review: "Project", map: "Project", graph: "Project", wiki: "Project", library: "Project",
  tasks: "Work", goals: "Work", org: "Work", desk: "Work", answers: "Work", product: "Work",
  record: "Watch", cluster: "Watch", ci: "Watch", supervisor: "Watch",
  git: "Connect", db: "Connect", slack: "Connect", jira: "Connect", "github-issues": "Connect", links: "Connect", notify: "Connect",
};
const KINDS = ["Project", "Work", "Watch", "Connect", "More"];

export function FeaturesPage({ pid }: PageProps) {
  const { toast } = useApp();
  const m = useMenu();
  const { pinned, toggle } = usePins();
  const installed = useLoad("features-installed", () => marketApi.installed(), { live: false });
  const perProject = useLoad(pid ? `plugins:${pid}` : null, () => api.plugins(pid), { live: false });
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [what, setWhat] = useState("");
  const [err, setErr] = useState(false);

  const groups = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = (installed.data?.plugins ?? []).filter(
      (p) => p.status !== "removed" && (!t || `${p.title ?? p.name} ${p.summary ?? ""}`.toLowerCase().includes(t)),
    );
    return KINDS.map((k) => ({ kind: k, items: list.filter((p) => (KIND_OF[p.name] ?? "More") === k) })).filter(
      (g) => g.items.length > 0,
    );
  }, [installed.data, q]);

  // 0.31 the features whose change applies at keel's next start (as Settings › Plugins shows them)
  const pend = installed.data ? pendingOf(installed.data) : null;
  const waiting = new Set(pend?.pending ? (pend.changes ?? []).map((c) => c.name) : []);

  const build = () => {
    if (!name.trim() || !what.trim()) {
      setErr(true);
      return;
    }
    openStartFlow(`New keel feature: ${name.trim()}`, {
      workflowId: "plugin",
      request: `Build a keel plugin called "${name.trim()}". ${what.trim()}`,
    });
  };

  return (
    <div className="feat2">
      <header className="page-head2 rise">
        <div>
          <h1>Add features</h1>
          <p>Turn keel's features on or off, pin their pages to the menu, or describe a new one and keel builds it.</p>
        </div>
        <label htmlFor="ft-q" className="sr-only">
          Find a feature
        </label>
        <input id="ft-q" className="inp2" placeholder="Find a feature" value={q} onChange={(e) => setQ(e.target.value)} />
      </header>

      <section className="ft-new rise r1" aria-labelledby="ft-new-h">
        <div className="ft-new-h">
          <span className="pill p-acc">New</span>
          <h2 id="ft-new-h">Build your own feature</h2>
          <span className="sub">keel writes it as a plugin with a flow (workflow “plugin”), and you approve each step.</span>
        </div>
        <div className="ft-new-row">
          <div className="fld2">
            <label htmlFor="ft-name">Name</label>
            <input id="ft-name" className="inp2" placeholder="e.g. Release notes" value={name} onChange={(e) => { setName(e.target.value); setErr(false); }} />
          </div>
          <div className="fld2 grow">
            <label htmlFor="ft-what">What should it do?</label>
            <input
              id="ft-what"
              className="inp2"
              placeholder="e.g. A page that writes release notes from the flows merged this week"
              value={what}
              onChange={(e) => { setWhat(e.target.value); setErr(false); }}
              onKeyDown={(e) => e.key === "Enter" && build()}
            />
          </div>
          <button type="button" className="btn primary" onClick={build}>
            Build it <kbd>↩</kbd>
          </button>
        </div>
        {err && <p className="h2-err">Give it a name and say what it should do.</p>}
      </section>

      {installed.error && <p className="badc">Could not read the features: {installed.error.message}</p>}
      {groups.map((g, i) => (
        <section key={g.kind} className={`ft-sec rise r${Math.min(i + 2, 5)}`} aria-label={g.kind}>
          <h2 className="lbl2">{g.kind}</h2>
          <div className="ft-grid">
            {g.items.map((p) => (
              <FeatureCard
                key={p.name}
                p={p}
                pid={pid}
                project={perProject.data?.find((x) => x.name === p.name)}
                projectLoaded={perProject}
                page={m.index.has(PAGE_OF[p.name] ?? p.name) ? PAGE_OF[p.name] ?? p.name : null}
                href={m.href}
                pinned={pinned}
                toggle={(id) => toast(toggle(id) ? `${m.label(id)} is pinned to the menu.` : `${m.label(id)} is not pinned any more.`)}
                onKeelOn={() => void installed.reload()}
                waits={waiting.has(p.name)}
              />
            ))}
          </div>
        </section>
      ))}
      {installed.data && groups.length === 0 && <p className="h2-none">No feature matches “{q}”.</p>}
      <p className="sub ft-more">
        More features are in <a href={`${hashFor("settings", "plugins")}?tab=market`}>Settings › Plugins › Marketplace</a>.
      </p>
    </div>
  );
}

function FeatureCard({
  p, pid, project, projectLoaded, page, href, pinned, toggle, onKeelOn, waits = false,
}: {
  p: InstalledPlugin;
  pid: string;
  project: Plugin | undefined;
  projectLoaded: Loaded<Plugin[]>;
  page: string | null;
  href: (id: string) => string;
  pinned: (id: string) => boolean;
  toggle: (id: string) => void;
  onKeelOn: () => void;
  waits?: boolean;
}) {
  const keelOn = p.on !== false;
  const on = !!page;
  const state = p.status === "restart" ? "after a restart" : keelOn ? (p.status === "loaded" ? "on" : p.status ?? "on") : "off";
  return (
    <div className={`ft-card lift${keelOn ? "" : " off"}`}>
      <div className="ft-top">
        <span className="ft-ini" aria-hidden="true">
          {(p.title ?? p.name).slice(0, 1).toUpperCase()}
        </span>
        <b>{p.title ?? p.name}</b>
        {/beta/i.test(p.version ?? "") && <span className="pill p-warn">Beta</span>}
        <span className={`pill ${state === "on" ? "p-ok" : state === "off" ? "" : "p-warn"}`}>{state}</span>
      </div>
      <p className="ft-what">{p.summary ?? "A keel plugin."}</p>
      {project && pid ? (
        <ProjectPlace pid={pid} plugin={project} loaded={projectLoaded} keelOn={keelOn} onKeelOn={onKeelOn} loadedNow={p.status === "loaded"} />
      ) : (
        <KeelSwitch p={p} keelOn={keelOn} onChanged={onKeelOn} waits={waits} />
      )}
      {on && page && (
        <div className="ft-acts">
          <a className="btn sm" href={href(page)}>
            Open
          </a>
          <button type="button" className="btn sm ghost" aria-pressed={pinned(page)} onClick={() => toggle(page)}>
            {pinned(page) ? "◆ Pinned" : "◇ Pin to menu"}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * 0.31 a feature that is on or off for the whole keel (no per-project place, such as keel Product): On · Off right on
 * its card (PUT /api/plugins/<name>). A feature with an api or engine part loads at keel's next start: the card says so
 * and offers Restart keel now (POST /api/plugins/restart).
 */
function KeelSwitch({ p, keelOn, onChanged, waits: pending }: { p: InstalledPlugin; keelOn: boolean; onChanged: () => void; waits: boolean }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const waits = pending || p.status === "restart";
  const set = async (on: boolean) => {
    if (on === keelOn || busy) return;
    setBusy(true);
    setErr("");
    try {
      await marketApi.setOn(p.name, on);
      toast(`${p.title ?? p.name} is ${on ? "on" : "off"}.`);
      onChanged();
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  const restart = async () => {
    setBusy(true);
    setErr("");
    try {
      const a = await marketApi.restart(true);
      toast(a.restarting ? "keel restarts now. This page comes back in about 30 seconds." : "keel restarts by itself when no agent step runs.");
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ft-switch">
      <div className="seg2" role="group" aria-label={`${p.title ?? p.name} for this keel`}>
        <button type="button" aria-pressed={!keelOn} disabled={busy} onClick={() => void set(false)}>
          Off
        </button>
        <button type="button" aria-pressed={keelOn} disabled={busy} onClick={() => void set(true)}>
          On
        </button>
      </div>
      {waits ? (
        <span className="sub">
          Applies when keel restarts.{" "}
          <button type="button" className="btn sm" disabled={busy} onClick={() => void restart()}>
            Restart keel now
          </button>
        </span>
      ) : (
        <span className="sub">{keelOn ? "On for the whole keel." : "Off. Turn it on to use it."}</span>
      )}
      {err && (
        <span className="sub t-err" role="alert">
          {err}
        </span>
      )}
    </div>
  );
}
