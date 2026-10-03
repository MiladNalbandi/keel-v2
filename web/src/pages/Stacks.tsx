// Stacks (Build): how each project builds and tests. A stack is one YAML file plus its skills;
// the flow never changes per language.

import { Fragment, useState } from "react";
import { api } from "../api";
import { Async, PageHead, Panel, Pill } from "../components/ui";
import { useApp, useLoad } from "../state";

export function StacksPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const stacks = useLoad(`stacks:${pid}`, () => api.stacks(pid), { live: false });
  const [sel, setSel] = useState<string | null>(null);
  return (
    <>
      <PageHead title="Stacks" sub="How each project builds and tests. A stack is one YAML file plus its skills; the flow never changes per language."
        actions={<button className="btn" type="button" onClick={async () => {
          await stacks.reload();
          const found = (stacks.data ?? []).filter((s) => s.detected).map((s) => s.name);
          toast(`Detected in ${project?.name ?? pid}: ${found.join(" · ") || "nothing"}`);
        }}>Detect again</button>} />
      <Async r={stacks} what="Reading stacks">
        {(list) => {
          const cur = list.find((s) => s.name === sel) ?? list.find((s) => s.detected) ?? list[0];
          return !list.length ? <div className="empty">No stack files found in keel. Is KEEL_HOME set?</div> : (
            <div className="grid g2">
              <div className="panel"><div className="table-wrap"><table>
                <thead><tr><th>Stack</th><th>Lane</th><th>From</th><th>Found by</th><th>In {project?.name ?? pid}</th></tr></thead>
                <tbody>
                  {list.map((x) => (
                    <tr key={x.name} className={`click ${x.name === cur?.name ? "rowsel" : ""}`} tabIndex={0} onClick={() => setSel(x.name)} onKeyDown={(e) => e.key === "Enter" && setSel(x.name)}>
                      <td><b>{x.name}</b></td><td><span className="tag">{x.lane}</span></td><td className="sub">{x.source}</td><td className="mono sub">{x.detect}</td>
                      <td>{x.detected ? <Pill tone="ok">detected</Pill> : <span className="sub">not used</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div></div>
              {cur && (
                <Panel title={<h2>{cur.name}</h2>} extra={<span className="tag">{cur.source}</span>} body="grid">
                  <div className="grid" style={{ gap: 14 }}>
                    {!cur.detected && <p className="sub" style={{ margin: 0 }}>Not found in this project. It costs nothing until a project uses it.</p>}
                    <div className="field"><span className="lab">Test layers (lowest first)</span>
                      <div className="row">{cur.layers.length ? cur.layers.map((l, i) => <span key={l} className="tag">{i + 1}. {l}</span>) : <span className="sub">none listed</span>}</div>
                      <span className="hint">The AC loop picks the lowest layer that can show the criterion.</span></div>
                    <div className="field"><span className="lab">Commands</span>
                      {cur.commands.length ? <div className="kv">{cur.commands.map((c) => <Fragment key={c.name}><span>{c.name}</span><b className="mono">{c.cmd}</b></Fragment>)}</div> : <span className="sub">none</span>}</div>
                    <div className="field"><span className="lab">Tools</span>
                      {cur.tools.length ? (
                        <div className="table-wrap"><table><thead><tr><th>Tool</th><th>Runs on</th><th>If it fails</th></tr></thead>
                          <tbody>{cur.tools.map((t) => <tr key={t.name}><td className="mono">{t.name}</td><td><span className="tag">{t.on}</span></td><td className="sub">{t.fail}</td></tr>)}</tbody></table></div>
                      ) : <span className="sub">none</span>}</div>
                    <div className="field"><span className="lab">Skills this stack gives agents</span>
                      <div className="row">{cur.skills.length ? cur.skills.map((k) => <a key={k} className="tag skilltag" href="#/skills" style={{ textDecoration: "none" }}>{k}</a>) : <span className="sub">ships its own testing skill</span>}</div></div>
                  </div>
                </Panel>
              )}
            </div>
          );
        }}
      </Async>
    </>
  );
}
