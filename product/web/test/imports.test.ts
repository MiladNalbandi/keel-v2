// keel Product's web part is a plugin: its pages import only react, @keel/web-sdk and their own files (keel's page
// shares the first two at run time), and every name they take from @keel/web-sdk is in it.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as sdk from "@keel/web-sdk";

const HERE = join(__dirname, "..");

/** product/web's own source files (not its tests): what its plugin bundle is built from. */
const sources = () =>
  readdirSync(HERE)
    .filter((f) => /\.tsx?$/.test(f) && f !== "vite.config.ts")
    .map((f) => ({ file: f, text: readFileSync(join(HERE, f), "utf8") }));

/** Every `import … from "x"` and `import "x"` in a source: x, and the value names it imports (not the `type` ones). */
function importsOf(text: string) {
  return [
    ...text.matchAll(/\bimport\s+(?:(type\s+)?([^;"]*?)\s+from\s+)?"([^"]+)"/g),
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

describe("keel Product's web part", () => {
  it("imports only react, @keel/web-sdk and its own files", () => {
    const outside = sources().flatMap(({ file, text }) =>
      importsOf(text)
        .filter(
          ({ from }) =>
            !from.startsWith("./") &&
            from !== "react" &&
            from !== "@keel/web-sdk",
        )
        .map(({ from }) => `${file}: ${from}`),
    );
    expect(outside).toEqual([]);
  });

  it("finds every name it imports from @keel/web-sdk in it", () => {
    const used = sources().flatMap(({ text }) =>
      importsOf(text)
        .filter((i) => i.from === "@keel/web-sdk")
        .flatMap((i) => i.values),
    );
    expect(used).toContain("useLoad");
    expect(used.filter((name) => !(name in sdk))).toEqual([]);
  });
});
