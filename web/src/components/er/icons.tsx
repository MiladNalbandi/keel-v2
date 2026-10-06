// The diagram's glyphs, drawn once as <symbol>s and placed with <use> (thousands of rows stay cheap).
// They paint with currentColor, so CSS colours them per kind (gold primary key, blue foreign key, ...).

export const IconDefs = () => (
  <defs>
    <symbol id="erd-i-table" viewBox="0 0 16 16">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M1.5 6.2h13M6.2 6.2v7.3" stroke="currentColor" strokeWidth="1.3" fill="none" />
    </symbol>
    <symbol id="erd-i-view" viewBox="0 0 16 16">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" strokeDasharray="2.2 1.6" />
      <circle cx="8" cy="8" r="2.3" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </symbol>
    <symbol id="erd-i-key" viewBox="0 0 16 16">
      <circle cx="5" cy="8" r="3.4" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M8.3 8H15M12.2 8v2.6M14.4 8v2" stroke="currentColor" strokeWidth="1.7" fill="none" strokeLinecap="round" />
    </symbol>
    <symbol id="erd-i-fkey" viewBox="0 0 16 16">
      <circle cx="5" cy="8" r="3.4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M8.3 8H15M12.2 8v2.6" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
      <path d="M3.6 8h2.8" stroke="currentColor" strokeWidth="1.2" />
    </symbol>
    <symbol id="erd-i-pkfk" viewBox="0 0 16 16">
      <circle cx="5" cy="8" r="3.4" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M8.3 8H15M12.2 8v2.6M14.4 8v2" stroke="currentColor" strokeWidth="1.7" fill="none" strokeLinecap="round" />
      <circle cx="13" cy="3.4" r="2.6" className="erd-dot-fk" />
    </symbol>
    <symbol id="erd-i-unique" viewBox="0 0 16 16">
      <path d="M8 2.6 13.4 8 8 13.4 2.6 8Z" fill="currentColor" opacity=".9" />
    </symbol>
    <symbol id="erd-i-index" viewBox="0 0 16 16">
      <path d="M8 3 13 8 8 13 3 8Z" fill="none" stroke="currentColor" strokeWidth="1.5" />
    </symbol>
    <symbol id="erd-i-col" viewBox="0 0 16 16">
      <rect x="3" y="4.5" width="10" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </symbol>
    <symbol id="erd-i-folder" viewBox="0 0 16 16">
      <path d="M1.8 4.2c0-.8.6-1.4 1.4-1.4h3l1.5 1.6h5.1c.8 0 1.4.6 1.4 1.4v6.3c0 .8-.6 1.4-1.4 1.4H3.2c-.8 0-1.4-.6-1.4-1.4Z" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </symbol>
    <symbol id="erd-i-api" viewBox="0 0 16 16">
      <path d="M5.5 3.5 2 8l3.5 4.5M10.5 3.5 14 8l-3.5 4.5M9.3 2.8 6.7 13.2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </symbol>
    <symbol id="erd-i-db" viewBox="0 0 16 16">
      <ellipse cx="8" cy="3.8" rx="5.5" ry="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 3.8v8.4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V3.8M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </symbol>
    <symbol id="erd-i-code" viewBox="0 0 16 16">
      <rect x="1.5" y="2.5" width="13" height="11" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.2 6.3 3.6 8l1.6 1.7M10.8 6.3 12.4 8l-1.6 1.7M8.8 5.6 7.2 10.4" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </symbol>
  </defs>
);

/** Small UI icons for the toolbar (inline, 16 px). */
export function Ic({ name }: { name: "search" | "fit" | "reset" | "export" | "panel" | "file" | "minus" | "plus" | "one" | "close" | "chev" }) {
  const p = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "search": return <svg {...p}><circle cx="7" cy="7" r="4.3" /><path d="m10.3 10.3 3.4 3.4" /></svg>;
    case "fit": return <svg {...p}><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" /></svg>;
    case "reset": return <svg {...p}><path d="M3 8a5 5 0 1 0 1.6-3.7" /><path d="M3 2.6v2.6h2.6" /></svg>;
    case "export": return <svg {...p}><path d="M8 2.5v7.5M4.8 7 8 10.2 11.2 7M3 13.5h10" /></svg>;
    case "panel": return <svg {...p}><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M10 2.5v11" /></svg>;
    case "file": return <svg {...p}><path d="M4 1.8h5l3 3v9.4H4Z" /><path d="M9 1.8v3h3M6 8.2h4M6 10.8h4" /></svg>;
    case "minus": return <svg {...p}><path d="M3.5 8h9" /></svg>;
    case "plus": return <svg {...p}><path d="M3.5 8h9M8 3.5v9" /></svg>;
    case "one": return <svg {...p}><path d="M6.4 4.4 8.4 3v10M6.2 13h4.4" /></svg>;
    case "close": return <svg {...p}><path d="m4 4 8 8M12 4l-8 8" /></svg>;
    case "chev": return <svg {...p}><path d="m4.5 6.2 3.5 3.5 3.5-3.5" /></svg>;
  }
}
