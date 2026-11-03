// The Desk page and its Inbox card inside keel's web (web/src/test/setup.ts runs every plugin's setup()): what can go to the model
// company is on the screen, a request is added, a text with personal data warns before Sort, and only Sort starts anything.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import type { InboxCardItem } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import type { DeskRequest, DeskView } from "../Desk";

const SENDS = "When you press Sort, the request's text and the titles of its past cases go to your model company.";
const REQ: DeskRequest = { id: "d1", project_id: "p", title: "Login breaks", body: "I cannot log in, write to ann@example.com", origin: "Ann", status: "new", kind: null, urgency: null, area: null,
  suggest: null, sorted_text: null, note: null, thread_id: null, choice: null, task_id: null, created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-01T10:00:00Z" };

let one: DeskView;
let calls: string[];
const main = () => within(document.getElementById("main")!);
const open = (hash: string) => { location.hash = hash; render(<App />); };

beforeEach(() => {
  one = { request: REQ, past: [{ type: "task", id: "t9", title: "Fix the login page", status: "done", score: 0.5 }], warnings: ["an e-mail address"] };
  calls = [];
  server.use(
    http.get("/api/projects/:pid/desk/requests", () => HttpResponse.json({ requests: [one.request], sends: SENDS })),
    http.post("/api/projects/:pid/desk/requests", async ({ request }) => {
      calls.push(`add:${JSON.stringify(await request.json())}`);
      return HttpResponse.json(one);
    }),
    http.get("/api/projects/:pid/desk/requests/:id", () => HttpResponse.json(one)),
    http.post("/api/projects/:pid/desk/requests/:id/sort", () => {
      calls.push("sort");
      one = { ...one, request: { ...one.request, status: "sorted", kind: "bug", urgency: "high", area: "src/login", suggest: "fix", sorted_text: "It breaks.\n\nKIND: bug", thread_id: "t1" } };
      return HttpResponse.json(one.request);
    }),
    http.post("/api/projects/:pid/desk/requests/:id/close", () => {
      calls.push("close");
      one = { ...one, request: { ...one.request, status: "closed" } };
      return HttpResponse.json(one.request);
    }),
  );
});

describe("the Desk", () => {
  it("says what can go to the model company on the page, and lists the requests", async () => {
    open("#/desk");
    expect((await main().findByText(/What can go to the model company/)).parentElement?.textContent).toContain(SENDS);
    const table = await main().findByRole("table", { name: "Requests" });
    expect(within(table).getByRole("link", { name: "Login breaks" }).getAttribute("href")).toBe("#/desk/d1");
  });

  it("adds a request and starts nothing", async () => {
    open("#/desk");
    const form = await main().findByRole("form", { name: "Add a request" });
    await userEvent.type(within(form).getByLabelText(/The request/), "The export is empty");
    await userEvent.type(within(form).getByLabelText(/^From/), "Ben");
    await userEvent.click(within(form).getByRole("button", { name: "Add it" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0]).toContain("The export is empty");
    expect(calls).not.toContain("sort");
  });

  it("warns about personal data before Sort, shows the past cases, and Sort shows how it was sorted", async () => {
    open("#/desk/d1");
    expect((await main().findByRole("alert")).textContent).toContain("an e-mail address");
    expect(main().getByRole("list", { name: "Past cases" }).textContent).toContain("Fix the login page");
    expect(calls).toEqual([]);
    await userEvent.click(main().getByRole("button", { name: "Sort it" }));
    await main().findByText("How it was sorted");
    expect(calls).toEqual(["sort"]);
    expect(main().getByRole("link", { name: "Choose what happens next" }).getAttribute("href")).toBe("#/flow/t1");
    expect(main().queryByRole("button", { name: "Sort it" })).toBeNull();
  });

  it("has a card for a new request in the Inbox", () => {
    const card = slotItems<InboxCardItem>(SLOTS.inboxCard).find((c) => c.id === "desk");
    expect(card?.kinds).toEqual({ "desk-request": "support request" });
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});
