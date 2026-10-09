// One line icon per screen, for the folded menu (Shell's icon strip): 24×24, stroke only, so it takes the text colour
// and the accent of the active screen in both themes. keel's own screens are here; a part's page brings its icon
// when it registers (registerPage({ icon })).

import type { ReactElement } from "react";
import { pageOf } from "../sdk/registry";

const P = (d: string) => <path d={d} />;

const ICONS: Record<string, ReactElement> = {
  projects: <>{P("M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z")}</>,
  flow: (
    <>
      {P("M5 6h4M15 6h4M5 18h4M15 18h4")}
      <circle cx="12" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      {P("M12 8.5v7")}
    </>
  ),
  inbox: (
    <>
      {P("M3 13h5l1.5 3h5L16 13h5")}
      {P("M5.5 5h13L21 13v6H3v-6z")}
    </>
  ),
  live: <>{P("M3 12h4l3-7 4 14 3-7h4")}</>,
  jobs: <>{P("M4 8h16v12H4zM9 8V5h6v3M4 13h16")}</>,
  workflows: <>{P("M3 4h7v5H3zM14 15h7v5h-7zM6.5 9v3.5h11V15")}</>,
  agents: (
    <>
      {P("M5 9h14v10H5zM12 5v4M9.5 14h.01M14.5 14h.01M2.5 13v3M21.5 13v3")}
      <circle cx="12" cy="4" r="1.2" />
    </>
  ),
  skills: <>{P("M13 2.5L4.5 14H11l-1 7.5L19.5 10H13z")}</>,
  stacks: (
    <>{P("M12 3l9 4.5-9 4.5-9-4.5zM3 12l9 4.5 9-4.5M3 16.5L12 21l9-4.5")}</>
  ),
  tools: <>{P("M9 3v5M15 3v5M6 8h12v3.5a6 6 0 0 1-12 0zM12 17.5V21")}</>,
  quality: (
    <>
      {P("M4.5 17a8 8 0 1 1 15 0M12 17l3.5-5")}
      <circle cx="12" cy="17" r="1.3" />
    </>
  ),
  budget: <>{P("M3 7.5h18V20H3zM3 7.5l3-3.5h12l3 3.5M16 13.5h2.5")}</>,
  settings: (
    <>
      {P("M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1")}
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="12" r="2" />
      <circle cx="17" cy="17" r="2" />
    </>
  ),
  connections: (
    <>
      {P(
        "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
      )}
    </>
  ),
  // v0.16.0 Control › Plugins: a puzzle piece
  plugins: (
    <>
      {P(
        "M5 8h3.5a2 2 0 1 1 4 0H16v3.5a2 2 0 1 1 0 4V19H12.5a2 2 0 1 0-4 0H5v-3.5a2 2 0 1 0 0-4z",
      )}
    </>
  ),
  // an add-on's page (v0.13.0): a compass
  addon: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      {P("M15.5 8.5l-2 5-5 2 2-5z")}
    </>
  ),
};

export function NavIcon({ id, size = 20 }: { id: string; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {ICONS[id] ?? pageOf(id)?.icon ?? ICONS.addon}
    </svg>
  );
}
