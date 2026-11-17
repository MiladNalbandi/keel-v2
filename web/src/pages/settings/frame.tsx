// keel 0.18.57 the frame every Settings tab draws itself in: "Settings ›", the tab's name and one line, its buttons on
// the right; under it the settings menu (a column on the left on a wide screen, a row of chips on a phone) and the tab.
// The menu is keel's own tabs plus the parts' (slot settings.tab: the Database plugin's Databases), by their order.

import { createContext, useContext, useState, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNarrow } from "../../components/page";
import { usePageKeys } from "../../components/rdBuild";
import { CORE_SETTINGS_TABS, settingsHash } from "../../routes";
import { useSlot } from "../../sdk/registry";
import { SLOTS, type SettingsTabItem } from "../../sdk/slots";
import { useApp } from "../../state";

/** keel's own tabs (routes.ts): id, name in the menu, order. */
export const CORE_TABS = CORE_SETTINGS_TABS;

/** Every tab of the menu, in its order: keel's own and the parts'. */
export function useSettingsTabs(): {
  id: string;
  label: string;
  part?: SettingsTabItem;
}[] {
  const parts = useSlot<SettingsTabItem>(SLOTS.settingsTab);
  return [
    ...CORE_TABS.map((t) => ({ ...t })),
    ...parts
      .filter((p) => !CORE_TABS.some((t) => t.id === p.id))
      .map((p) => ({
        id: p.id,
        label: p.title,
        order: p.order ?? 60,
        part: p,
      })),
  ].sort((a, b) => a.order - b.order);
}

// A tab's small hint in the menu ("1" on Models when a provider needs a login, "restart" on Plugins), from what that
// tab last read: the menu asks for nothing itself, so a Settings page costs no extra request.
const hints = new Map<string, string>();
const hintListeners = new Set<() => void>();
let hintVersion = 0;
export function setSettingsHint(tab: string, text: string | null) {
  if ((hints.get(tab) ?? null) === text) return;
  if (text) hints.set(tab, text);
  else hints.delete(tab);
  hintVersion++;
  hintListeners.forEach((l) => l());
}
function useHints() {
  useSyncExternalStore(
    (l) => {
      hintListeners.add(l);
      return () => void hintListeners.delete(l);
    },
    () => hintVersion,
    () => hintVersion,
  );
  return hints;
}

function SettingsMenu({ tab, phone }: { tab: string; phone: boolean }) {
  const tabs = useSettingsTabs();
  const shown = useHints();
  const { health, project } = useApp();
  const links = tabs.map((t) => {
    const on = t.id === tab;
    const hint = !on ? shown.get(t.id) : undefined;
    return (
      <a
        key={t.id}
        href={settingsHash(t.id)}
        aria-current={on ? "page" : undefined}
      >
        <span>{t.label}</span>
        {hint && (
          <span className="ss-hint" aria-label={`(${hint})`}>
            {hint}
          </span>
        )}
      </a>
    );
  });
  if (phone)
    return (
      <nav className="ss-chips" aria-label="Settings sections">
        {links}
      </nav>
    );
  return (
    <nav className="ss-menu" aria-label="Settings sections">
      <span className="ss-menu-h" aria-hidden="true">
        Settings
      </span>
      {links}
      {(health?.version || project) && (
        <span className="ss-menu-foot">
          {[
            health?.version ? `keel ${health.version.replace(/^v/, "")}` : null,
            project ? `project ${project.name}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      )}
    </nav>
  );
}

// 0.35 the Settings page keeps one frame (its head and menu) while the tab changes: only the tab's panel is drawn again,
// not the whole page. SettingsShell owns the frame; a tab's SettingsFrame inside it puts its head into the shell's head
// (a portal) and its content into the shell's content column.
const ShellHead = createContext<HTMLDivElement | null | undefined>(undefined);

/** The Settings page's frame that stays while the tab changes: the head's place, the menu and the content column. */
export function SettingsShell({ tab, children }: { tab: string; children: ReactNode }) {
  const phone = useNarrow(900);
  // the head's place exists from the first render (the tab draws into it at once: no second render, no flash)
  const [head] = useState<HTMLDivElement | null>(() => {
    if (typeof document === "undefined") return null;
    const el = document.createElement("div");
    el.style.display = "contents";
    return el;
  });
  const place = (n: HTMLDivElement | null) => {
    if (n && head && head.parentNode !== n) n.appendChild(head);
  };
  return (
    <ShellHead.Provider value={head}>
      <div className="ss-page rdb rdb-settings" data-testid="settings-page">
        <div className="ss-head" ref={place} />
        {phone && <SettingsMenu tab={tab} phone />}
        <div className="ss-body">
          {!phone && <SettingsMenu tab={tab} phone={false} />}
          <div className="ss-content">{children}</div>
        </div>
      </div>
    </ShellHead.Provider>
  );
}

function HeadParts({ title, line, actions }: { title: string; line: ReactNode; actions?: ReactNode }) {
  return (
    <>
      <div className="ss-head-t">
        <span className="ss-crumb">Settings ›</span>
        <h1>{title}</h1>
        <p>{line}</p>
      </div>
      {actions && <div className="ss-head-a">{actions}</div>}
    </>
  );
}

/** One tab: the head (Settings › name, one line, its buttons), the menu, and the tab's content. Overlays (drawers) go in
 *  `after`, outside the content column. Inside SettingsShell it draws only its head and content. */
export function SettingsFrame({
  tab,
  title,
  line,
  actions,
  children,
  after,
}: {
  tab: string;
  title: string;
  line: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  after?: ReactNode;
}) {
  // one menu at a time: the chips on a phone (and a narrow window), the column beside the tab on a wide screen
  const phone = useNarrow(900);
  const shell = useContext(ShellHead);
  usePageKeys();
  if (shell !== undefined)
    return (
      <>
        {shell && createPortal(<HeadParts title={title} line={line} actions={actions} />, shell)}
        <div className="ss-tab" data-testid={`settings-${tab}`}>{children}</div>
        {after}
      </>
    );
  return (
    <div className="ss-page rdb rdb-settings" data-testid={`settings-${tab}`}>
      <div className="ss-head">
        <HeadParts title={title} line={line} actions={actions} />
      </div>
      {phone && <SettingsMenu tab={tab} phone />}
      <div className="ss-body">
        {!phone && <SettingsMenu tab={tab} phone={false} />}
        <div className="ss-content">{children}</div>
      </div>
      {after}
    </div>
  );
}
