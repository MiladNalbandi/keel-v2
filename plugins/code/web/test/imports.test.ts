// The Code plugin's web part imports only react, react-dom, @keel/web-sdk and its own files (keel's page shares the
// first three at run time), and every name it takes from @keel/web-sdk is in it. Like the Code Review plugin's own
// check. What other parts share with it (the fuzzy file match, icons, the diff parser) is keel's, never this plugin's.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as sdk from "@keel/web-sdk";

const HERE = join(__dirname, "..");
const SHARED = ["react", "react-dom", "@keel/web-sdk"];

/** plugins/code/web's own source files (not its tests): what its bundle is built from. */
const sources = () =>
  readdirSync(HERE)
    .filter((f) => /\.tsx?$/.test(f))
    .map((f) => ({ file: f, text: readFileSync(join(HERE, f), "utf8") }));

/** Every `import … from "x"`, `export … from "x"` and `import "x"` in a source: x, and the value names it takes (not
 *  the `type` ones). */
function importsOf(text: string) {
  return [
    ...text.matchAll(
      /\b(?:import|export)\s+(?:(type\s+)?([^;"]*?)\s+from\s+)?"([^"]+)"/g,
    ),
  ].map(([, onlyTypes, what = "", from]) => {
    const named = /\{([\s\S]*)\}/.exec(what)?.[1] ?? "";
    const values = onlyTypes
      ? []
      : named
          .split(",")
          .map((n) => n.trim())
          .filter((n) => n && !n.startsWith("type "))
          .map((n) => n.split(/\s+as\s+/)[0]);
    return { from, values };
  });
}

describe("the Code plugin's web part", () => {
  it("imports only react, react-dom, @keel/web-sdk and its own files", () => {
    const outside = sources().flatMap(({ file, text }) =>
      importsOf(text)
        .filter(({ from }) => !from.startsWith("./") && !SHARED.includes(from))
        .map(({ from }) => `${file}: ${from}`),
    );
    expect(outside).toEqual([]);
  });

  it("finds every name it takes from @keel/web-sdk in it", () => {
    const used = sources().flatMap(({ text }) =>
      importsOf(text)
        .filter((i) => i.from === "@keel/web-sdk")
        .flatMap((i) => i.values),
    );
    expect(used).toEqual(
      expect.arrayContaining([
        "definePlugin",
        "SLOTS",
        "useSlot",
        "rankFiles",
        "fuzzy",
        "openLauncher",
        "parseHash",
        "parseDiff",
        "FileIcon",
        "askAssistant",
        "get",
        // 0.15.3–0.15.4: a .md file rendered (copies as Markdown), Focus mode's key and event, keel's key helpers
        "MarkdownView",
        "FOCUS_EVENT",
        "FOCUS_KEYS",
        "isTyping",
        "modalOpen",
      ]),
    );
    expect(used.filter((name) => !(name in sdk))).toEqual([]);
  });
});
