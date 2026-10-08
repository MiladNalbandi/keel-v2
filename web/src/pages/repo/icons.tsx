// A small built-in icon set for the Repo IDE: file types by extension (a coloured monogram), folders, and the
// activity bar. No icon font or package — the whole set is this file.

import type { CSSProperties, ReactNode } from "react";

type FileType = [label: string, color: string, lang?: string];

const T = {
  kotlin: ["K", "#9b6bff", "Kotlin"], java: ["J", "#e2711d", "Java"], ts: ["TS", "#3b82d6", "TypeScript"],
  tsx: ["TS", "#1fa9c9", "TypeScript JSX"], js: ["JS", "#c9a400", "JavaScript"], jsx: ["JS", "#1fa9c9", "JavaScript JSX"],
  json: ["{}", "#c48a00", "JSON"], md: ["M", "#4f8ad6", "Markdown"], py: ["PY", "#3b78b5", "Python"], sql: ["SQ", "#d0743c", "SQL"],
  yaml: ["Y", "#cc4b5d", "YAML"], xml: ["<>", "#e0603a", "XML"], html: ["<>", "#e0603a", "HTML"], css: ["#", "#3f73e0", "CSS"],
  scss: ["S", "#c6538c", "SCSS"], sh: ["$", "#3fa04a", "Shell"], gradle: ["G", "#3fa37a", "Gradle"], docker: ["D", "#2496ed", "Dockerfile"],
  git: ["G", "#e5533d", "Git"], conf: ["⚙", "#8a8a8a", "Config"], text: ["≡", "#8a8a8a", "Plain text"], lock: ["L", "#8a8a8a", "Lockfile"],
  go: ["GO", "#00a7cf", "Go"], rust: ["RS", "#c8794a", "Rust"], ruby: ["RB", "#cc342d", "Ruby"], php: ["PH", "#7a7fc4", "PHP"],
  c: ["C", "#5c6bc0", "C"], cpp: ["C+", "#5c6bc0", "C++"], cs: ["C#", "#9b4f96", "C#"], swift: ["SW", "#f05138", "Swift"],
  vue: ["V", "#3fae7d", "Vue"], svelte: ["S", "#f0531d", "Svelte"], csv: [",", "#3fa04a", "CSV"], pdf: ["PDF", "#d64545", "PDF"],
  license: ["©", "#c9a400", "License"], env: ["E", "#c9a400", "Environment"], toml: ["T", "#9c4221", "TOML"], proto: ["P", "#4f8ad6", "Protobuf"],
} satisfies Record<string, FileType>;

const BY_EXT: Record<string, FileType> = {
  kt: T.kotlin, kts: T.kotlin, java: T.java, ts: T.ts, mts: T.ts, cts: T.ts, tsx: T.tsx, js: T.js, mjs: T.js, cjs: T.js, jsx: T.jsx,
  json: T.json, jsonc: T.json, md: T.md, markdown: T.md, mdx: T.md, py: T.py, sql: T.sql, yml: T.yaml, yaml: T.yaml, xml: T.xml,
  html: T.html, htm: T.html, css: T.css, scss: T.scss, sass: T.scss, less: T.css, sh: T.sh, bash: T.sh, zsh: T.sh, gradle: T.gradle,
  go: T.go, rs: T.rust, rb: T.ruby, php: T.php, c: T.c, h: T.c, cc: T.cpp, cpp: T.cpp, hpp: T.cpp, cs: T.cs, swift: T.swift,
  vue: T.vue, svelte: T.svelte, csv: T.csv, tsv: T.csv, pdf: T.pdf, properties: T.conf, conf: T.conf, ini: T.conf, cfg: T.conf,
  toml: T.toml, txt: T.text, log: T.text, lock: T.lock, proto: T.proto, editorconfig: T.conf,
};
const BY_NAME: Record<string, FileType> = {
  dockerfile: T.docker, "docker-compose.yml": T.docker, "compose.yml": T.docker, makefile: T.sh, gradlew: T.sh, "gradlew.bat": T.sh,
  ".gitignore": T.git, ".gitattributes": T.git, ".gitmodules": T.git, license: T.license, "license.md": T.license, "license.txt": T.license,
  ".env.example": T.env, ".env.sample": T.env, "package-lock.json": T.lock, "yarn.lock": T.lock, "uv.lock": T.lock,
};

export const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg", "avif"]);

export const extOf = (name: string) => (name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "");
export const isImage = (path: string) => IMAGE_EXT.has(extOf(path));

function typeOf(name: string): FileType | null {
  const n = name.toLowerCase();
  if (BY_NAME[n]) return BY_NAME[n];
  if (n.startsWith("dockerfile")) return T.docker;
  if (n.endsWith(".gradle.kts")) return T.gradle;
  return BY_EXT[extOf(n)] ?? null;
}

/** The language name the status bar shows. */
export function languageName(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  if (isImage(name)) return "Image";
  return typeOf(name)?.[2] ?? "Plain text";
}

const Svg = ({ children, size = 16, style }: { children: ReactNode; size?: number; style?: CSSProperties }) => (
  <svg className="ide-svg" viewBox="0 0 16 16" width={size} height={size} aria-hidden="true" style={style}>{children}</svg>
);

export function FileIcon({ name }: { name: string }) {
  if (isImage(name)) {
    return (
      <Svg style={{ color: "#a074c4" }}>
        <rect x="2" y="3" width="12" height="10" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M3.5 11.5l3-3.2 2.2 2.2 1.6-1.5 2.2 2.5z" fill="currentColor" /><circle cx="10.6" cy="6" r="1.1" fill="currentColor" />
      </Svg>
    );
  }
  const t = typeOf(name);
  if (!t) {
    return (
      <Svg style={{ color: "var(--faint)" }}>
        <path d="M4 1.8h5.2L12.5 5v9.2H4z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
        <path d="M9 2v3.3h3.3" fill="none" stroke="currentColor" strokeWidth="1.2" />
      </Svg>
    );
  }
  return <span className={`fi fi-${t[0].length}`} style={{ "--fi": t[1] } as CSSProperties} aria-hidden="true">{t[0]}</span>;
}

export function FolderIcon({ open, keel }: { open: boolean; keel?: boolean }) {
  return (
    <Svg style={{ color: keel ? "var(--accent)" : "var(--dim)" }}>
      {open
        ? <path d="M1.8 4.2c0-.6.4-1 1-1h3.3l1.4 1.4h5c.6 0 1 .4 1 1v.9H4.6L2.6 12.6H2.8l-1-.1z M4.4 6.9h10.2l-2 5.8H2.4z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
        : <path d="M1.8 4.2c0-.6.4-1 1-1h3.3l1.4 1.4h5.7c.6 0 1 .4 1 1v6.6c0 .6-.4 1-1 1H2.8c-.6 0-1-.4-1-1z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />}
    </Svg>
  );
}

export const Chevron = ({ open }: { open: boolean }) => (
  <Svg size={12} style={{ transform: open ? "rotate(90deg)" : undefined, transition: "transform .12s" }}>
    <path d="M6 3.5l4.5 4.5L6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </Svg>
);

/** Line icons for the activity bar and toolbars (24 × 24, stroke = currentColor). */
const PATHS: Record<string, string> = {
  files: "M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h10a1.5 1.5 0 0 0 1.5-1.5V8zM14 3v5h5M3 7v13.5A1.5 1.5 0 0 0 4.5 22",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.3 15.3 20.5 20.5",
  branch: "M6 3v12M6 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 8.5c0 5-6.5 4-11.2 8.4",
  keel: "M3 5h18c0 4.5-5 8.2-8.1 9.6L12 20l-.9-5.4C8 13.2 3 9.5 3 5z",
  collapse: "M9 4h10.5A1.5 1.5 0 0 1 21 5.5V16M4.5 8h10A1.5 1.5 0 0 1 16 9.5v10a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 3 19.5v-10A1.5 1.5 0 0 1 4.5 8zM7 14.5h5",
  reveal: "M12 5c-5 0-9 7-9 7s4 7 9 7 9-7 9-7-4-7-9-7zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4.5V11h-6.5",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  table: "M3.5 4.5h17v15h-17zM3.5 9.5h17M3.5 14.5h17M9.5 9.5v10",
  column: "M8 4h8v16H8zM8 9h8",
  key: "M14.5 9.5a4 4 0 1 1-1.2-2.8M14.5 9.5H21M18 9.5v3M20.5 9.5v2",
  plus: "M12 5v14M5 12h14",
  play: "M7 4.5 19 12 7 19.5z",
  console: "M4 5h16v14H4zM7.5 9.5 10 12l-2.5 2.5M12 15h4.5",
  close: "M6 6l12 12M18 6 6 18",
  wrap: "M4 6h16M4 12h13a3 3 0 0 1 0 6h-4M15 15.5 12.5 18l2.5 2.5M4 18h5",
  diff: "M7 3v10M2 8h10M12 21H22M17 3l-5 18",
  split: "M4 4h16v16H4zM12 4v16",
  inline: "M4 4h16v16H4zM4 12h16",
  preview: "M12 5c-5 0-9 7-9 7s4 7 9 7 9-7 9-7-4-7-9-7zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  up: "M12 19V5M6 11l6-6 6 6",
  down: "M12 5v14M6 13l6 6 6-6",
  lock: "M7 11V8a5 5 0 0 1 10 0v3M5.5 11h13v9.5h-13z",
  review: "M4 5h16v10.5H11L6.5 19.5V15.5H4zM8.5 10.2l2.2 2.2 4.8-4.8",
  back: "M15 5l-7 7 7 7",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  link: "M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4L12 5.6M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2",
  history: "M3.5 12a8.5 8.5 0 1 0 2.5-6M3 3v4.5h4.5M12 7.5V12l3 2",
  commit: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3 12h5.5M15.5 12H21",
  helper: "M4.5 5h15A1.5 1.5 0 0 1 21 6.5v9a1.5 1.5 0 0 1-1.5 1.5H12l-4.5 3.5V17h-3A1.5 1.5 0 0 1 3 15.5v-9A1.5 1.5 0 0 1 4.5 5zM8 10h.01M12 10h.01M16 10h.01",
};

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  if (name === "keel") {
    return (
      <svg className="ide-svg" viewBox="0 0 120 68" width={size + 4} height={size} aria-hidden="true">
        <path fill="currentColor" d="M4 0H116Q120 0 119.4 4C118 22 92 38 66.6 50Q64 51.5 64 54L62.2 64.5Q61.8 67 60 67Q58.2 67 57.8 64.5L56 54Q56 51.5 53.4 50C28 38 2 22 .6 4Q0 0 4 0Z" />
      </svg>
    );
  }
  return (
    <svg className="ide-svg" viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
      <path d={PATHS[name] ?? ""} fill="none" stroke="currentColor" strokeWidth={size <= 16 ? 2 : 1.7} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
