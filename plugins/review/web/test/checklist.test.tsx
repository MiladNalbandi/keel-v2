// 0.18.63 the classic Review's Checklist tab: its steps were squeezed into a 10px column and drawn over each other,
// because the CI checks list (Checks.tsx) gave the same class name, .rv-check, a 4-column grid. The steps have their
// own class now.

import { render } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ChecklistTab } from "../ReviewPanel";
import type { ReviewView } from "../reviewApi";

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.join(here, "..", "review.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
/** Every rule whose selector list names `cls` on its own (not .rv-check-dot and the like). */
const rules = (cls: string) =>
  [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
    .filter((m) => m[1].split(",").some((sel) => new RegExp(`\\.${cls}(?![\\w-])`).test(sel)))
    .map((m) => m[2]);

describe("the Checklist tab", () => {
  it("lists its steps in one column with a class no other part styles", () => {
    const view = { checks: [], drafts: [] } as unknown as ReviewView;
    const { container } = render(<ChecklistTab view={view} ai={null} checked={[]} onCheck={() => undefined} />);
    const list = container.querySelector("ol")!;
    expect(list.className).toBe("rv-steps");
    expect(list.querySelectorAll("li input[type=checkbox]")).toHaveLength(7);
    const own = rules("rv-steps").join(" ");
    expect(own).toMatch(/display:\s*grid/);
    expect(own).not.toMatch(/grid-template-columns/);
  });
});
