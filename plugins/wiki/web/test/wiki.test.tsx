// The Wiki plugin's page inside keel's web, as the full image has it (web/src/test/setup.ts runs every plugin's
// setup()): its place in the menu, the empty wiki, a knowledge page, "Refresh stale" with the stale sections, and a
// workflow's page as read-only blocks and a table whose steps open "what this step does". Moved from web/src/test
// (pages, blocks, explain) with the page.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the Wiki inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import * as fx from "../../../../web/src/test/fixtures";
import { db, server } from "../../../../web/src/test/setup";
import plugin from "../index";

const main = () => within(document.getElementById("main")!);
const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);
const explains = () =>
  calls("POST", "/api/projects/ludus-engine/workflows/explain-step");

describe("the Wiki plugin's web part", () => {
  it("is a plugin whose setup() puts the Wiki page in the menu, in the Project group right after Graph", async () => {
    expect(plugin.name).toBe("wiki");
    expect(typeof plugin.setup).toBe("function");
    location.hash = "#/flow";
    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    const links = within(nav).getAllByRole("link");
    const names = links.map((l) => l.textContent?.trim());
    expect(names.indexOf("Wiki")).toBe(names.indexOf("Graph") + 1);
    expect(links[names.indexOf("Wiki")]).toHaveAttribute("href", "#/wiki");
  });
});

describe("Wiki", () => {
  it("an empty wiki says what fills it", async () => {
    server.use(
      http.get("/api/projects/:pid/wiki", () =>
        HttpResponse.json({
          sections: [{ id: "knowledge", title: "Knowledge", items: [] }],
        }),
      ),
    );
    location.hash = "#/wiki";
    render(<App />);
    expect(await main().findByText("The wiki is empty")).toBeInTheDocument();
    expect(main().getByRole("link", { name: "Open Flow" })).toHaveAttribute(
      "href",
      "#/flow",
    );
  });

  it("opens the first page: a knowledge section with its words, citations and status", async () => {
    location.hash = "#/wiki";
    render(<App />);
    expect(
      await main().findByRole("heading", { name: "architecture" }),
    ).toBeInTheDocument();
    expect(main().getByText("1180 words · 41 citations")).toBeInTheDocument();
    expect(main().getByText("written")).toBeInTheDocument();
    const tree = main().getByRole("navigation", { name: "Wiki pages" });
    expect(
      within(tree).getByRole("link", { name: "architecture" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      within(tree).getByRole("link", { name: "feature (keel)" }),
    ).toHaveAttribute("href", "#/wiki/wf%3Afeature");
  });

  it("Refresh stale asks the librarians for the stale knowledge sections only", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/projects/:pid/wiki", () =>
        HttpResponse.json({
          ...fx.wiki,
          sections: [
            {
              id: "knowledge",
              title: "Knowledge base",
              items: [
                {
                  id: "kb:architecture",
                  title: "Architecture",
                  status: "written",
                },
                { id: "kb:domain", title: "Domain", status: "stale" },
              ],
            },
          ],
        }),
      ),
    );
    location.hash = "#/wiki";
    render(<App />);
    await user.click(
      await main().findByRole("button", { name: "Refresh stale" }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/wiki/refresh")[0]?.body,
      ).toEqual({ sections: ["domain"] }),
    );
    expect(
      await screen.findByRole("link", { name: "Watch it in Flow" }),
    ).toHaveAttribute("href", "#/flow");
  });
});

describe("Wiki workflow page", () => {
  it("shows the workflow as read-only blocks; a click opens what it does", async () => {
    const user = userEvent.setup();
    location.hash = "#/wiki/wf%3Afeature";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: /^verify_red, plain code/ }),
    );
    expect(
      await screen.findByRole("dialog", {
        name: /What verify_red does|What commit contract does/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Remove / }),
    ).not.toBeInTheDocument();
  });

  it("what this step does opens from the blocks and the table, with placeholders", async () => {
    const user = userEvent.setup();
    location.hash = "#/wiki/wf%3Afeature";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: /^red, agent/ }),
    );
    const dlg = await screen.findByRole("dialog", { name: "What red does" });
    await waitFor(() =>
      expect(explains().at(-1)?.body).toEqual({
        step_id: "s3",
        workflow_id: "feature",
      }),
    );
    expect(
      await within(dlg).findByText(/with «placeholders»/),
    ).toBeInTheDocument();
    expect(
      within(dlg).queryByRole("region", { name: "Last run" }),
    ).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "verify_red" }));
    await waitFor(() =>
      expect(explains().at(-1)?.body).toEqual({
        step_id: "s4",
        workflow_id: "feature",
      }),
    );
  });
});
