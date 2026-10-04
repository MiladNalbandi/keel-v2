// Syntax highlighting: highlight.js core with only the languages agents usually touch (keeps the bundle small).
// hljs escapes the source itself, so its HTML output is safe to insert.

import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import diff from "highlight.js/lib/languages/diff";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import yaml from "highlight.js/lib/languages/yaml";

hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("python", python);
hljs.registerLanguage("kotlin", kotlin);
hljs.registerLanguage("java", java);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("diff", diff);

const EXT: Record<string, string> = {
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", tsx: "typescript", mts: "typescript", cts: "typescript",
  json: "json", jsonc: "json",
  py: "python",
  kt: "kotlin", kts: "kotlin",
  java: "java",
  sh: "bash", bash: "bash", zsh: "bash", env: "bash",
  yml: "yaml", yaml: "yaml",
  md: "markdown", markdown: "markdown",
  sql: "sql",
  diff: "diff", patch: "diff",
};
const NAMES: Record<string, string> = { Dockerfile: "bash", Makefile: "bash", ".env": "bash", "gradlew": "bash" };
const ALIAS: Record<string, string> = { ...EXT, shell: "bash", console: "bash", javascript: "javascript", typescript: "typescript", python: "python", kotlin: "kotlin" };

/** The highlight language for a file path, from its extension; null when we do not know it. */
export function languageOf(path: string | undefined | null): string | null {
  if (!path) return null;
  const name = path.split("/").pop() ?? path;
  if (NAMES[name]) return NAMES[name];
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  return EXT[ext] ?? null;
}

/** A fence tag (```ts, ```kotlin …) as a registered language, or null. */
export function normLang(tag: string | undefined | null): string | null {
  if (!tag) return null;
  const t = tag.toLowerCase();
  const l = ALIAS[t] ?? t;
  return hljs.getLanguage(l) ? l : null;
}

/** Highlighted HTML for `code`, or null (unknown language, too big, or hljs failed). */
export function highlight(code: string, lang: string | null): string | null {
  if (!lang || code.length > 200_000) return null;
  try {
    return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
  } catch {
    return null;
  }
}
