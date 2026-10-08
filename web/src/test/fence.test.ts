/**
 * The fence (docs/plugins/07-step1-contract.md §9): keel's core web code must not import a part that becomes a
 * plugin, and never anything in product/. Today's couplings sit in fence-allowlist.txt next to this file. That list
 * may only shrink: a new import fails, and so does a line whose import is gone. Plugin parts may import core, and
 * each other. Test files (src/test/**, *.test.ts(x)) are not checked.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

// a path, not new URL(): the jsdom test environment replaces the global URL
const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "../..");
const ALLOWLIST = path.join(HERE, "fence-allowlist.txt");
const ALLOWLIST_NAME = "web/src/test/fence-allowlist.txt";
const GUIDE = "docs/plugins/02-plugin-package.md";

/** The parts that become plugins (docs/plugins/01-today.md), relative to web/. A name ending in "/" is a folder. */
const PLUGIN_PARTS = [
  "src/components/JiraCard.tsx",
  "src/components/graph/",
  "src/components/helper/",
  "src/components/plugins/",
  "src/components/review/",
  "src/pages/Graph.tsx",
  "src/pages/Helper.tsx",
  "src/pages/Map.tsx",
  "src/pages/Repo.tsx",
  "src/pages/Tasks.tsx",
  "src/pages/Wiki.tsx",
  "src/pages/repo/",
  "src/reviewApi.ts",
  "src/tasksApi.ts",
];
/** Core never imports these (keel Product and the parts that moved to plugins/ are plugins). They can never be in the
 *  allowlist. */
const FORBIDDEN = ["../product/", "../plugins/"];

type Found = { couplings: Set<string>; forbidden: Set<string> };
type Spec = { spec: string; glob: boolean };

const posix = path.posix;

const under = (file: string, parts: string[]) =>
  parts.some((p) =>
    p.endsWith("/")
      ? file.startsWith(p) || file === p.slice(0, -1)
      : file === p,
  );

const isTest = (file: string) =>
  file.startsWith("src/test/") || /\.(test|spec)\.tsx?$/.test(file);
const isSource = (file: string) => /\.tsx?$/.test(file) && !isTest(file);

function isFile(file: string): boolean {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

/** Every file under root/dir, as paths relative to root (posix). */
function walk(root: string, dir: string): string[] {
  if (!fs.existsSync(path.join(root, dir))) return [];
  const out: string[] = [];
  for (const entry of fs.readdirSync(path.join(root, dir), {
    withFileTypes: true,
  })) {
    const rel = posix.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "node_modules") out.push(...walk(root, rel));
    } else out.push(rel);
  }
  return out.sort();
}

function isMetaGlob(e: ts.Expression): boolean {
  return (
    ts.isPropertyAccessExpression(e) &&
    /^glob(Eager)?$/.test(e.name.text) &&
    ts.isMetaProperty(e.expression) &&
    e.expression.keywordToken === ts.SyntaxKind.ImportKeyword &&
    e.expression.name.text === "meta"
  );
}

/** What a file imports: import/export ... from, import "x", import("x"), import("x").T in types, import.meta.glob. */
function specifiersOf(file: string, text: string): Spec[] {
  const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const source = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    false,
    kind,
  );
  const out: Spec[] = [];
  const add = (node: ts.Node | undefined, glob = false) => {
    if (node && ts.isStringLiteralLike(node))
      out.push({ spec: node.text, glob });
  };
  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
      add(node.moduleSpecifier);
    else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference)
    ) {
      add(node.moduleReference.expression);
    } else if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument))
      add(node.argument.literal);
    else if (ts.isCallExpression(node)) {
      const [first] = node.arguments;
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) add(first);
      else if (isMetaGlob(node.expression)) {
        if (first && ts.isArrayLiteralExpression(first))
          first.elements.forEach((e) => add(e, true));
        else add(first, true);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

/** A relative ("./", "../") or root ("/" is web/ in Vite) specifier as a path relative to web/; null for a package. */
function toPath(from: string, spec: string): string | null {
  if (spec.startsWith("./") || spec.startsWith("../"))
    return posix.normalize(posix.join(posix.dirname(from), spec));
  if (spec.startsWith("/")) return posix.normalize(spec.slice(1));
  return null;
}

/** The file a specifier loads, relative to web/ ("./x" from src/a.ts -> src/x.tsx); null for a package like react. */
function resolveSpec(root: string, from: string, spec: string): string | null {
  const base = toPath(from, spec.split("?")[0]);
  if (base === null) return null;
  const found = ["", ".ts", ".tsx", "/index.ts", "/index.tsx"]
    .map((ext) => base + ext)
    .find((f) => isFile(path.join(root, f)));
  return found ?? base;
}

function globToRegex(glob: string): RegExp {
  let re = "";
  let braces = 0;
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      const slash = glob[i + 2] === "/";
      re += slash ? "(?:.*/)?" : ".*";
      i += slash ? 2 : 1;
    } else if (c === "*") {
      re += "[^/]*";
    } else if (c === "?") {
      re += "[^/]";
    } else if (c === "{") {
      braces++;
      re += "(?:";
    } else if (c === "}" && braces > 0) {
      braces--;
      re += ")";
    } else if (c === "," && braces > 0) {
      re += "|";
    } else {
      re += c.replace(/[.+^$()|[\]\\{}]/g, "\\$&");
    }
  }
  return new RegExp(`^${re}$`);
}

/** The files an import.meta.glob pattern can load, relative to web/ (its folder when nothing matches). */
function globTargets(root: string, from: string, pattern: string): string[] {
  const full = pattern.startsWith("!") ? null : toPath(from, pattern);
  if (full === null) return [];
  if (!/[*?{]/.test(full)) return [full];
  const segments = full.split("/");
  const dir = segments
    .slice(
      0,
      segments.findIndex((s) => /[*?{]/.test(s)),
    )
    .join("/");
  const re = globToRegex(full);
  const matched = walk(root, dir).filter((f) => re.test(f));
  return matched.length > 0 ? matched : [`${dir}/`];
}

/** Couplings and forbidden imports of the core files under root/src, as "importer -> imported" lines. */
function scan(
  root: string,
  plugins = PLUGIN_PARTS,
  forbidden = FORBIDDEN,
): Found {
  const couplings = new Set<string>();
  const bad = new Set<string>();
  for (const file of walk(root, "src").filter(isSource)) {
    if (under(file, plugins)) continue; // a plugin part may import core and other plugins
    const text = fs.readFileSync(path.join(root, file), "utf8");
    for (const { spec, glob } of specifiersOf(file, text)) {
      const targets = glob
        ? globTargets(root, file, spec)
        : [resolveSpec(root, file, spec)];
      for (const target of targets) {
        if (target === null) continue;
        if (under(target, forbidden)) bad.add(`${file} -> ${target}`);
        else if (under(target, plugins)) couplings.add(`${file} -> ${target}`);
      }
    }
  }
  return { couplings, forbidden: bad };
}

/** The lines of an allowlist, without comments ('#') and blank lines, with one space around "->". */
function readAllowlist(file: string): string[] {
  return fs
    .readFileSync(file, "utf8")
    .split("\n")
    .map((line) => line.split("#")[0].trim())
    .filter((line) => line.length > 0)
    .map((line) =>
      line
        .split("->")
        .map((part) => part.trim())
        .join(" -> "),
    );
}

/** What is wrong, in plain words: new couplings, and allowlist lines whose import is gone. */
function problems(
  found: Set<string>,
  allowed: string[],
  allowlistName: string,
): string[] {
  const listed = new Set(allowed);
  const fresh = [...found].filter((line) => !listed.has(line)).sort();
  const gone = allowed.filter((line) => !found.has(line)).sort();
  const out: string[] = [];
  if (fresh.length > 0) {
    out.push(
      `Core must not import a plugin. Use an extension point instead, see ${GUIDE}.\n` +
        `New imports from core into a plugin:\n${fresh.map((l) => `  ${l}`).join("\n")}`,
    );
  }
  if (gone.length > 0) {
    out.push(
      `Remove these lines from ${allowlistName}: the coupling is gone (the list only shrinks).\n` +
        gone.map((l) => `  ${l}`).join("\n"),
    );
  }
  return out;
}

describe("the fence: keel's core web code does not import plugins", () => {
  const found = scan(WEB);

  it("core never imports anything in product/", () => {
    const bad = [...found.forbidden].sort();
    const message =
      `Core must never import product/ (keel Product is a plugin). Use an extension point, see ${GUIDE}.\n` +
      bad.map((l) => `  ${l}`).join("\n");
    expect(bad, message).toEqual([]);
  });

  it("core imports no plugin part beyond the allowlist", () => {
    const issues = problems(
      found.couplings,
      readAllowlist(ALLOWLIST),
      ALLOWLIST_NAME,
    );
    expect(issues, issues.join("\n\n")).toEqual([]);
  });

  it("the allowlist is sorted and names nothing in product/", () => {
    const allowed = readAllowlist(ALLOWLIST);
    expect(
      allowed,
      `Keep ${ALLOWLIST_NAME} sorted, one line per coupling.`,
    ).toEqual([...new Set(allowed)].sort());
    expect(
      allowed.filter((l) => under(l.split(" -> ")[1] ?? "", FORBIDDEN)),
      "product/ can never be in the allowlist",
    ).toEqual([]);
  });
});

describe("the fence scanner, on a tiny tree", () => {
  function tree(files: Record<string, string>): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "keel-fence-"));
    for (const [name, text] of Object.entries(files)) {
      fs.mkdirSync(path.dirname(path.join(dir, name)), { recursive: true });
      fs.writeFileSync(path.join(dir, name), text);
    }
    return dir;
  }

  it("finds every kind of import in core files, and skips tests, plugin parts and comments", () => {
    const dir = tree({
      "web/src/App.tsx": [
        'import { lazy } from "react";',
        'import { Helper } from "./pages/Helper";',
        'import Inbox from "./pages/Inbox";',
        'import type { Task } from "./tasksApi";',
        'import "./components/helper/helper.css";',
        'import { x } from "./components/helpers/x";', // not the helper folder
        'import Repository from "./pages/Repository";', // not pages/Repo.tsx
        'export { Diff } from "./components/review/Diff";',
        '// import { Graph } from "./pages/Graph";',
        'const s = "./pages/Wiki";',
        'const Map = lazy(() => import("./pages/Map"));',
        'let r: typeof import("./reviewApi").api;',
        'const graphs = import.meta.glob("./components/graph/*.tsx");',
        'const product = import.meta.glob<{ default: unknown }>(["../../product/web/index.tsx"]);',
        "export default function App() { return <div>{s}</div>; }",
      ].join("\n"),
      "web/src/sdk/index.ts":
        'export * from "../reviewApi";\nexport { api } from "../api";\n',
      "web/src/api.ts": "export const api = {};\n",
      "web/src/reviewApi.ts": "export const api = {};\n",
      "web/src/tasksApi.ts": "export type Task = {};\n",
      "web/src/pages/Helper.tsx":
        'import "../../../product/web/index";\nexport const Helper = 1;\n',
      "web/src/pages/Inbox.tsx":
        'import { Task } from "../tasksApi";\nexport default 1;\n',
      "web/src/pages/Map.tsx": "export default 1;\n",
      "web/src/pages/Repository.tsx": "export default 1;\n",
      "web/src/components/helpers/x.ts": "export const x = 1;\n",
      "web/src/components/helper/helper.css": "",
      "web/src/components/review/Diff.tsx": "export const Diff = 1;\n",
      "web/src/components/graph/Graph.tsx": "export default 1;\n",
      "web/src/components/graph/layout.ts": "export default 1;\n",
      "web/src/test/app.test.tsx":
        'import { Helper } from "../pages/Helper";\n',
      "web/src/Board.test.tsx": 'import "./pages/Map";\n',
      "product/web/index.tsx": "export default {};\n",
    });
    try {
      const got = scan(path.join(dir, "web"));
      expect([...got.couplings].sort()).toEqual([
        "src/App.tsx -> src/components/graph/Graph.tsx",
        "src/App.tsx -> src/components/helper/helper.css",
        "src/App.tsx -> src/components/review/Diff.tsx",
        "src/App.tsx -> src/pages/Helper.tsx",
        "src/App.tsx -> src/pages/Map.tsx",
        "src/App.tsx -> src/reviewApi.ts",
        "src/App.tsx -> src/tasksApi.ts",
        "src/pages/Inbox.tsx -> src/tasksApi.ts",
        "src/sdk/index.ts -> src/reviewApi.ts",
      ]);
      expect([...got.forbidden]).toEqual([
        "src/App.tsx -> ../product/web/index.tsx",
      ]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("says which lines are new and which are gone", () => {
    const msgs = problems(
      new Set(["a -> src/pages/Map.tsx", "b -> src/tasksApi.ts"]),
      ["b -> src/tasksApi.ts", "c -> src/reviewApi.ts"],
      "list.txt",
    );
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toContain("Core must not import a plugin");
    expect(msgs[0]).toContain(GUIDE);
    expect(msgs[0]).toContain("  a -> src/pages/Map.tsx");
    expect(msgs[1]).toContain(
      "Remove these lines from list.txt: the coupling is gone",
    );
    expect(msgs[1]).toContain("  c -> src/reviewApi.ts");
    expect(problems(new Set(["b -> x"]), ["b -> x"], "list.txt")).toEqual([]);
  });

  it("reads the allowlist without comments and with even spacing", () => {
    const dir = tree({ "list.txt": "# header\n\n  a  ->  b   # why\nc->d\n" });
    try {
      expect(readAllowlist(path.join(dir, "list.txt"))).toEqual([
        "a -> b",
        "c -> d",
      ]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
