// The Code page's icons that the Database tool uses, drawn the same way as keel's (components/icons.tsx: the same paths,
// class and stroke), so Code › Database looks as in keel 0.15.1. A plugin carries its own copy: it imports only react and
// @keel/web-sdk.

const PATHS: Record<string, string> = {
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4.5V11h-6.5",
  database:
    "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  table: "M3.5 4.5h17v15h-17zM3.5 9.5h17M3.5 14.5h17M9.5 9.5v10",
  column: "M8 4h8v16H8zM8 9h8",
  key: "M14.5 9.5a4 4 0 1 1-1.2-2.8M14.5 9.5H21M18 9.5v3M20.5 9.5v2",
  plus: "M12 5v14M5 12h14",
  play: "M7 4.5 19 12 7 19.5z",
  console: "M4 5h16v14H4zM7.5 9.5 10 12l-2.5 2.5M12 15h4.5",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg
      className="ide-svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
    >
      <path
        d={PATHS[name] ?? ""}
        fill="none"
        stroke="currentColor"
        strokeWidth={size <= 16 ? 2 : 1.7}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
