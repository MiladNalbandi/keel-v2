// v0.10.0 Tools › Plugins: keel's installable plugins (Database, Git) as cards. Each says what it adds; Install turns it
// on for this project (or for every project), Turn off takes it away. A plugin's tools then show up in "Who may use
// what" on the same page, its blocks in Workflows, its section in Connections, and its panel on the Map or Code page.

import { useState } from "react";
import { api, errorParts, type Plugin } from "../../api";
import { useApp, type Loaded } from "../../state";
import { Pill } from "../ui";

const WHERE: Record<string, string> = {
  connections: "Connections",
  map: "Map › Query",
  code: "Code › Git",
  workflows: "Workflows blocks",
  keelbot: "KeelBot",
  inbox: "Inbox",
};

export function PluginCards({
  pid,
  plugins,
}: {
  pid: string;
  plugins: Loaded<Plugin[]>;
}) {
  const { toast, project } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const name = project?.name ?? pid;
  const set = async (p: Plugin, enabled: boolean, scope: "project" | "all") => {
    setBusy(p.name);
    try {
      plugins.setData(await api.setPlugin(pid, p.name, enabled, scope));
      toast(
        enabled
          ? `${p.title} is on for ${scope === "all" ? "every project" : name}.`
          : `${p.title} is off for ${name}.`,
      );
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setBusy(null);
    }
  };
  if (!plugins.data?.length) return null;
  return (
    <div className="pl-cards">
      {plugins.data.map((p) => (
        <article
          key={p.name}
          className={`pl-card${p.enabled ? " on" : ""}`}
          aria-label={`${p.title} plugin`}
        >
          <div className="pl-head">
            <b>{p.title}</b>
            {p.enabled ? (
              <Pill tone="ok">
                on{p.scope === "all" ? " for every project" : ` for ${name}`}
              </Pill>
            ) : (
              <Pill tone="idle">not installed</Pill>
            )}
          </div>
          <p className="pl-about">{p.description}</p>
          <dl className="pl-adds">
            {!!p.tools?.read?.length && (
              <>
                <dt>KeelBot tools</dt>
                <dd className="mono">{p.tools.read.join(", ")}</dd>
              </>
            )}
            {!!p.actions.length && (
              <>
                <dt>Workflow blocks</dt>
                <dd className="mono">
                  {p.actions.map((a) => a.name).join(", ")}
                </dd>
              </>
            )}
            {!!p.commands?.length && (
              <>
                <dt>Commands</dt>
                <dd className="mono">
                  {p.commands.map((c) => `/${c.name}`).join(" ")}
                </dd>
              </>
            )}
            <dt>Shows in</dt>
            <dd>{p.shows_in.map((w) => WHERE[w] ?? w).join(" · ")}</dd>
          </dl>
          <div className="row">
            {p.enabled ? (
              <button
                type="button"
                className="btn sm"
                disabled={busy === p.name}
                onClick={() => void set(p, false, "project")}
              >
                Turn off
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="btn sm primary"
                  disabled={busy === p.name}
                  onClick={() => void set(p, true, "project")}
                >
                  Install for {name}
                </button>
                <button
                  type="button"
                  className="btn sm"
                  disabled={busy === p.name}
                  onClick={() => void set(p, true, "all")}
                >
                  For every project
                </button>
              </>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}
