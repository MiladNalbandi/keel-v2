// keel 0.28 the redesign of the Code page: the head's Go to file and Ask KeelBot, the Show switch (Code / Changes), the
// column on the right (the outline and "This file in keel"), the symbol in the path, and the keys strip. And the outline
// reader itself (outline.ts). It runs inside keel's web with every plugin, as the full image has it.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { restorePlugins } from "../../../../web/src/test/coreOnly";
import { outlineOf, symbolAt } from "../outline";

afterEach(() => {
  restorePlugins();
  localStorage.removeItem("keel2.repo.info");
});

const head = async () => (await screen.findByRole("heading", { name: "Code" })).closest(".top") as HTMLElement;
const row = (n: number) => document.querySelector<HTMLElement>(`.ed-body .cv-row[data-line="${n}"]`);

describe("the Code page after the redesign", () => {
  it("the head's Go to file opens quick open, and Ask KeelBot opens KeelBot's column", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    const top = await head();
    await user.click(within(top).getByRole("button", { name: /^Go to file/ }));
    expect(await screen.findByRole("dialog", { name: "Quick open" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Quick open" })).toBeNull());
    await user.click(within(top).getByRole("button", { name: /^Ask KeelBot/ }));
    expect(await screen.findByRole("textbox", { name: "Ask KeelBot" })).toBeInTheDocument();
  });

  it("Code and Changes are one switch; the pressed part says what shows", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo/api/ScoreController.kt";
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    const show = screen.getByRole("group", { name: "Show" });
    const code = within(show).getByRole("button", { name: "Code" });
    const changes = within(show).getByRole("button", { name: "Changes" });
    expect(code).toHaveAttribute("aria-pressed", "true");
    expect(changes).toHaveAttribute("aria-pressed", "false");
    await user.click(changes);
    expect(await screen.findByRole("region", { name: "Changes in api/ScoreController.kt" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Show" })).getByRole("button", { name: "Code" })).toHaveAttribute("aria-pressed", "false");
    // the side by side layout is a switch beside it
    expect(screen.getByRole("group", { name: "Diff layout" })).toBeInTheDocument();
    await user.click(within(screen.getByRole("group", { name: "Show" })).getByRole("button", { name: "Code" }));
    expect(await screen.findByRole("region", { name: "Code of api/ScoreController.kt" })).toBeInTheDocument();
  });

  it("the column on the right lists the file's symbols (a click goes to the line) and what keel knows about it", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo/api/ScoreController.kt";
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    const info = await screen.findByRole("complementary", { name: "About this file" });
    const outline = within(info).getByRole("region", { name: "Outline" });
    expect(within(outline).getAllByRole("button").map((b) => b.textContent?.replace(/\d+$/, ""))).toEqual(["CScoreController", "ƒsave", "ƒtop"]);
    await user.click(within(outline).getByRole("button", { name: "top" }));
    await waitFor(() => expect(row(13)).toHaveClass("tgt"));
    expect(location.hash).toBe("#/repo/api/ScoreController.kt:13");

    const keel = within(info).getByRole("region", { name: "This file in keel" });
    expect(keel).toHaveTextContent("Editable in ac-gate by the phase's agent.");
    expect(keel).toHaveTextContent("AC-002");
    expect(keel).toHaveTextContent("a81c3f0");
    // History and rules: the keel view in the side bar
    await user.click(within(keel).getByRole("button", { name: "History and rules" }));
    expect(screen.getByRole("button", { name: "keel" })).toHaveAttribute("aria-pressed", "true");

    // the Outline button hides the column, and the choice is remembered
    await user.click(screen.getByRole("button", { name: "Outline" }));
    expect(screen.queryByRole("complementary", { name: "About this file" })).toBeNull();
    expect(localStorage.getItem("keel2.repo.info")).toBe("false");
  });

  it("the path ends with the symbol the cursor is in", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo/api/ScoreController.kt:9";
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    await waitFor(() => expect(row(9)).toBeTruthy());
    await user.click(row(9)!.querySelector(".cv-code")!);
    const path = screen.getByRole("navigation", { name: "Path" });
    await waitFor(() => expect(within(path).getByRole("button", { name: "save()" })).toBeInTheDocument());
  });

  it("the side bar shows the reading keys", async () => {
    location.hash = "#/repo";
    render(<App />);
    const keys = await screen.findByRole("group", { name: "Keys" });
    expect(keys).toHaveTextContent(/file/);
    expect(keys).toHaveTextContent(/search/);
    expect(keys).toHaveTextContent(/ask/);
    expect(keys).toHaveTextContent(/all keys/);
  });
});

describe("outline.ts", () => {
  it("reads Python classes and functions, nested by indent", () => {
    const py = 'class Row:\n    place: int\n\n    def label(self):\n        return ""\n\nasync def board(scores):\n    pass\n';
    expect(outlineOf(py, "src/scoreboard.py")).toEqual([
      { name: "Row", kind: "class", line: 1, depth: 0 },
      { name: "label", kind: "fn", line: 4, depth: 1 },
      { name: "board", kind: "fn", line: 7, depth: 0 },
    ]);
  });

  it("reads TypeScript functions, arrow functions, classes and types", () => {
    const ts = [
      "export type Row = { n: number };",
      "export interface Board { rows: Row[] }",
      "export function top(n: number) {}",
      "const rank = (s: string) => s.length;",
      "export const label = async (r: Row): Promise<string> => '';",
      "const limit = 10;",
      "export default class Scores {",
      "}",
    ].join("\n");
    expect(outlineOf(ts, "web/a.ts").map((s) => [s.name, s.kind])).toEqual([
      ["Row", "type"], ["Board", "type"], ["top", "fn"], ["rank", "fn"], ["label", "fn"], ["Scores", "class"],
    ]);
  });

  it("reads Markdown headings outside code fences, and nothing for a file kind it does not know", () => {
    const md = "# Title\n\n```\n# not a heading\n```\n\n## Usage\n### Keys\n";
    expect(outlineOf(md, "README.md")).toEqual([
      { name: "Title", kind: "head", line: 1, depth: 0 },
      { name: "Usage", kind: "head", line: 7, depth: 1 },
      { name: "Keys", kind: "head", line: 8, depth: 2 },
    ]);
    expect(outlineOf("a: 1\n", "config.yml")).toEqual([]);
  });

  it("symbolAt finds the symbol a line is in", () => {
    const syms = outlineOf("fun a() {\n}\nfun b() {\n  x\n}\n", "x.kt");
    expect(symbolAt(syms, 4)?.name).toBe("b");
    expect(symbolAt(syms, 1)?.name).toBe("a");
    expect(symbolAt(syms, null)).toBeNull();
    expect(symbolAt(outlineOf("\n\nfun a() {}\n", "x.kt"), 1)).toBeNull();
  });
});
