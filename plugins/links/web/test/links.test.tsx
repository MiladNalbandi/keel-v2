// Settings › Connections › Linear inside keel's web (web/src/test/setup.ts runs every plugin's setup()): what can go to the model
// company, a key that is refused, a key that is kept (the field is emptied, the key not shown), forgetting it, and the Tools card.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import type { LinearView } from "../Links";

const SENDS = "What an agent reads in Linear goes to your model company.";
const OFF: LinearView = { linked: false, url: "https://api.linear.app/graphql", who: null, at: null, hint: null, sends: SENDS };
const ON: LinearView = { ...OFF, linked: true, who: "Ann", hint: "…KEY" };

let state: LinearView;
let posted: string[];
const main = () => within(document.getElementById("main")!);
const card = async () => {
  location.hash = "#/settings/connections";
  render(<App />);
  return within((await main().findByRole("heading", { name: "Linear" })).closest("section")!);
};

beforeEach(() => {
  state = OFF;
  posted = [];
  server.use(
    http.get("/api/plugins/links/linear", () => HttpResponse.json(state)),
    http.post("/api/plugins/links/linear", async ({ request }) => {
      const { token } = (await request.json()) as { token: string };
      posted.push(token);
      if (token === "bad") return HttpResponse.json({ error: "Linear refused this key", hint: "Nothing was kept." }, { status: 400 });
      state = ON;
      return HttpResponse.json(state);
    }),
    http.delete("/api/plugins/links/linear", () => {
      state = OFF;
      return HttpResponse.json(state);
    }),
  );
});

describe("Connections › Linear", () => {
  it("says what can go to the model company, and that it is not linked", async () => {
    const c = await card();
    expect(c.getByText(/What can go to the model company/).parentElement?.textContent).toContain(SENDS);
    expect(c.getByText("not linked")).toBeTruthy();
  });

  it("shows why a key is refused and keeps nothing", async () => {
    const c = await card();
    await userEvent.type(c.getByLabelText(/API key/), "bad");
    await userEvent.click(c.getByRole("button", { name: "Link Linear" }));
    expect((await c.findByRole("alert")).textContent).toContain("Nothing was kept");
    expect(c.getByText("not linked")).toBeTruthy();
  });

  it("keeps a good key, shows who it is, never the key, and can forget it", async () => {
    const c = await card();
    await userEvent.type(c.getByLabelText(/API key/), "lin_api_SECRET");
    await userEvent.click(c.getByRole("button", { name: "Link Linear" }));
    expect(await c.findByText("linked as Ann")).toBeTruthy();
    expect(posted).toEqual(["lin_api_SECRET"]);
    expect(document.body.textContent).not.toContain("lin_api_SECRET");
    await userEvent.click(c.getByRole("button", { name: "Forget the key" }));
    await waitFor(() => expect(c.getByText("not linked")).toBeTruthy());
  });

  it("has its card on the Tools page", () => {
    expect(slotItems(SLOTS.toolsCard).map((t) => t.id)).toContain("links");
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});
