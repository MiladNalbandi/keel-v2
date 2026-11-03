// The Library page inside keel's web (web/src/test/setup.ts runs every plugin's setup()): what can go to the model company, whether
// agents reach it in this project, the local search, adding a document, a document's switch and a new version.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import type { Doc, DocRow } from "../Library";

const SENDS = "What an agent finds here goes to your model company.";
const DOC: Doc = { id: "l1", project_id: null, title: "Data rules", source: "March edition", for_agents: true, version: 1, updated_at: "2026-10-01T10:00:00Z", size: 20, body: "# Backups\n\nBackups run each night." };

let docs: DocRow[];
let doc: Doc;
let on: boolean;
let calls: { what: string; body?: unknown }[];
const main = () => within(document.getElementById("main")!);
const open = (hash: string) => { location.hash = hash; render(<App />); };

beforeEach(() => {
  docs = [DOC];
  doc = DOC;
  on = false;
  calls = [];
  server.use(
    http.get("/api/projects/:pid/library/docs", () => HttpResponse.json({ docs, sends: SENDS })),
    http.post("/api/projects/:pid/library/docs", async ({ request }) => {
      calls.push({ what: "add", body: await request.json() });
      return HttpResponse.json({ ...DOC, id: "l2" });
    }),
    http.get("/api/projects/:pid/library/docs/:id", () => HttpResponse.json(doc)),
    http.post("/api/projects/:pid/library/docs/:id", async ({ request }) => {
      const body = (await request.json()) as Partial<Doc>;
      calls.push({ what: "change", body });
      doc = { ...doc, ...body, version: body.body ? doc.version + 1 : doc.version };
      return HttpResponse.json(doc);
    }),
    http.get("/api/projects/:pid/library/search", ({ request }) => {
      calls.push({ what: "search", body: new URL(request.url).searchParams.get("q") });
      return HttpResponse.json(new URL(request.url).searchParams.get("q") === "pets" ? [] : [
        { doc_id: "l1", title: "Data rules", section: "Backups", version: 1, quote: "Backups run each night.", score: 8, for_agents: false }]);
    }),
    http.get("/api/projects/:pid/plugins", () => HttpResponse.json([{ name: "library", enabled: on }])),
  );
});

describe("the Library", () => {
  it("says what can go to the model company, and that agents do not read it while it is off", async () => {
    open("#/library");
    expect((await main().findByText(/What can go to the model company/)).parentElement?.textContent).toContain(SENDS);
    expect((await main().findByText(/off for this project/)).textContent).toContain("no agent reads it here");
    expect(within(main().getByRole("table", { name: "Documents" })).getByRole("link", { name: "Data rules" }).getAttribute("href")).toBe("#/library/l1");
  });

  it("says when agents can read it", async () => {
    on = true;
    open("#/library");
    expect(await main().findByText(/on for this project/)).toBeTruthy();
  });

  it("searches, shows the quote with its section, and says when it finds nothing", async () => {
    open("#/library");
    const box = await main().findByLabelText("Search words");
    await userEvent.type(box, "backups");
    await userEvent.click(main().getByRole("button", { name: "Search" }));
    const found = await main().findByRole("list", { name: "Found" });
    expect(found.textContent).toContain("Backups run each night.");
    expect(found.textContent).toContain("people only");
    await userEvent.clear(box);
    await userEvent.type(box, "pets");
    await userEvent.click(main().getByRole("button", { name: "Search" }));
    expect(await main().findByText("The library says nothing about that.")).toBeTruthy();
  });

  it("adds a document, for people only when the box is cleared", async () => {
    open("#/library");
    const form = await main().findByRole("form", { name: "Add a document" });
    await userEvent.type(within(form).getByLabelText(/^Title/), "Staff rules");
    await userEvent.type(within(form).getByLabelText(/^The text/), "Only for us.");
    await userEvent.click(within(form).getByLabelText(/Agents may read it/));
    await userEvent.click(within(form).getByRole("button", { name: "Add it" }));
    await waitFor(() => expect(calls.find((c) => c.what === "add")).toBeTruthy());
    expect(calls.find((c) => c.what === "add")?.body).toMatchObject({ title: "Staff rules", body: "Only for us.", for_agents: false, scope: "all" });
  });

  it("a document says who reads it; its switch and a new text change it", async () => {
    open("#/library/l1");
    expect(await main().findByText(/it goes to your model company/)).toBeTruthy();
    await userEvent.click(main().getByRole("switch"));
    expect(await main().findByText(/For people only/)).toBeTruthy();
    expect(calls[0]).toEqual({ what: "change", body: { for_agents: false } });
    await userEvent.click(main().getByRole("button", { name: "Change the text" }));
    const box = main().getByLabelText(/^The text/);
    await userEvent.clear(box);
    await userEvent.type(box, "New words");
    await userEvent.click(main().getByRole("button", { name: "Save a new version" }));
    expect(await main().findByText("Version 2")).toBeTruthy();
  });

  it("gives its documents to the launcher", () => {
    expect(slotItems(SLOTS.launcherSource).map((s) => s.id)).toContain("library");
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});
