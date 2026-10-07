// v0.10.0 Connections › Databases (the Database plugin): the project's databases. keel suggests what the project names
// (docker compose, .env.example, Spring's config, a SQLite file); you confirm, keel tests it, and the password is saved
// encrypted. local and test databases may get changes of data (always after your OK); staging and prod are read only.

import { useState } from "react";
import { api, errorParts, type DbConnection, type DbSuggestion } from "../../api";
import { clock } from "../../format";
import { useApp, useLoad } from "../../state";
import { Section } from "../page";
import { ErrorBox, Pill } from "../ui";

const KIND: Record<string, string> = { postgres: "PostgreSQL", mysql: "MySQL", sqlite: "SQLite" };
const ENVS = ["local", "test", "staging", "prod"] as const;
const EXAMPLE = "postgres://user:password@localhost:5432/app"; // keel:allow-secret

export function DatabasesSection({ pid }: { pid: string }) {
  const plugins = useLoad(pid ? `plugins:${pid}` : null, () => api.plugins(pid), { live: false });
  const on = !!plugins.data?.find((p) => p.name === "db")?.enabled;
  const conns = useLoad(on ? `db:${pid}` : null, () => api.dbConnections(pid), { live: false });
  const sugg = useLoad(on ? `db-suggest:${pid}` : null, () => api.dbSuggest(pid), { live: false });
  const { toast } = useApp();
  const [form, setForm] = useState<{ name: string; url: string; env: string; source?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  if (!on) return null;

  const reload = () => { void conns.reload(); void sugg.reload(); };
  const save = async () => {
    if (!form) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.dbAdd(pid, form);
      toast(r.test.ok ? `${r.connection.name} is connected: ${r.connection.server}, ${r.connection.tables} tables.`
        : `${r.connection.name} is saved, but keel could not connect. ${r.test.error ?? ""}`);
      setForm(null);
      reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const act = async (what: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      toast(what);
      reload();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const useSuggestion = (s: DbSuggestion) => setForm({ name: (conns.data?.length ? s.name : "local").replace(/[^A-Za-z0-9_-]/g, "") || "local",
    url: s.url, env: "local", source: s.source });

  return (
    <Section title="Databases" sub="The Database plugin: the project's databases for KeelBot, workflows and Map › Query. Staging and prod are read only, always."
      actions={!form && <button type="button" className="btn sm primary" onClick={() => setForm({ name: conns.data?.length ? "" : "local", url: "", env: "local" })}>Add a database</button>}>
      {conns.error && <ErrorBox error={conns.error} onRetry={reload} />}
      <div className="db-list">
        {(conns.data ?? []).map((c) => <ConnectionCard key={c.name} c={c}
          onTest={() => void act(`Tested ${c.name}.`, () => api.dbTest(pid, c.name))}
          onEnv={(env) => void act(`${c.name} is now ${env}.`, () => api.dbUpdate(pid, c.name, { env }))}
          onRemove={() => void act(`${c.name} removed.`, () => api.dbDelete(pid, c.name))} />)}
        {conns.data && !conns.data.length && !form && <p className="sub">No database yet. Add one, or use one keel found below.</p>}
      </div>
      {!form && !!sugg.data?.length && (
        <div className="db-sugg" role="group" aria-label="Databases keel found in the project">
          <span className="sub">keel found these in the project:</span>
          {sugg.data.map((s) => (
            <div key={s.shown} className="db-sugg-row">
              <span className="mono">{s.shown}</span><span className="sub">{KIND[s.kind]} · {s.source}</span>
              <button type="button" className="btn sm" onClick={() => useSuggestion(s)}>Use it</button>
            </div>
          ))}
        </div>
      )}
      {form && (
        <form className="db-form" onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Add a database">
          <label><span>Name</span><input className="inline-input" id="db-name" value={form.name} placeholder="local"
            onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="db-url"><span>Address</span><input className="inline-input mono" id="db-url" value={form.url} autoComplete="off"
            placeholder={EXAMPLE} onChange={(e) => setForm({ ...form, url: e.target.value })} /></label>
          <label><span>Kind</span><select id="db-env" value={form.env} onChange={(e) => setForm({ ...form, env: e.target.value })}>
            {ENVS.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
          <p className="sub db-note">keel runs in Docker: <code>localhost</code> means your computer. The password is saved encrypted and never shown again.</p>
          {err && <ErrorBox error={err} />}
          <div className="row">
            <button type="submit" className="btn sm primary" disabled={busy || !form.url.trim()}>{busy ? "Testing…" : "Save and test"}</button>
            <button type="button" className="btn sm ghost" onClick={() => { setForm(null); setErr(null); }}>Cancel</button>
          </div>
        </form>
      )}
    </Section>
  );
}

function ConnectionCard({ c, onTest, onEnv, onRemove }: { c: DbConnection; onTest: () => void; onEnv: (env: string) => void; onRemove: () => void }) {
  const [sure, setSure] = useState(false);
  return (
    <article className="db-card" aria-label={`Database ${c.name}`}>
      <div className="db-head">
        <span className={`db-dot ${c.ok === true ? "ok" : c.ok === false ? "bad" : ""}`} aria-hidden="true" />
        <b>{c.name}</b>
        <Pill tone="idle">{c.server ?? KIND[c.kind]}</Pill>
        {c.ok === true && <Pill tone="ok">connected · {c.tables} tables</Pill>}
        {c.ok === false && <Pill tone="bad">cannot connect</Pill>}
        <Pill tone={c.can_change ? "run" : "warn"}>{c.env}: {c.can_change ? "changes with your OK" : "read only, always"}</Pill>
      </div>
      <span className="mono sub db-shown">{c.shown}{c.source ? ` · found in ${c.source}` : ""}</span>
      {c.ok === false && c.error && <p className="db-err">{c.error}</p>}
      <div className="row">
        <button type="button" className="btn sm" onClick={onTest}>Test</button>
        <select aria-label={`Kind of ${c.name}`} value={c.env} onChange={(e) => onEnv(e.target.value)}>
          {ENVS.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        {sure ? <><button type="button" className="btn sm danger" onClick={onRemove}>Remove {c.name}</button>
          <button type="button" className="btn sm ghost" onClick={() => setSure(false)}>Keep it</button></>
          : <button type="button" className="btn sm ghost" onClick={() => setSure(true)}>Remove</button>}
        {c.checked_at && <span className="sub">tested {clock(c.checked_at, false)}</span>}
      </div>
    </article>
  );
}
