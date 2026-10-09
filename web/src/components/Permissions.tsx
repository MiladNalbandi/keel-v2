// v0.16.0 what a plugin may do, in plain words: its trust level (content only, adds pages, runs code in keel) and its
// permissions, each with its level (high, medium, low). The Plugins page and the Inbox's install requests show them.

import { permissionLines, trustOf, type Permissions } from "../marketplaceApi";

export function TrustTag({ trust }: { trust: string | null | undefined }) {
  const t = trustOf(trust);
  if (!t) return null;
  return (
    <span className={`trust ${t.tone}`} title={t.about}>
      <i aria-hidden="true" />
      {t.label}
    </span>
  );
}

const LEVEL: Record<string, string> = {
  high: "high",
  medium: "medium",
  low: "low",
};

/** The permissions, the highest level first; `max` shows only the first few and says how many more. */
export function PermissionList({
  permissions,
  max,
  compact,
}: {
  permissions: Permissions | null | undefined;
  max?: number;
  compact?: boolean;
}) {
  const lines = permissionLines(permissions);
  if (!lines.length)
    return <p className="sub perm-none">It asks for no permissions.</p>;
  const shown = max ? lines.slice(0, max) : lines;
  return (
    <ul
      className={`perms${compact ? " compact" : ""}`}
      aria-label="Permissions"
    >
      {shown.map((l, i) => (
        <li key={i} className="perm">
          <span className={`lvl ${l.level}`}>{LEVEL[l.level]}</span>
          <span>
            <b>{l.text}</b>
            {!compact && l.detail && <span className="why">{l.detail}</span>}
          </span>
        </li>
      ))}
      {max && lines.length > max && (
        <li className="sub">and {lines.length - max} more</li>
      )}
    </ul>
  );
}
