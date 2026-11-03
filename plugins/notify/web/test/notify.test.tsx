// Settings › Notify inside keel's web (web/src/test/setup.ts runs every plugin's setup()): what is sent, no channel, adding one
// (its webhook goes up once and never comes back), a refused address, the test button, the switch and the last messages.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import type { Channel, NotifyView } from "../Notify";

const CHAT: Channel = { id: "c1", kind: "chat", name: "Team chat", on: true, events: ["waiting"], project: null, host: "hooks.example.com",
  port: 587, security: "starttls", user: "", from: "", to: [] };
const SENDS = "Only the project's name, the flow's title, the gate's name and where to answer.";

let state: NotifyView;
let posted: { path: string; body: Record<string, unknown> }[];

function api() {
  posted = [];
  server.use(
    http.get("/api/plugins/notify/channels", () => HttpResponse.json(state)),
    http.post("/api/plugins/notify/channels", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      posted.push({ path: "add", body });
      if (String(body.url).startsWith("http://8.8"))
        return HttpResponse.json({ error: "Over the internet a webhook must use https://" }, { status: 400 });
      state = { ...state, channels: [...state.channels, { ...CHAT, id: "c2", name: String(body.name) }] };
      return HttpResponse.json(state.channels[state.channels.length - 1]);
    }),
    http.post("/api/plugins/notify/channels/:id/test", ({ params }) => {
      posted.push({ path: `test:${String(params.id)}`, body: {} });
      return HttpResponse.json({ ok: false, error: "The chat service answered 403." });
    }),
    http.post("/api/plugins/notify/channels/:id", async ({ request, params }) => {
      const body = (await request.json()) as Partial<Channel>;
      posted.push({ path: `change:${String(params.id)}`, body });
      state = { ...state, channels: state.channels.map((c) => (c.id === params.id ? { ...c, ...body } : c)) };
      return HttpResponse.json(state.channels[0]);
    }),
    http.delete("/api/plugins/notify/channels/:id", ({ params }) => {
      state = { ...state, channels: state.channels.filter((c) => c.id !== params.id) };
      return HttpResponse.json({ removed: true });
    }),
  );
}

const main = () => within(document.getElementById("main")!);
const open = async () => {
  location.hash = "#/settings/notify";
  render(<App />);
  return main().findByText(/What is sent/);
};

describe("Settings › Notify", () => {
  beforeEach(() => {
    state = { channels: [], sent: [], sends: SENDS, example: "keel: a flow waits for you in shop." };
    api();
  });

  it("has its tab and says what is sent, before any channel", async () => {
    expect(slotItems(SLOTS.settingsTab).map((t) => t.id)).toContain("notify");
    await open();
    expect(main().getByText(SENDS)).toBeTruthy();
    expect(main().getByText("keel sends nothing outside until you add one.")).toBeTruthy();
  });

  it("adds a chat channel; the webhook goes up once and the field is emptied", async () => {
    await open();
    const form = main().getByRole("form", { name: "Add a channel" });
    await userEvent.type(within(form).getByLabelText(/^Name/), "Team chat");
    const hook = within(form).getByLabelText(/Webhook address/) as HTMLInputElement;
    await userEvent.type(hook, "https://hooks.example.com/T1/secret");
    await userEvent.click(within(form).getByRole("button", { name: "Add the channel" }));
    await main().findByRole("listitem", { name: "Team chat" });
    expect(posted[0].body).toMatchObject({ kind: "chat", name: "Team chat", url: "https://hooks.example.com/T1/secret" });
    expect(hook.value).toBe("");
    expect(hook.type).toBe("password");
  });

  it("shows why an address is refused", async () => {
    await open();
    const form = main().getByRole("form", { name: "Add a channel" });
    await userEvent.type(within(form).getByLabelText(/Webhook address/), "http://8.8.8.8/hook");
    await userEvent.click(within(form).getByRole("button", { name: "Add the channel" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("must use https");
  });

  it("adds a mail channel with its server, From and To", async () => {
    await open();
    const form = main().getByRole("form", { name: "Add a channel" });
    await userEvent.click(within(form).getByRole("button", { name: "Mail" }));
    await userEvent.type(within(form).getByLabelText(/Mail server/), "mail.example.com");
    await userEvent.type(within(form).getByLabelText(/^From/), "keel@example.com");
    await userEvent.type(within(form).getByLabelText(/^To/), "a@example.com, b@example.com");
    await userEvent.click(within(form).getByRole("button", { name: "Add the channel" }));
    await waitFor(() => expect(posted[0]).toBeTruthy());
    expect(posted[0].body).toMatchObject({ kind: "mail", host: "mail.example.com", port: 587, security: "starttls", from: "keel@example.com",
      to: ["a@example.com", "b@example.com"] });
  });

  it("a channel's test says what failed, its switch and boxes change it, and it can be removed", async () => {
    state = { ...state, channels: [CHAT], sent: [{ at: "2026-10-01T10:00:00Z", channel: "c1", channel_name: "Team chat", text: "keel: test", ok: false, error: "answered 403" }] };
    await open();
    const row = await main().findByRole("listitem", { name: "Team chat" });
    expect(main().getByRole("list", { name: "Last messages" }).textContent).toContain("failed: answered 403");
    await userEvent.click(within(row).getByRole("button", { name: "Send a test" }));
    await waitFor(() => expect(posted.some((p) => p.path === "test:c1")).toBe(true));
    await userEvent.click(within(row).getByLabelText(/a flow failed/));
    await waitFor(() => expect(posted.find((p) => p.path === "change:c1")?.body).toEqual({ events: ["waiting", "failed"] }));
    await userEvent.click(within(await main().findByRole("listitem", { name: "Team chat" })).getByRole("button", { name: "Remove" }));
    expect(await main().findByText("keel sends nothing outside until you add one.")).toBeTruthy();
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});
