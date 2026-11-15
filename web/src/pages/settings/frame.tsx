// keel 0.18.57 the frame every Settings tab draws itself in: "Settings ›", the tab's name and one line, its buttons on
// the right; under it the settings menu (a column on the left on a wide screen, a row of chips on a phone) and the tab.
// The menu is keel's own tabs plus the parts' (slot settings.tab: the Database plugin's Databases), by their order.

import { useSyncExternalStore, type ReactNode } from "react";
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

/** One tab: the head (Settings › name, one line, its buttons), the menu, and the tab's content. Overlays (drawers) go in
 *  `after`, outside the content column. */
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
  usePageKeys();
  return (
    <div className="ss-page rdb rdb-settings" data-testid={`settings-${tab}`}>
      <div className="ss-head">
        <div className="ss-head-t">
          <span className="ss-crumb">Settings ›</span>
          <h1>{title}</h1>
          <p>{line}</p>
        </div>
        {actions && <div className="ss-head-a">{actions}</div>}
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
