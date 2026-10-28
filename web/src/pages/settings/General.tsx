// keel 0.18.57 Settings › General: the settings for every project, and what one project changes for itself. The head's
// switch shows Every project (#/settings/general) or one project (#/settings/general/<id>); in a project a changed row
// says "changed for <project>" with Every project's value and Reset, and "Only changed" leaves just those rows. Every
// change saves at once. The sections fold; they are data (GENERAL_SECTIONS): a new keel setting is one more row in
// its section, a new section one more entry. The parts add theirs after keel's own (slot settings.section: Code Review
// threads), then This browser and (in a project) Where these are saved.

import { type ComponentType } from "react";
import { api, errorParts, type Settings } from "../../api";
import { refreshFeatures, useFeatures } from "../../addons";
import { BaseBranchPanel } from "../../components/BaseBranch";
import { BellSettings } from "../../components/Notifications";
import { Skeleton } from "../../components/page";
import { RUN_MODES, runModeAbout } from "../../components/RunMode";
import { Fold, RadioCards, Seg, Switch, SwitchRow } from "../../components/settingsUi";
import { useTheme } from "../../components/Shell";
import { ErrorBox } from "../../components/ui";
import { settingsHash } from "../../routes";
import { useSlot } from "../../sdk/registry";
import {
  SLOTS,
  type SettingsBrowserItem,
  type SettingsSectionItem,
  type SettingsSectionProps,
} from "../../sdk/slots";
import { useStored } from "../../storage";
import { useApp } from "../../state";
import { SettingsFrame } from "./frame";
import {
  SettingControl,
  SettingLine,
  show,
  useScopedSettings,
  type Row,
} from "./rows";

/** A section of General: its rows, and what it draws after them (the bell's settings, This browser). */
export type GeneralSection = {
  id: string;
  title: string;
  sub: string;
  rows: Row[];
  /** drawn after the rows (not a setting of a project: the bell, this browser), hidden while "Only changed" filters */
  after?: ComponentType<SettingsSectionProps>;
  /** a section only one scope shows */
  only?: "general" | "project";
  /** open when the page first shows (the others start folded) */
  open?: boolean;
};

/** keel's own sections of General, in order. Add a setting here: one row in its section. */
export const GENERAL_SECTIONS: GeneralSection[] = [
  {
    id: "flow",
    title: "Flow and gates",
    sub: "How much a flow does by itself, and when it stops for you.",
    open: true,
    rows: [
      {
        key: "run_mode",
        label: "Run mode (new flows)",
        def: "manual",
        kind: { t: "select", opts: RUN_MODES.map(([k, l]) => [k, l]) },
        help: (v) =>
          `${runModeAbout(String(v ?? "manual"))} A running flow can change it on the Flow page.`,
      },
      {
        key: "gates_mode",
        label: "Gate mode",
        kind: {
          t: "select",
          opts: [
            ["every-ac", "every AC"],
            ["end-of-lane", "end of lane"],
            ["end", "end of flow"],
          ],
        },
        help: "When the AC gate waits for you: after every criterion, at the end of each lane, or once at the end.",
      },
      {
        key: "fix_attempts",
        label: "Fix attempts per ladder rung",
        kind: {
          t: "select",
          opts: [
            ["1", "1"],
            ["3", "3"],
            ["5", "5"],
          ],
        },
        help: "How many times an agent may try to fix a failing setup step before it asks you.",
      },
      {
        key: "coverage_min",
        label: "Coverage needed before push",
        kind: {
          t: "select",
          opts: [
            ["0", "off"],
            ["70", "70%"],
            ["80", "80%"],
            ["90", "90%"],
          ],
        },
        help: "The test coverage the ship flow needs before it pushes.",
      },
    ],
  },
  {
    id: "git",
    title: "Git",
    sub: "Branch names, who keel's commits are by, and what happens at ship.",
    rows: [
      {
        key: "branch_pattern",
        label: "Branch name",
        kind: {
          t: "text",
          suggest: ["feat/{slug}", "{user}/{slug}", "keel/{flow}-{slug}"],
        },
        help: "{slug} is the flow's title, {user} is you, {flow} is the workflow.",
      },
      {
        key: "web_lane_worktree",
        label: "Web lane in its own worktree",
        kind: { t: "bool", on: "yes", off: "no" },
        help: "Web criteria run in a second checkout, next to the API lane.",
      },
      {
        key: "commit_coauthor",
        label: "KeelBot as co-author",
        def: true,
        kind: { t: "bool", on: "yes", off: "no" },
        help: (v) =>
          v === false
            ? "keel's commits name only their author."
            : "keel's commits end with Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>, so GitHub shows KeelBot next to you.",
      },
      {
        key: "commit_author",
        label: "Commit author",
        def: "",
        kind: { t: "text", placeholder: "Ada Lovelace <ada@example.com>" },
        help: "Who keel's commits are by, as Name <email>. Empty: the project's git name; when it has none, KeelBot.",
      },
      {
        key: "ci_on_failure",
        label: "When CI fails",
        def: "notify",
        kind: {
          t: "select",
          opts: [
            ["notify", "tell me"],
            ["fix", "start the fix flow"],
            ["quiet", "nothing"],
          ],
        },
        help: "CI/CD plugin: what keel does when a pipeline of this project fails. The fix flow runs in the project folder when it is free and on that branch; its push follows the next setting.",
      },
      {
        key: "push_pr",
        label: "Push and open PR at ship",
        kind: {
          t: "select",
          opts: [
            ["ask", "ask me"],
            ["auto", "automatic"],
            ["never", "never"],
          ],
        },
        help: "Ask you first, do it by itself, or never. The Auto run mode never pushes.",
      },
    ],
  },
  {
    // 0.18.65 the base branch and the canonical remote: files in the repo (.keel/config.yml), one project's own
    id: "base",
    title: "Base branch",
    sub: "The branch work is compared with, and the remote keel trusts for it.",
    rows: [],
    after: BaseBranchPanel,
    only: "project",
  },
  {
    id: "notify",
    title: "Notifications",
    sub: "What this project tells you about, and the bell's sound and pop-ups.",
    open: true,
    after: TheBell,
    rows: [
      {
        key: "notify",
        label: "Notify me about this project",
        kind: {
          t: "select",
          opts: [
            ["all", "all kinds"],
            ["needs_you", "only needs you"],
            ["none", "nothing"],
          ],
        },
        help: "Everything, only what waits for you, or nothing.",
      },
    ],
  },
  {
    id: "env",
    title: "Environment",
    sub: "What agents may see from this machine.",
    rows: [
      {
        key: "env_names",
        label: "Variables agents may see (names only)",
        kind: { t: "list" },
        help: "Comma-separated names. Agents get these variables; every other one is hidden.",
      },
      {
        key: "mcp",
        label: "MCP servers",
        kind: { t: "list" },
        help: "Servers this project's agents may call. Which agent may use which is on the Tools page.",
      },
      {
        key: "repo_skills",
        label: "Skills found in the repo",
        def: true,
        kind: { t: "bool", on: "load them", off: "list only" },
        help: (v) =>
          v === false
            ? "The repo's own skills (.claude/skills, .opencode/skills) are listed on the Skills page, loaded by no agent."
            : "The repo's own skills (.claude/skills, .opencode/skills), found at init and at a knowledge refresh, are loaded for the agents they are for (Skills › Found in the repo).",
      },
    ],
  },
];

/** A project's setting that another tab shows (its models, its budget rules): where the scope line points to. */
const OTHER_TAB: Record<string, [string, string]> = Object.fromEntries([
  ...["default_model", "implementer_model", "reviewer_model", "cheaper_model", "local_only"].map((k) => [k, ["models", "Models & logins"]]),
  ...["cap_tokens", "on_cap", "usage_warn", "usage_pause"].map((k) => [k, ["budget", "Budget"]]),
]);

/** The last sections: after keel's and the parts' own. */
const TAIL_SECTIONS: GeneralSection[] = [
  {
    id: "browser",
    title: "This browser",
    sub: "Only for you, in this browser. Nothing here is saved on the keel.",
    rows: [],
    after: ThisBrowser,
  },
  {
    id: "where",
    title: "Where these are saved",
    sub: "The settings on this page stay on this machine, in keel's database. The rules agents follow are files in the repo, shared with the team.",
    rows: [],
    after: WhereSaved,
    only: "project",
  },
];

/** Notifications › The bell: the same settings as the bell's drawer (they are yours, on every project). */
function TheBell() {
  return (
    <div
      className="ss-bellbox"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "14px 0 0",
        borderTop: "1px solid var(--border)",
      }}
    >
      <b style={{ fontWeight: 600 }}>The bell</b>
      <BellSettings />
    </div>
  );
}

/** This browser: the mascot, following the other keel windows (0.18.63), the parts' rows (KeelBot's own sound) and the
 *  theme. */
function ThisBrowser() {
  const { showMascot, setShowMascot, followFocus, setFollowFocus } = useApp();
  const parts = useSlot<SettingsBrowserItem>(SLOTS.settingsBrowser);
  const theme = useTheme();
  return (
    <div style={{ borderTop: "1px solid var(--border)" }}>
      <SwitchRow
        on={showMascot}
        onChange={setShowMascot}
        title="Show keel next to the bell"
        help="The little hull jumps when something arrives and says what it is. Off at first."
      />
      <SwitchRow
        on={followFocus}
        onChange={setFollowFocus}
        title="Follow other keel windows"
        help="On: this browser moves to the project and KeelBot chat you open in keel TUI or another browser, and they follow what you open here. Off: this browser stays on its own project and chat, and moves nobody else."
      />
      {parts.map((p) => (
        <p.component key={p.id} />
      ))}
      <div className="ss-set">
        <div className="ss-set-l">
          <b>Theme</b>
          <span className="ss-help">
            Light or dark, for this browser. The switch at the bottom of the
            menu does the same.
          </span>
        </div>
        <div className="ss-set-c">
          <Seg
            label="Theme"
            value={theme.dark ? "dark" : "light"}
            onChange={theme.set}
            options={[
              ["dark", "Dark"],
              ["light", "Light"],
            ]}
          />
        </div>
      </div>
    </div>
  );
}

/** Where these are saved: the project's folder and branch, keel's database, the rule files. */
function WhereSaved({ pid }: SettingsSectionProps) {
  const { projects } = useApp();
  const p = projects.find((x) => x.id === pid);
  return (
    <dl className="ss-dl">
      {p && (
        <>
          <dt>Folder</dt>
          <dd className="ss-mono">{p.root}</dd>
          <dt>Branch</dt>
          <dd className="ss-mono">{p.branch}</dd>
        </>
      )}
      <dt>These settings</dt>
      <dd>keel's database (data folder), per project</dd>
      <dt>keel's rules</dt>
      <dd>
        <span className="ss-mono">.keel/config.yml</span> and{" "}
        <span className="ss-mono">.keel/rules/</span> ·{" "}
        <a href="#/rules">Rules</a>
      </dd>
    </dl>
  );
}

/** v0.13.0 What this keel does: only when an add-on (keel Product) offers more than Dev. For everyone on this keel. */
const MODE_TEXT: Record<string, [string, string]> = {
  dev: [
    "Dev only",
    "Flows, tasks, code and workflows: keel as it is today. No Product menu, no product API.",
  ],
  product: [
    "Product only",
    "Initiatives and teams. keel still reads the code for impact, read only, but code pages are hidden. Stories go to Jira.",
  ],
  both: [
    "Product and Dev",
    "Everything. Start on a story runs keel's flow right here. Each person picks a view under the project.",
  ],
};

function KeelModePanel({
  onSaved,
  onError,
}: {
  onSaved: (text: string) => void;
  onError: (e: { message: string; hint?: string } | null) => void;
}) {
  const features = useFeatures();
  if (features.modes.length < 2) return null;
  const pick = async (mode: string) => {
    onError(null);
    try {
      await api.saveGeneralSettings({ keel_mode: mode } as Partial<Settings>);
      await refreshFeatures();
      onSaved(`Saved: keel does ${MODE_TEXT[mode]?.[0] ?? mode}`);
    } catch (e) {
      onError(errorParts(e));
    }
  };
  return (
    <section className="ss-cardp" id="ss-mode" aria-labelledby="ss-mode-h">
      <div className="ss-sechead">
        <div>
          <h2 id="ss-mode-h" className="ss-h2">
            What this keel does
          </h2>
          <p className="ss-sub">
            For everyone on this keel. Turning a part off deletes nothing.
          </p>
        </div>
      </div>
      <RadioCards
        labelledBy="ss-mode-h"
        className="grid"
        value={features.mode}
        onChange={(m) => void pick(m)}
        options={features.modes.map((m) => ({
          id: m,
          title: MODE_TEXT[m]?.[0] ?? m,
          sub: MODE_TEXT[m]?.[1] ?? "",
        }))}
      />
    </section>
  );
}

export function GeneralTab({
  pid,
  scopeArg,
}: {
  pid: string;
  scopeArg?: string;
}) {
  const { projects } = useApp();
  // the scope: Every project, or the project the link names (#/settings/general/<id>)
  const scopeId =
    scopeArg && projects.some((p) => p.id === scopeArg)
      ? scopeArg
      : scopeArg && !projects.length
        ? scopeArg
        : "";
  const isProj = !!scopeId;
  const offerId = scopeId || pid;
  const offerName = projects.find((p) => p.id === offerId)?.name ?? offerId;
  const s = useScopedSettings(scopeId, offerName);
  const [only, setOnly] = useStored<boolean>(
    `keel2.settings.only:${scopeId}`,
    false,
  );
  const [folds, setFolds] = useStored<Record<string, boolean>>(
    "keel2.settings.folds",
    {},
  );
  // the parts' sections come after keel's own (their keys are the parts' settings)
  const extra = useSlot<SettingsSectionItem>(SLOTS.settingsSection);
  const sections: GeneralSection[] = [
    ...GENERAL_SECTIONS,
    ...extra.map((x): GeneralSection => ({
      id: x.id,
      title: x.title,
      sub: x.sub,
      rows: x.rows as Row[],
      after: x.component,
    })),
    ...TAIL_SECTIONS,
  ].filter((x) => !x.only || x.only === (isProj ? "project" : "general"));

  const data = isProj ? s.proj : s.general;
  // what this project changes: General's own rows here; the models and the budget rules are on their tabs
  const allChanged = isProj && s.proj.data ? Object.keys(s.ov).filter(s.isChanged) : [];
  const here = new Set(sections.flatMap((x) => x.rows.map((r) => r.key)));
  const changed = allChanged.filter((k) => here.has(k));
  const elsewhere = allChanged.filter((k) => !here.has(k) && OTHER_TAB[k]);
  const elseTabs = [...new Map(elsewhere.map((k) => [OTHER_TAB[k][0], OTHER_TAB[k]])).values()];
  const filter = isProj && only && changed.length > 0;
  const shown = sections
    .map((x) => ({
      ...x,
      rows: filter ? x.rows.filter((r) => s.isChanged(r.key)) : x.rows,
    }))
    .filter((x) => x.rows.length || (x.after && !filter));
  const isOpen = (x: GeneralSection) => folds[x.id] ?? !!x.open;
  const go = (id: string) => {
    location.hash = settingsHash("general", id || undefined);
  };
  const status =
    s.save.state === "saving"
      ? "Saving…"
      : s.save.state === "error"
        ? s.save.text
        : s.save.state === "saved"
          ? "Saved"
          : "";

  return (
    <SettingsFrame
      tab="general"
      title="General"
      line="Settings for every project, and what one project changes for itself. Changes save as you make them."
      actions={
        <div className="ss-scope">
          <span className="ss-meta ss-small">Show settings for</span>
          <Seg
            label="Show settings for"
            value={scopeId}
            onChange={go}
            options={
              offerId
                ? [
                    ["", "Every project"],
                    [offerId, offerName],
                  ]
                : [["", "Every project"]]
            }
          />
          <span
            className={`ss-save s-${s.save.state}`}
            role="status"
            aria-live="polite"
            data-testid="save-state"
          >
            {status}
          </span>
        </div>
      }
    >
      {isProj && s.proj.data && (
        <div className="ss-scopebar">
          <span id="ovcount">
            {changed.length
              ? `${changed.length} setting${changed.length === 1 ? "" : "s"} changed for ${offerName}.`
              : "Everything follows Every project."}{" "}
            A value you do not change here comes from Every project.
            {elsewhere.length > 0 && (
              <>
                {" "}{elsewhere.length} more {elsewhere.length === 1 ? "is" : "are"} changed on{" "}
                {elseTabs.map((t, i) => (
                  <span key={t[0]}>{i > 0 && " and "}<a href={settingsHash(t[0])}>{t[1]}</a></span>
                ))}.
              </>
            )}
          </span>
          {changed.length > 0 && <Switch on={only} onChange={setOnly}>Only changed</Switch>}
        </div>
      )}
      {s.err && <ErrorBox error={s.err} />}
      {data.error ? (
        <ErrorBox error={data.error} onRetry={() => void data.reload()} />
      ) : (
        <>
          {!isProj && <KeelModePanel onSaved={s.saved} onError={s.setErr} />}
          {shown.map((sec) => {
            const n = isProj
              ? sec.rows.filter((r) => s.isChanged(r.key)).length
              : 0;
            const After = sec.after;
            return (
              <Fold
                key={sec.id}
                id={sec.id}
                title={sec.title}
                sub={sec.sub}
                open={filter || isOpen(sec)}
                testId={`sec-${sec.id}`}
                onToggle={() =>
                  setFolds((f) => ({ ...f, [sec.id]: !isOpen(sec) }))
                }
                badge={
                  n > 0 ? (
                    <span className="ss-tag acc">{n} changed</span>
                  ) : undefined
                }
              >
                {!data.data ? (
                  sec.rows.length ? (
                    <Skeleton
                      lines={sec.rows.length + 1}
                      label="Loading settings"
                    />
                  ) : null
                ) : (
                  sec.rows.map((row) => {
                    if (isProj) {
                      const p = s.proj.data!;
                      const has = s.isChanged(row.key);
                      const gv =
                        (p.general as Record<string, unknown>)[row.key] ??
                        row.def;
                      const value =
                        (has
                          ? s.ov[row.key]
                          : (p.general as Record<string, unknown>)[row.key]) ??
                        row.def;
                      return (
                        <SettingLine
                          key={row.key}
                          row={row}
                          value={value}
                          changedFor={has ? offerName : undefined}
                          from={show(row, gv)}
                          fromGeneral={!has}
                          onReset={() => void s.saveProject(row.key, null, row)}
                          control={
                            <SettingControl
                              row={row}
                              value={value}
                              onSave={(v) =>
                                void s.saveProject(row.key, v, row)
                              }
                            />
                          }
                        />
                      );
                    }
                    const value =
                      (s.general.data as Record<string, unknown>)[row.key] ??
                      row.def;
                    return (
                      <SettingLine
                        key={row.key}
                        row={row}
                        value={value}
                        control={
                          <SettingControl
                            row={row}
                            value={value}
                            onSave={(v) => void s.saveGeneral(row.key, v, row)}
                          />
                        }
                      />
                    );
                  })
                )}
                {After && !filter && (
                  <After
                    pid={scopeId || pid}
                    scope={isProj ? "project" : "general"}
                  />
                )}
              </Fold>
            );
          })}
        </>
      )}
      <p className="ss-sub">
        Models are in <a href="#/settings/models">Models &amp; logins</a>, the
        budget rules in <a href="#/settings/budget">Budget</a>, phones in{" "}
        <a href="#/settings/devices">Devices</a>.
      </p>
    </SettingsFrame>
  );
}
