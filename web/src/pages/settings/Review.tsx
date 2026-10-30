// keel 0.18.65 Settings › Review, for one project: what "Find problems" on the Review page does (how many keel reviewers
// read the change, their focus, model and agent, the lenses each one goes through), the checker ("is the reviewer
// right?"), how a confirmed finding is fixed, and the model of the flows' last review. Kept in the project's
// .keel/config.yml under `review:` (a teammate's keel uses the same), lenses in .keel/lenses/<name>.md. Every change
// saves at once (a text field when you leave it); "Back to keel's default" takes the block out again.
//
//   Reviewers  [1][2][3][4]       ┌ A · correctness and security ┐ ┌ B · tests … ┐ ┌ C · … ┐
//   Lenses     On · Lens · From · Reviewer · View/Edit           + New lens
//   Checking and fixing   the checker's model · fix with quick | small | change | ask
//   The last review       project default (sonnet) → the final review uses opus · high
//   On the Review page    "Two keel reviewers read the change (A: …, B: …)…"   [Back to keel's default]

import { useCallback, useEffect, useState } from "react";
import { errorParts, type Model } from "../../api";
import { ModelPicker } from "../../components/ModelPicker";
import { useSlot } from "../../sdk/registry";
import { SLOTS, type SettingsSectionItem } from "../../sdk/slots";
import { useApp } from "../../state";
import { SettingsFrame } from "./frame";
import {
  findText,
  LETTERS,
  modelText,
  NEW_REVIEWER,
  reviewSetup,
  slug,
  type Depth,
  type LensFile,
  type ModelChoice,
  type ReviewConfig,
  type Reviewer,
  type ReviewSetup,
} from "./reviewSetup";

const PICK = "pick";
const isModel = (v: unknown): v is Model => !!v && typeof v === "object" && "provider" in (v as object);
const short = (m: Model | null | undefined) => (m ? m.model : "keel's");

/** A model setting: project default, stronger, or a model you pick; its effort; and the model it will really use. */
function ModelField({
  id,
  label,
  value,
  effort,
  base,
  uses,
  stronger = true,
  flowOwn = false,
  efforts,
  onChange,
}: {
  id: string;
  label: string;
  value: ModelChoice | "flow";
  effort: string | null;
  base: Model | null;
  uses: Model | null;
  stronger?: boolean;
  flowOwn?: boolean;
  efforts: string[];
  onChange: (value: ModelChoice | "flow", effort: string | null) => void;
}) {
  const sel = isModel(value) ? PICK : value;
  const fallback: Model = base ?? { provider: "claude", mode: "subscription", model: "sonnet" };
  return (
    <div className="ss-rv-model">
      <div className="ss-fields">
        <label className="ss-label">
          {label}
          <select
            id={id}
            className="ss-input"
            value={sel}
            onChange={(e) => {
              const v = e.target.value;
              onChange(v === PICK ? { ...fallback, effort: undefined } : (v as ModelChoice | "flow"), effort);
            }}
          >
            {flowOwn ? (
              <option value="flow">the flow's own</option>
            ) : (
              <option value="default">project default ({short(base)})</option>
            )}
            {stronger && <option value="stronger">stronger (Opus at high effort for Claude)</option>}
            <option value={PICK}>another model…</option>
          </select>
        </label>
        {!flowOwn && (
          <label className="ss-label">
            Effort
            <select
              className="ss-input"
              aria-label={`${label}: effort`}
              value={effort ?? ""}
              onChange={(e) => onChange(value, e.target.value || null)}
            >
              <option value="">the model's own</option>
              {efforts.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {isModel(value) && (
        <ModelPicker id={`${id}-pick`} value={value} effort={false} onChange={(m) => onChange(m, effort)} />
      )}
      {uses && (
        <span className="ss-help" data-testid={`${id}-uses`}>
          {sel === "default"
            ? `uses ${modelText(uses)}`
            : `project default (${short(base)}) → uses ${modelText(uses)}`}
        </span>
      )}
    </div>
  );
}

export function ReviewTab({ pid }: { pid: string }) {
  const { toast } = useApp();
  const [view, setView] = useState<ReviewSetup | null>(null);
  const [revs, setRevs] = useState<Reviewer[]>([]);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<LensFile | null>(null);
  const [edit, setEdit] = useState("");
  // 0.18.74 the plugins' sections that belong here (the review plugin's Code Review threads), for this project
  const parts = useSlot<SettingsSectionItem>(SLOTS.settingsSection).filter((x) => x.page === "review");

  const take = useCallback((v: ReviewSetup) => {
    setView(v);
    setRevs(v.config.reviewers.map((r) => ({ ...r, lenses: [...r.lenses] })));
  }, []);

  useEffect(() => {
    let live = true;
    reviewSetup
      .view(pid)
      .then((v) => live && take(v))
      .catch((e) => live && setError(errorParts(e)));
    return () => {
      live = false;
    };
  }, [pid, take]);

  const save = async (patch: Partial<ReviewConfig> | { reset: true }, said = "Saved in .keel/config.yml.") => {
    setBusy(true);
    setError(null);
    try {
      take(await reviewSetup.save(pid, patch));
      toast(said);
    } catch (e) {
      setError(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  if (!view)
    return (
      <SettingsFrame tab="review" title="Review" line="What Find problems on the Review page does, and keel's last review of a flow.">
        {error ? (
          <p className="ss-cardp ss-warn-text" role="alert">
            {error.message}
          </p>
        ) : (
          <p className="ss-sub">Loading…</p>
        )}
      </SettingsFrame>
    );

  const known = new Set(view.lenses.map((l) => l.name));
  const efforts = view.limits.efforts;
  // what is saved: the reviewers on screen, with only lenses that have a file
  const clean = (list: Reviewer[]) => list.map((r) => ({ ...r, lenses: r.lenses.filter((n) => known.has(n)) }));
  const saveRevs = (list: Reviewer[], said?: string) => {
    setRevs(list);
    void save({ reviewers: clean(list) }, said);
  };
  const setCount = (n: number) => {
    if (n === revs.length) return;
    const next = n > revs.length
      ? [...revs, ...Array.from({ length: n - revs.length }, () => ({ ...NEW_REVIEWER, lenses: [] }))]
      : revs.slice(0, n);
    saveRevs(next, `${n} reviewer${n === 1 ? "" : "s"} now.`);
  };
  // a name or focus is saved when you leave its field, and only when it changed
  const blurSave = () => {
    if (JSON.stringify(clean(revs)) !== JSON.stringify(clean(view.config.reviewers))) saveRevs(revs);
  };
  const patchRev = (i: number, p: Partial<Reviewer>, now = true) => {
    const next = revs.map((r, j) => (j === i ? { ...r, ...p } : r));
    if (now) saveRevs(next);
    else setRevs(next);
  };
  const ownerOf = (lens: string) => revs.findIndex((r) => r.lenses.includes(lens));
  const moveLens = (lens: string, to: number | null) => {
    const next = revs.map((r, j) => ({
      ...r,
      lenses: j === to ? [...r.lenses.filter((n) => n !== lens), lens] : r.lenses.filter((n) => n !== lens),
    }));
    saveRevs(next);
  };
  const openLens = async (name: string) => {
    try {
      const f = await reviewSetup.lens(pid, name);
      setOpen(f);
      setEdit(f.text);
    } catch (e) {
      setError(errorParts(e));
    }
  };
  const agents = view.agents.filter((a) => a.custom || /review|auditor/.test(a.id));
  const cfg = view.config;
  const readOnly = !view.configured;

  return (
    <SettingsFrame
      tab="review"
      title="Review"
      line={
        <>
          How keel reviews a change: “Find problems” on the Review page, a PR review and your uncommitted changes all
          run one review flow. Pick how deep it looks; it follows your project's own rules first. For this project.
        </>
      }
    >
      {readOnly && (
        <p className="ss-cardp ss-warn-text" role="alert">
          This project has no <span className="ss-mono">.keel/config.yml</span> yet: keel keeps these settings there.
          Run keel init (Flow page) first.
        </p>
      )}
      {error && (
        <p className="ss-cardp ss-warn-text" role="alert">
          {error.message}
          {error.hint ? ` ${error.hint}` : ""}
        </p>
      )}

      <section className="ss-cardp" aria-labelledby="rv-how-h">
        <h2 id="rv-how-h" className="ss-h2">
          How a review runs
        </h2>
        <div role="radiogroup" aria-label="Find problems runs" className="ss-rv-radios">
          <label>
            <input
              type="radio"
              name="rv-runs"
              checked={(cfg.find_runs ?? "harness") === "harness"}
              disabled={busy || readOnly}
              onChange={() => void save({ find_runs: "harness" })}
            />
            <span>
              <b>Your coding harness</b> · the project's default model (Claude Code, Codex, Copilot CLI or OpenCode)
              reviews the change in one session of its own, then a fresh run checks each serious claim (keel's default)
            </span>
          </label>
          <label>
            <input
              type="radio"
              name="rv-runs"
              checked={cfg.find_runs === "flow"}
              disabled={busy || readOnly}
              onChange={() => void save({ find_runs: "flow" })}
            />
            <span>
              <b>The review flow</b> · every step is on the Flow page: prepare, build and lint, reviewers, a second
              look at each claim, the report
            </span>
          </label>
          <label>
            <input
              type="radio"
              name="rv-runs"
              checked={cfg.find_runs === "classic"}
              disabled={busy || readOnly}
              onChange={() => void save({ find_runs: "classic" })}
            />
            <span>
              <b>Classic</b> · the reviewers below as KeelBot runs, then the checker
            </span>
          </label>
        </div>
        {view.depths && (
          <div className="ss-rv-depths" role="radiogroup" aria-label="Review depth">
            {(view.limits.depths ?? (["quick", "standard", "full"] as Depth[])).map((d) => {
              const info = view.depths![d];
              return (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={(cfg.depth ?? "standard") === d}
                  className="ss-rv-depth"
                  disabled={busy || readOnly}
                  onClick={() => void save({ depth: d }, `${info.title} reviews from now on.`)}
                >
                  <b>{info.title}</b>
                  <span className="ss-meta">
                    {info.tokens} · {info.time}
                  </span>
                  <span className="ss-help">{info.what}</span>
                </button>
              );
            })}
          </div>
        )}
        {view.standards && (
          <div className="ss-rv-standards" role="group" aria-label="Your standards">
            <b>Your standards: every reviewer reads them first</b>
            <span className="ss-help">
              Read from the change's base branch, so a change cannot rewrite its own rules.
            </span>
            <dl>
              <dt>Rules</dt>
              <dd className="ss-mono">
                {view.standards.rules.length
                  ? view.standards.rules.join(" · ")
                  : "none found (AGENTS.md, CLAUDE.md, CONTRIBUTING.md, .cursor/rules/ …)"}
              </dd>
              <dt>Your lenses</dt>
              <dd className="ss-mono">
                {view.standards.lenses.length ? view.standards.lenses.join(" · ") : "none yet (.keel/lenses/, see Lenses below)"}
              </dd>
              <dt>Stack</dt>
              <dd>{view.standards.stack.length ? view.standards.stack.join(" · ") : "not detected"}</dd>
            </dl>
          </div>
        )}
      </section>

      {cfg.find_runs === "classic" && (
      <section className="ss-cardp" aria-labelledby="rv-revs-h">
        <h2 id="rv-revs-h" className="ss-h2">
          Reviewers
        </h2>
        <div className="ss-actions">
          <span className="ss-meta">How many read the change</span>
          <div className="ss-seg" role="radiogroup" aria-label="Number of reviewers">
            {Array.from({ length: view.limits.reviewers }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={revs.length === n}
                disabled={busy || readOnly}
                onClick={() => setCount(n)}
              >
                {n}
              </button>
            ))}
          </div>
          <span className="ss-help">≈ 25k tokens per reviewer on a 600-line change · keel's default is 2</span>
        </div>
        <div className="ss-rv-grid">
          {revs.map((r, i) => (
            <div key={i} className="ss-rv-card" aria-label={`Reviewer ${LETTERS[i]}`} role="group">
              <b>
                {LETTERS[i]} · {r.name || "reviewer"}
              </b>
              <label className="ss-label">
                Name
                <input
                  className="ss-input"
                  value={r.name}
                  maxLength={60}
                  disabled={readOnly}
                  onChange={(e) => patchRev(i, { name: e.target.value }, false)}
                  onBlur={blurSave}
                />
              </label>
              <label className="ss-label">
                Focus
                <input
                  className="ss-input"
                  value={r.focus}
                  maxLength={600}
                  disabled={readOnly}
                  onChange={(e) => patchRev(i, { focus: e.target.value }, false)}
                  onBlur={blurSave}
                />
              </label>
              <ModelField
                id={`rv-model-${LETTERS[i]}`}
                label="Model"
                value={r.model}
                effort={r.effort}
                base={view.models.default}
                uses={view.uses.reviewers[i] ?? null}
                efforts={efforts}
                onChange={(m, e) => patchRev(i, { model: m as ModelChoice, effort: e })}
              />
              <label className="ss-label">
                Agent (its role and skills)
                <select
                  className="ss-input"
                  value={r.agent ?? ""}
                  disabled={readOnly}
                  onChange={(e) => patchRev(i, { agent: e.target.value || null })}
                >
                  <option value="">none: keel's review prompt only</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.custom ? `your agent: ${a.id}` : `${a.id} (keel)`}
                    </option>
                  ))}
                  {r.agent && !agents.some((a) => a.id === r.agent) && <option value={r.agent}>{r.agent}</option>}
                </select>
              </label>
              <span className="ss-help">
                {r.lenses.filter((n) => known.has(n)).length} lens{r.lenses.filter((n) => known.has(n)).length === 1 ? "" : "es"}
              </span>
            </div>
          ))}
        </div>
      </section>

      )}

      <section className="ss-cardp" aria-labelledby="rv-lenses-h">
        <div className="ss-sechead">
          <div>
            <h2 id="rv-lenses-h" className="ss-h2">
              Lenses
            </h2>
            <p className="ss-sub">
              A lens is a checklist a reviewer goes through. keel's own come with keel; your project's live in{" "}
              <span className="ss-mono">.keel/lenses/&lt;name&gt;.md</span>.
            </p>
          </div>
          <button type="button" className="ss-btn p" disabled={readOnly} onClick={() => setCreating((c) => !c)}>
            + New lens
          </button>
        </div>
        <div className="ss-rv-table">
          <table>
            <thead>
              <tr>
                <th>On</th>
                <th>Lens</th>
                <th>From</th>
                <th>Reviewer</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {view.lenses.map((l) => {
                const at = ownerOf(l.name);
                return (
                  <tr key={l.name}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`${l.title} on`}
                        checked={at >= 0}
                        disabled={busy || readOnly}
                        onChange={(e) => moveLens(l.name, e.target.checked ? 0 : null)}
                      />
                    </td>
                    <td>
                      <div>{l.title}</div>
                      <div className="ss-help">{l.description}</div>
                    </td>
                    <td>{l.source === "keel" ? <span className="ss-meta">keel</span> : <span className="ss-mono">{l.path}</span>}</td>
                    <td>
                      <select
                        className="ss-input"
                        aria-label={`Reviewer for ${l.title}`}
                        value={at >= 0 ? String(at) : ""}
                        disabled={at < 0 || busy || readOnly}
                        onChange={(e) => moveLens(l.name, Number(e.target.value))}
                      >
                        {at < 0 && <option value="">—</option>}
                        {revs.map((_, i) => (
                          <option key={i} value={String(i)}>
                            {LETTERS[i]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button type="button" className="ss-btn link" onClick={() => void openLens(l.name)}>
                        {l.source === "project" ? "Edit" : "View"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {open && (
          <LensPanel
            file={open}
            text={edit}
            onText={setEdit}
            onClose={() => setOpen(null)}
            onSave={async () => {
              try {
                const f = await reviewSetup.saveLens(pid, open.name, edit);
                setOpen(f);
                take(await reviewSetup.view(pid));
                toast(`Saved ${f.path}.`);
              } catch (e) {
                setError(errorParts(e));
              }
            }}
          />
        )}
        {creating && (
          <NewLens
            reviewers={revs.length}
            onSave={async (title, reviewer, checklist) => {
              try {
                const r = await reviewSetup.newLens(pid, { title, reviewer, checklist });
                take(r.view);
                setCreating(false);
                toast(`Saved as ${r.lens.path}: commit it to share it with your team.`);
              } catch (e) {
                setError(errorParts(e));
              }
            }}
          />
        )}
      </section>

      <section className="ss-cardp" aria-labelledby="rv-fix-h">
        <h2 id="rv-fix-h" className="ss-h2">
          Checking and fixing a finding
        </h2>
        <div className="ss-rv-two">
          <div className="ss-rv-card" role="group" aria-label="The checker">
            <b>The checker (“is the reviewer right?”)</b>
            <span className="ss-help">A fresh run that checks every serious claim against the code before you see it.</span>
            <ModelField
              id="rv-checker"
              label="Model"
              value={cfg.checker.model}
              effort={cfg.checker.effort}
              base={view.models.default}
              uses={view.uses.checker}
              efforts={efforts}
              onChange={(m, e) => void save({ checker: { ...cfg.checker, model: m as ModelChoice, effort: e } })}
            />
            <label className="ss-rv-check">
              <input
                type="checkbox"
                checked={cfg.checker.should_fix}
                disabled={busy || readOnly}
                onChange={(e) => void save({ checker: { ...cfg.checker, should_fix: e.target.checked } })}
              />
              Check “should fix” findings too, not only blocking ones
            </label>
          </div>
          <div className="ss-rv-card" role="group" aria-label="The fix">
            <b>When the reviewer is right: fix it with</b>
            <div role="radiogroup" aria-label="Fix flow" className="ss-rv-radios">
              {(
                [
                  ["quick", "one agent, one commit, no gates; for a one-line fix"],
                  ["small", "a test first, then the fix, one gate at the end (keel's default)"],
                  ["change", "the full loop per criterion, a gate per criterion"],
                  ["ask", "ask me each time: the finding shows the three buttons"],
                ] as const
              ).map(([id, text]) => (
                <label key={id}>
                  <input
                    type="radio"
                    name="rv-fixflow"
                    checked={cfg.fix.flow === id}
                    disabled={busy || readOnly}
                    onChange={() => void save({ fix: { ...cfg.fix, flow: id } })}
                  />
                  <span>
                    <b>{id === "ask" ? "ask me each time" : id}</b> · {text.replace(/^ask me each time: /, "")}
                  </span>
                </label>
              ))}
            </div>
            <div className="ss-fields">
              <ModelField
                id="rv-fixmodel"
                label="Fix model"
                value={cfg.fix.model}
                effort={null}
                base={null}
                uses={view.uses.fix}
                stronger={false}
                flowOwn
                efforts={efforts}
                onChange={(m) => void save({ fix: { ...cfg.fix, model: m as "flow" | Model } })}
              />
              <label className="ss-label">
                Run mode
                <select
                  className="ss-input"
                  value={cfg.fix.run_mode ?? ""}
                  disabled={busy || readOnly}
                  onChange={(e) => void save({ fix: { ...cfg.fix, run_mode: e.target.value || null } })}
                >
                  <option value="">the project's</option>
                  {view.limits.run_modes.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="ss-rv-check">
              <input
                type="checkbox"
                checked={cfg.fix.one_flow_per_file}
                disabled={busy || readOnly}
                onChange={(e) => void save({ fix: { ...cfg.fix, one_flow_per_file: e.target.checked } })}
              />
              Several findings in one file: one flow for all of them
            </label>
          </div>
        </div>
      </section>

      <section className="ss-cardp" aria-labelledby="rv-final-h">
        <div>
          <h2 id="rv-final-h" className="ss-h2">
            The last review of a flow
          </h2>
          <p className="ss-sub">
            The whole-branch review at the end of a feature, small or plugin flow, and the ship review's lenses. keel runs
            them on a stronger model than the project's by default.
          </p>
        </div>
        <ModelField
          id="rv-final"
          label="Model"
          value={cfg.final.model}
          effort={cfg.final.effort}
          base={view.models.reviewer}
          uses={null}
          efforts={efforts}
          onChange={(m, e) => void save({ final: { model: m as ModelChoice, effort: e } })}
        />
        <p className="ss-help" data-testid="rv-final-line">
          project default ({short(view.models.reviewer)}) → the final review uses {modelText(view.uses.final)}
        </p>
        <p className="ss-help">
          The ship review's lenses ({view.ship_lenses_from}): <span className="ss-mono">{view.ship_lenses.join(", ")}</span>
        </p>
        <label className="ss-rv-check">
          <input
            type="checkbox"
            checked={cfg.drop_correctness}
            disabled={busy || readOnly}
            onChange={(e) => void save({ drop_correctness: e.target.checked })}
          />
          Skip the ship's correctness lens when the full-diff review passed and no production file changed since
        </label>
      </section>

      {parts.map((x) => (
        <section key={x.id} className="ss-cardp" aria-labelledby={`rv-part-${x.id}`}>
          <h2 id={`rv-part-${x.id}`} className="ss-h2">
            {x.title}
          </h2>
          <p className="ss-help">{x.sub}</p>
          {x.component && <x.component pid={pid} scope="project" />}
        </section>
      ))}

      <section className="ss-cardp" aria-labelledby="rv-page-h">
        <h2 id="rv-page-h" className="ss-h2">
          On the Review page
        </h2>
        <div className="ss-rv-preview">
          {/* 0.18.69 the flow's own text when Find problems runs the review flow (it said "Two keel reviewers…") */}
          <p data-testid="rv-find-text">{cfg.find_runs === "classic" ? findText(revs) : view.find_text}</p>
          <span className="ss-btn p" aria-hidden="true">
            Find problems
          </span>
        </div>
        <div className="ss-actions">
          <button
            type="button"
            className="ss-btn"
            disabled={busy || readOnly}
            onClick={() => void save({ reset: true }, "Back to keel's default.")}
          >
            Back to keel's default
          </button>
          <span className="ss-help">
            Stored in <span className="ss-mono">.keel/config.yml</span> under <span className="ss-mono">review:</span>, so
            a teammate's keel uses the same reviewers.
          </span>
        </div>
      </section>
    </SettingsFrame>
  );
}

function LensPanel({
  file,
  text,
  onText,
  onClose,
  onSave,
}: {
  file: LensFile;
  text: string;
  onText: (t: string) => void;
  onClose: () => void;
  onSave: () => Promise<void>;
}) {
  const own = file.source === "project";
  return (
    <div className="ss-rv-file" role="region" aria-label={`Lens file ${file.path}`}>
      <div className="ss-actions">
        <span className="ss-mono">{file.path}</span>
        <span className="ss-push">
          {own && (
            <button type="button" className="ss-btn p sm" onClick={() => void onSave()}>
              Save
            </button>
          )}
          <button type="button" className="ss-btn sm" onClick={onClose}>
            Close
          </button>
        </span>
      </div>
      {own ? (
        <textarea className="ss-input mono" rows={12} aria-label="Lens text" value={text} onChange={(e) => onText(e.target.value)} />
      ) : (
        <pre className="ss-rv-pre">{file.text}</pre>
      )}
    </div>
  );
}

function NewLens({
  reviewers,
  onSave,
}: {
  reviewers: number;
  onSave: (title: string, reviewer: string | null, checklist: string) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [who, setWho] = useState(LETTERS[Math.min(reviewers, LETTERS.length) - 1]);
  const [list, setList] = useState("");
  const name = slug(title);
  return (
    <div className="ss-rv-new" role="region" aria-label="New lens">
      <h3 className="ss-h3">New lens</h3>
      <div className="ss-fields">
        <label className="ss-label">
          Name
          <input className="ss-input" value={title} placeholder="Money rounding" onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="ss-label">
          Reviewer
          <select className="ss-input" value={who} onChange={(e) => setWho(e.target.value)}>
            {LETTERS.slice(0, reviewers).map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
            <option value="">none (off)</option>
          </select>
        </label>
      </div>
      <label className="ss-label">
        Checklist (one line per thing to check)
        <textarea
          className="ss-input"
          rows={4}
          value={list}
          placeholder={"- amounts are integers in cents, never floats\n- an order total equals the sum of its lines"}
          onChange={(e) => setList(e.target.value)}
        />
      </label>
      <div className="ss-actions">
        <button
          type="button"
          className="ss-btn p"
          disabled={!name || !list.trim()}
          onClick={() => void onSave(title.trim(), who || null, list)}
        >
          Save lens
        </button>
        <span className="ss-help">
          Saved as <span className="ss-mono">.keel/lenses/{name || "<name>"}.md</span> in the project: commit it to share
          it with your team.
        </span>
      </div>
    </div>
  );
}
