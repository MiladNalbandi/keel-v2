// The open file's outline (keel 0.28, the redesign): its classes, functions and headings, read from the file's text with
// a few patterns per language. It is a reader's map, not a parser: it finds what is declared at the start of a line, so a
// symbol declared in an unusual way may be missing. Nothing here talks to the server.

export type SymKind = "class" | "fn" | "type" | "head";
export type Sym = { name: string; kind: SymKind; line: number; depth: number };

const MAX = 300;

type Rule = { re: RegExp; kind: SymKind };

const PY: Rule[] = [
  { re: /^(\s*)class\s+([A-Za-z_]\w*)/, kind: "class" },
  { re: /^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)/, kind: "fn" },
];
const JS: Rule[] = [
  { re: /^(\s*)(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)/, kind: "class" },
  { re: /^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)/, kind: "fn" },
  { re: /^(\s*)(?:export\s+)?(?:declare\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)/, kind: "type" },
  { re: /^(\s*)(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)/, kind: "fn" },
];
const KT: Rule[] = [
  { re: /^(\s*)(?:(?:public|private|protected|internal|open|abstract|sealed|data|enum|inner|annotation|value|final|static)\s+)*(?:class|object|interface|record)\s+([A-Za-z_]\w*)/, kind: "class" },
  { re: /^(\s*)(?:(?:public|private|protected|internal|open|override|suspend|inline|operator|infix|tailrec|abstract|final)\s+)*fun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)/, kind: "fn" },
];
const JAVA: Rule[] = [
  { re: /^(\s*)(?:(?:public|private|protected|abstract|final|static|sealed)\s+)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)/, kind: "class" },
  { re: /^(\s*)(?:(?:public|private|protected|abstract|final|static|synchronized|native)\s+)+[\w<>[\],.? ]+\s+([A-Za-z_]\w*)\s*\(/, kind: "fn" },
];
const GO: Rule[] = [
  { re: /^()type\s+([A-Za-z_]\w*)\s+(?:struct|interface)/, kind: "class" },
  { re: /^()func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)/, kind: "fn" },
];
const RS: Rule[] = [
  { re: /^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|impl(?:<[^>]*>)?)\s+([A-Za-z_]\w*)/, kind: "class" },
  { re: /^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)/, kind: "fn" },
];
const RB: Rule[] = [
  { re: /^(\s*)(?:class|module)\s+([A-Z]\w*(?:::\w+)*)/, kind: "class" },
  { re: /^(\s*)def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)/, kind: "fn" },
];
const SH: Rule[] = [
  { re: /^()(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?/, kind: "fn" },
];

const RULES: Record<string, Rule[]> = {
  py: PY, pyi: PY,
  js: JS, jsx: JS, mjs: JS, cjs: JS, ts: JS, tsx: JS, mts: JS, cts: JS,
  kt: KT, kts: KT, scala: KT,
  java: JAVA, cs: JAVA,
  go: GO, rs: RS, rb: RB,
  sh: SH, bash: SH, zsh: SH,
};

const extOf = (path: string) => (path.split("/").pop() ?? "").split(".").slice(1).pop()?.toLowerCase() ?? "";

/** The symbols of a file, top to bottom; [] for a file whose kind has no patterns. */
export function outlineOf(text: string, path: string): Sym[] {
  const ext = extOf(path);
  if (ext === "md" || ext === "markdown" || ext === "mdx") return headingsOf(text);
  const rules = RULES[ext];
  if (!rules || !text) return [];
  const out: Sym[] = [];
  const lines = text.split("\n");
  // the indents seen so far, outermost first: a deeper indent is one level in
  const indents: number[] = [];
  for (let i = 0; i < lines.length && out.length < MAX; i++) {
    const l = lines[i];
    if (!l || l.length > 400) continue;
    for (const r of rules) {
      const m = r.re.exec(l);
      if (!m) continue;
      const ind = (m[1] ?? "").replace(/\t/g, "    ").length;
      while (indents.length && indents[indents.length - 1] >= ind) indents.pop();
      out.push({ name: m[2], kind: r.kind, line: i + 1, depth: Math.min(indents.length, 3) });
      indents.push(ind);
      break;
    }
  }
  return out;
}

/** A Markdown file's headings (#, ##, ###), outside code fences. */
function headingsOf(text: string): Sym[] {
  const out: Sym[] = [];
  let fence = false;
  const lines = text.split("\n");
  for (let i = 0; i < lines.length && out.length < MAX; i++) {
    const l = lines[i];
    if (/^\s*(```|~~~)/.test(l)) fence = !fence;
    if (fence) continue;
    const m = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(l);
    if (m) out.push({ name: m[2], kind: "head", line: i + 1, depth: m[1].length - 1 });
  }
  return out;
}

/** The symbol the line is in: the last one that starts at or above it. */
export function symbolAt(syms: Sym[], line: number | null | undefined): Sym | null {
  if (!line) return null;
  let hit: Sym | null = null;
  for (const s of syms) {
    if (s.line > line) break;
    hit = s;
  }
  return hit;
}
