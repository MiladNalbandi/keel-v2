// The editor of one database connection (Control › Databases, a project's Databases): its name, environment and where
// it is available, then two tabs. General: the type of database (with its logo), how keel connects (host and port, a
// socket, a URL, or a variable of the project's .env), the user, the password and where keel keeps it, the default
// database (a list after a test) and the URL the fields make (never with the password). Access: the projects that see
// it, what keel's agents may do (staging and prod: read only for agents, always), and "ask me first".
// The Database plugin's part (plugins/db): it imports only react and @keel/web-sdk.

import { useEffect, useId, useState } from "react";
import { errorParts, ErrorBox, Tabs, useApp } from "@keel/web-sdk";
import {
  builtUrl,
  bodyOf,
  CHANGE_ENVS,
  DEFAULT_PORT,
  dbxApi,
  ENVS,
  type Access,
  type ConnectMode,
  type DbDraft,
  type DbEntry,
  type DbTest,
  type Env,
  testLine,
} from "./dbConfig";
import { DRIVERS, DRIVER_LABEL, DriverLogo, type Driver } from "./logos";

type Project = { id: string; name: string };

const MODES: [ConnectMode, string][] = [
  ["host", "Host & port"],
  ["socket", "Socket"],
  ["url", "URL only"],
  ["env", "From .env"],
];

const ADDRESS = new Set(["driver", "host", "port", "username", "default_db", "connect_mode", "url_override", "socket_path"]);

const ACCESS: { v: Access; title: string; sub: string }[] = [
  { v: "none", title: "Nothing", sub: "Only you, in the Database tool." },
  {
    v: "private",
    title: "Run queries, results only to you",
    sub: "Agents see the tables and write the SQL. The rows come to you, not to the agent or its AI provider.",
  },
  {
    v: "read",
    title: "Read only",
    sub: "SELECT and the schema. At most 200 rows, 15 s per query.",
  },
  {
    v: "write_approve",
    title: "Read and change, after you approve",
    sub: "Each change waits in the Inbox.",
  },
];

export function DbEditor({
  draft: start,
  entry,
  pid,
  projectName,
  projects,
  onSaved,
  onCancel,
  onRemove,
}: {
  draft: DbDraft;
  entry?: DbEntry;
  /** the chosen project: "This project" and .env and SQLite paths are its */
  pid?: string;
  projectName?: string;
  projects: Project[];
  onSaved: (saved: DbEntry, close: boolean) => void;
  onCancel: () => void;
  onRemove?: () => void;
}) {
  const { toast } = useApp();
  const [d, setD] = useState<DbDraft>(start);
  const [tab, setTab] = useState<"general" | "access">("general");
  const [noteOpen, setNoteOpen] = useState(!!start.note);
  const [busy, setBusy] = useState<"" | "test" | "save">("");
  const [test, setTest] = useState<DbTest | null>(null);
  const [dbs, setDbs] = useState<string[] | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const [sure, setSure] = useState(false);
  const urlTyped = d.connect_mode === "url";
  const id = useId();
  const f = (k: string) => `${id}-${k}`;

  useEffect(() => {
    setD(start);
    setTest(null);
    setDbs(null);
    setErr(null);
    setSure(false);
    setNoteOpen(!!start.note);
  }, [start]);

  // a connection keel found keeps its address (and the password the api reads again from the project) until the person
  // changes one of the address's own fields
  const set = (patch: Partial<DbDraft>) =>
    setD((x) => ({ ...x, ...patch, ...(x.import_url && Object.keys(patch).some((k) => ADDRESS.has(k)) ? { import_url: undefined } : {}) }));
  const pickDriver = (driver: Driver) =>
    set({
      driver,
      port:
        d.port && d.port !== String(DEFAULT_PORT[d.driver] ?? "")
          ? d.port
          : String(DEFAULT_PORT[driver] ?? ""),
      connect_mode: driver === "sqlite" ? "host" : d.connect_mode,
    });
  const pickEnv = (env: Env) =>
    set({
      env,
      agent_access:
        !CHANGE_ENVS.includes(env) && d.agent_access === "write_approve"
          ? "read"
          : d.agent_access,
    });
  const changeLocked = !CHANGE_ENVS.includes(d.env);
  const shownUrl = d.import_url ?? (urlTyped ? d.url_override : builtUrl(d));

  const runTest = async () => {
    setBusy("test");
    setTest(null);
    try {
      const t = await dbxApi.test(bodyOf(d), pid);
      setTest(t);
      if (t.ok && d.driver !== "sqlite") {
        try {
          setDbs(
            (await dbxApi.serverDatabases(bodyOf(d), pid)).databases.map(
              (x) => x.name,
            ),
          );
        } catch {
          setDbs(null); // the list is a help; the field stays a text box
        }
      }
    } catch (e) {
      setTest({
        ok: false,
        ...((p) => ({ error: p.message, hint: p.hint }))(errorParts(e)),
      });
    } finally {
      setBusy("");
    }
  };

  const save = async (close: boolean) => {
    setBusy("save");
    setErr(null);
    try {
      const body = bodyOf(d);
      const saved = !entry
        ? d.scope === "global"
          ? await dbxApi.createGlobal(body)
          : await dbxApi.createIn(pid ?? "", body)
        : entry.source === "global" || d.scope !== entry.source
          ? await dbxApi.updateGlobal(
              entry.id,
              {
                ...body,
                scope: d.scope,
                project: d.scope === "project" ? pid : undefined,
              },
              pid,
            )
          : await dbxApi.updateIn(pid ?? entry.project ?? "", entry.id, body);
      toast(`${saved.name} is saved.`);
      onSaved(saved, close);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy("");
    }
  };

  const others = projects.filter((p) => !d.projects.includes(p.id));
  const nameOf = (pidOf: string) =>
    projects.find((p) => p.id === pidOf)?.name ?? pidOf;

  return (
    <form
      className="dbx-editor"
      aria-label={entry ? `Edit ${entry.name}` : "New database connection"}
      onSubmit={(e) => {
        e.preventDefault();
        void save(true);
      }}
    >
      <div className="dbx-head">
        <div className="dbx-headrow">
          <div className="dbx-field dbx-name">
            <label htmlFor={f("name")}>Name</label>
            <input
              id={f("name")}
              className="dbx-input"
              value={d.name}
              placeholder="LOC 8.4"
              autoComplete="off"
              onChange={(e) => set({ name: e.target.value })}
            />
          </div>
          <div className="dbx-field">
            <label htmlFor={f("env")}>Environment</label>
            <select
              id={f("env")}
              className={`dbx-input dbx-env-select t-${d.env}`}
              value={d.env}
              onChange={(e) => pickEnv(e.target.value as Env)}
            >
              {ENVS.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </div>
          <div className="dbx-field">
            <span className="dbx-label" id={f("scope")}>
              Available in
            </span>
            <div className="dbx-seg" role="group" aria-labelledby={f("scope")}>
              <button
                type="button"
                aria-pressed={d.scope === "global"}
                onClick={() => set({ scope: "global" })}
              >
                All projects
              </button>
              <button
                type="button"
                aria-pressed={d.scope === "project"}
                disabled={!pid}
                title={pid ? undefined : "Choose a project first"}
                onClick={() => set({ scope: "project" })}
              >
                This project
              </button>
            </div>
          </div>
        </div>
        {noteOpen ? (
          <div className="dbx-field">
            <label htmlFor={f("note")}>Note</label>
            <textarea
              id={f("note")}
              className="dbx-input dbx-note"
              rows={2}
              maxLength={500}
              value={d.note}
              placeholder="What it is for, who owns it"
              onChange={(e) => set({ note: e.target.value })}
            />
          </div>
        ) : (
          <button
            type="button"
            className="dbx-link"
            onClick={() => setNoteOpen(true)}
          >
            + Add a note (what it is for, who owns it)
          </button>
        )}
        {d.found_in && <p className="sub dbx-found">Found in {d.found_in}.</p>}
        <Tabs
          label="Connection settings"
          value={tab}
          onChange={setTab}
          options={[
            ["general", "General"],
            ["access", "Access"],
          ]}
        />
      </div>

      {tab === "general" ? (
        <div className="dbx-grid" role="tabpanel" aria-label="General">
          <span className="dbx-label" id={f("driver")}>
            Database
          </span>
          <div
            className="dbx-span dbx-types"
            role="group"
            aria-labelledby={f("driver")}
          >
            {DRIVERS.map((x) => (
              <button
                key={x}
                type="button"
                className="dbx-type"
                aria-pressed={d.driver === x}
                onClick={() => pickDriver(x)}
              >
                <DriverLogo driver={x} label={false} />
                {DRIVER_LABEL[x]}
              </button>
            ))}
          </div>

          {d.driver === "sqlite" ? (
            <>
              <label htmlFor={f("file")}>File</label>
              <div className="dbx-span dbx-stack">
                <input
                  id={f("file")}
                  className="dbx-input mono"
                  value={d.default_db}
                  placeholder="data/app.db"
                  onChange={(e) => set({ default_db: e.target.value })}
                />
                <span className="sub">
                  A path in the project's folder, or a whole path.
                </span>
              </div>
            </>
          ) : (
            <>
              <span className="dbx-label" id={f("mode")}>
                Connect with
              </span>
              <div className="dbx-span">
                <div
                  className="dbx-seg"
                  role="group"
                  aria-labelledby={f("mode")}
                >
                  {MODES.map(([m, l]) => (
                    <button
                      key={m}
                      type="button"
                      aria-pressed={d.connect_mode === m}
                      onClick={() =>
                        set({
                          connect_mode: m,
                          url_override:
                            m === "url" && !d.url_override
                              ? builtUrl(d)
                              : d.url_override,
                        })
                      }
                    >
                      {l}
                    </button>
                  ))}
                </div>
              </div>

              {d.connect_mode === "host" && (
                <>
                  <label htmlFor={f("host")}>Host</label>
                  <input
                    id={f("host")}
                    className="dbx-input"
                    value={d.host}
                    placeholder="localhost"
                    onChange={(e) => set({ host: e.target.value })}
                  />
                  <label htmlFor={f("port")} className="dbx-right">
                    Port
                  </label>
                  <input
                    id={f("port")}
                    className="dbx-input mono"
                    inputMode="numeric"
                    value={d.port}
                    placeholder={String(DEFAULT_PORT[d.driver] ?? "")}
                    onChange={(e) =>
                      set({ port: e.target.value.replace(/[^0-9]/g, "") })
                    }
                  />
                </>
              )}
              {d.connect_mode === "socket" && (
                <>
                  <label htmlFor={f("socket")}>Socket</label>
                  <input
                    id={f("socket")}
                    className="dbx-input dbx-span mono"
                    value={d.socket_path}
                    placeholder={
                      d.driver === "postgres"
                        ? "/var/run/postgresql"
                        : "/tmp/mysql.sock"
                    }
                    onChange={(e) => set({ socket_path: e.target.value })}
                  />
                </>
              )}
              {d.connect_mode === "env" && (
                <>
                  <label htmlFor={f("envvar")}>Variable</label>
                  <div className="dbx-span dbx-stack">
                    <input
                      id={f("envvar")}
                      className="dbx-input mono"
                      value={d.env_var}
                      placeholder="DATABASE_URL"
                      onChange={(e) => set({ env_var: e.target.value })}
                    />
                    <span className="sub">
                      keel reads it from the project's .env each time it
                      connects, and keeps nothing.
                    </span>
                  </div>
                </>
              )}

              {d.connect_mode !== "env" && (
                <>
                  <label htmlFor={f("auth")}>Sign in</label>
                  <select
                    id={f("auth")}
                    className="dbx-input dbx-span"
                    value={d.sign_in}
                    onChange={(e) =>
                      set({ sign_in: e.target.value as DbDraft["sign_in"] })
                    }
                  >
                    <option value="password">User &amp; password</option>
                    <option value="none">No password</option>
                  </select>

                  {!urlTyped && (
                    <>
                      <label htmlFor={f("user")}>User</label>
                      <input
                        id={f("user")}
                        className="dbx-input dbx-span"
                        value={d.username}
                        autoComplete="off"
                        onChange={(e) => set({ username: e.target.value })}
                      />
                    </>
                  )}

                  {d.sign_in === "password" && (
                    <>
                      <label htmlFor={f("pw")}>Password</label>
                      <div className="dbx-pw">
                        <svg
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          aria-hidden="true"
                        >
                          <rect x="5" y="11" width="14" height="10" rx="2" />
                          <path d="M8 11V7a4 4 0 0 1 8 0v4" />
                        </svg>
                        <input
                          id={f("pw")}
                          type="password"
                          autoComplete="new-password"
                          value={d.password}
                          aria-describedby={f("pwnote")}
                          placeholder={
                            entry?.has_password ? "saved (type to change)" : ""
                          }
                          onChange={(e) => set({ password: e.target.value })}
                        />
                      </div>
                      <label htmlFor={f("keep")} className="dbx-right">
                        Keep
                      </label>
                      <select
                        id={f("keep")}
                        className="dbx-input"
                        value={d.keep_password}
                        onChange={(e) =>
                          set({
                            keep_password: e.target
                              .value as DbDraft["keep_password"],
                          })
                        }
                      >
                        <option value="keel">In keel (encrypted)</option>
                        <option value="session">This session only</option>
                        <option value="never">Never</option>
                      </select>
                      <span className="dbx-gap" />
                      <span id={f("pwnote")} className="dbx-span sub">
                        {d.keep_password === "keel"
                          ? "Stored encrypted with keel's key. Agents never see it."
                          : d.keep_password === "session"
                            ? "Kept in memory until keel stops. Agents never see it."
                            : "Not kept: keel will ask for it each time (coming soon); until then it cannot connect."}
                      </span>
                    </>
                  )}

                  {!urlTyped && (
                    <>
                      <label htmlFor={f("db")}>Default database</label>
                      {dbs ? (
                        <select
                          id={f("db")}
                          className="dbx-input dbx-span"
                          value={d.default_db}
                          onChange={(e) => set({ default_db: e.target.value })}
                        >
                          <option value="">(none: the server's default)</option>
                          {[
                            ...new Set([
                              ...(d.default_db ? [d.default_db] : []),
                              ...dbs,
                            ]),
                          ].map((x) => (
                            <option key={x} value={x}>
                              {x}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          id={f("db")}
                          className="dbx-input dbx-span"
                          value={d.default_db}
                          placeholder="Test the connection to pick one"
                          onChange={(e) => set({ default_db: e.target.value })}
                        />
                      )}
                    </>
                  )}

                  <label htmlFor={f("url")}>URL</label>
                  <div className="dbx-span dbx-stack">
                    <input
                      id={f("url")}
                      className={`dbx-input mono${urlTyped ? "" : " dbx-built"}`}
                      value={shownUrl}
                      autoComplete="off"
                      spellCheck={false}
                      aria-describedby={f("urlnote")}
                      onChange={(e) =>
                        set({
                          connect_mode: "url",
                          url_override: e.target.value,
                        })
                      }
                    />
                    <span id={f("urlnote")} className="sub">
                      {urlTyped
                        ? "Your own URL: the fields come from it. Without the password (type that above)."
                        : "Built from the fields above. Type your own to override them (no password in the URL)."}
                    </span>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="dbx-access" role="tabpanel" aria-label="Access">
          <section className="dbx-card" aria-labelledby={f("where")}>
            <h3 id={f("where")}>Where it is available</h3>
            {d.scope === "global" ? (
              <>
                <label
                  className={`dbx-choice${d.projects.length === 0 ? " on" : ""}`}
                >
                  <input
                    type="radio"
                    name={f("scopeall")}
                    checked={d.projects.length === 0}
                    onChange={() => set({ projects: [] })}
                  />
                  <span>
                    <b>All projects</b>
                    <span className="sub">
                      Every project sees it. A project can still turn it off for
                      itself.
                    </span>
                  </span>
                </label>
                <label
                  className={`dbx-choice${d.projects.length > 0 ? " on" : ""}`}
                >
                  <input
                    type="radio"
                    name={f("scopeall")}
                    checked={d.projects.length > 0}
                    onChange={() =>
                      set({
                        projects: pid
                          ? [pid]
                          : projects.slice(0, 1).map((p) => p.id),
                      })
                    }
                  />
                  <span>
                    <b>Only these projects</b>
                  </span>
                </label>
                {d.projects.length > 0 && (
                  <div
                    className="dbx-chips"
                    role="group"
                    aria-label="Projects that see it"
                  >
                    {d.projects.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className="dbx-chip"
                        aria-label={`Remove ${nameOf(p)}`}
                        onClick={() =>
                          set({ projects: d.projects.filter((x) => x !== p) })
                        }
                      >
                        {nameOf(p)} <span aria-hidden="true">✕</span>
                      </button>
                    ))}
                    {others.length > 0 && (
                      <select
                        className="dbx-chip add"
                        aria-label="Add a project"
                        value=""
                        onChange={(e) =>
                          e.target.value &&
                          set({ projects: [...d.projects, e.target.value] })
                        }
                      >
                        <option value="">+ project</option>
                        {others.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
              </>
            ) : (
              <p className="sub">
                Only in {projectName ?? "this project"}. Pick "All projects" at
                the top to share it.
              </p>
            )}
            <p className="sub">
              A project's own connection with the same name wins inside that
              project.
            </p>
          </section>

          <section className="dbx-card" aria-labelledby={f("agents")}>
            <h3 id={f("agents")}>What keel's agents may do</h3>
            {ACCESS.map((a) => {
              const off = a.v === "write_approve" && changeLocked;
              return (
                <label
                  key={a.v}
                  className={`dbx-choice${d.agent_access === a.v ? " on" : ""}${off ? " off" : ""}`}
                >
                  <input
                    type="radio"
                    name={f("access")}
                    value={a.v}
                    checked={d.agent_access === a.v}
                    disabled={off}
                    aria-describedby={off ? f("lock") : undefined}
                    onChange={() => set({ agent_access: a.v })}
                  />
                  <span>
                    <b>{a.title}</b>
                    <span className="sub">{a.sub}</span>
                  </span>
                </label>
              );
            })}
            {changeLocked && (
              <p className="sub" id={f("lock")}>
                Not for {d.env}: {d.env} is read only for agents, always.
              </p>
            )}
          </section>

          <section className="dbx-card" aria-labelledby={f("safety")}>
            <h3 id={f("safety")}>Safety</h3>
            <ul className="dbx-checks">
              <li>Password stored encrypted, never shown to agents</li>
              <li>Prod and staging are read only for agents, always</li>
              <li>Dangerous functions blocked (file read, terminate)</li>
            </ul>
            <label className="dbx-check">
              <input
                type="checkbox"
                checked={d.ask_first}
                onChange={(e) => set({ ask_first: e.target.checked })}
              />
              Ask me before an agent's first query in a flow
            </label>
          </section>
        </div>
      )}

      {err && (
        <div className="dbx-errwrap">
          <ErrorBox error={err} />
        </div>
      )}
      <div className="dbx-foot">
        <button
          type="button"
          className="btn sm"
          onClick={() => void runTest()}
          disabled={busy !== ""}
        >
          {busy === "test" ? "Testing…" : "Test connection"}
        </button>
        <span
          role="status"
          aria-live="polite"
          className={`dbx-result${test ? (test.ok ? " ok" : " bad") : ""}`}
        >
          {test ? testLine(test) : ""}
          {test && !test.ok && test.hint ? (
            <span className="sub"> {test.hint}</span>
          ) : null}
        </span>
        <div className="dbx-actions">
          {entry &&
            onRemove &&
            (sure ? (
              <>
                <button
                  type="button"
                  className="btn sm danger"
                  onClick={onRemove}
                >
                  Remove {entry.name}
                </button>
                <button
                  type="button"
                  className="btn sm ghost"
                  onClick={() => setSure(false)}
                >
                  Keep it
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => setSure(true)}
              >
                Remove
              </button>
            ))}
          <button type="button" className="btn sm ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn sm"
            disabled={busy !== ""}
            onClick={() => void save(false)}
          >
            Apply
          </button>
          <button
            type="submit"
            className="btn sm primary"
            disabled={busy !== "" || !d.name.trim()}
          >
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </form>
  );
}
