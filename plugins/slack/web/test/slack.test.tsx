// Settings › Slack inside keel's web (web/src/test/setup.ts runs every plugin's setup()): what goes where, a refused token, who may act, and that no
// token ever comes back to the page.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import { parsePeople, type SlackView } from "../Slack";

const OFF: SlackView = { linked: false, state: "off", problem: null, bot_hint: null, app_hint: null,
  config: { on: true, channel: "", allowed: [], send_detail: false, project: null, team_name: null, allow_chat: false, chat_day_usd: 1, chat_day_tokens: 200000, allow_start: false, allow_all_gates: false, strict_allowed: [] }, log: [],
  sends_slack: "To Slack go: titles and status.", sends_model: "To your model company: nothing from Slack by itself." };

let state: SlackView;
let posted: Record<string, unknown>[];
const main = () => within(document.getElementById("main")!);
const open = async () => {
  location.hash = "#/settings/slack";
  render(<App />);
  return main().findByText(/What goes where/i);
};

beforeEach(() => {
  state = OFF;
  posted = [];
  server.use(
    http.get("/api/plugins/slack", () => HttpResponse.json(state)),
    http.post("/api/plugins/slack", async ({ request }) => {
      const b = (await request.json()) as Record<string, unknown>;
      posted.push(b);
      if (b.bot_token === "xoxb-bad") return HttpResponse.json({ error: "Slack said: invalid_auth." }, { status: 400 });
      state = { ...state, linked: true, state: "connected", bot_hint: "…abc", app_hint: "…def",
        config: { ...state.config, team_name: "Northwind", send_detail: b.send_detail === undefined ? state.config.send_detail : (b.send_detail as boolean) } };
      return HttpResponse.json(state);
    }),
    http.delete("/api/plugins/slack", () => {
      state = OFF;
      return HttpResponse.json(state);
    }),
  );
});

describe("Settings › Slack", () => {
  it("has its tab and says what goes to Slack and to the model company", async () => {
    expect(slotItems(SLOTS.settingsTab).map((t) => t.id)).toContain("slack");
    await open();
    expect(main().getByText(/To Slack go: titles and status/)).toBeTruthy();
    expect(main().getByText(/nothing from Slack by itself/)).toBeTruthy();
    expect(main().getByText(/What stays in keel/i)).toBeTruthy();
  });

  it("shows why a token is refused", async () => {
    await open();
    await userEvent.type(main().getByLabelText("Bot token"), "xoxb-bad");
    await userEvent.click(main().getByRole("button", { name: "Save" }));
    expect((await main().findByRole("alert")).textContent).toContain("invalid_auth");
  });

  it("saves tokens, the channel and who may act, and shows no token again", async () => {
    await open();
    await userEvent.type(main().getByLabelText("Bot token"), "xoxb-good");
    await userEvent.type(main().getByLabelText("App-level token"), "xapp-good");
    await userEvent.type(main().getByLabelText("Channel id for gates"), "C123");
    await userEvent.type(main().getByLabelText("Who may answer and make tasks"), "U123ABC Ann Lee");
    await userEvent.click(main().getByRole("button", { name: "Save" }));
    await main().findByText(/connected/);
    expect(posted[0]).toMatchObject({ bot_token: "xoxb-good", app_token: "xapp-good", channel: "C123", allowed: [{ id: "U123ABC", name: "Ann Lee" }] });
    expect(document.body.textContent).not.toContain("xoxb-good");
    expect((main().getByLabelText("Bot token") as HTMLInputElement).value).toBe("");
    expect((main().getByLabelText("Bot token") as HTMLInputElement).type).toBe("password");
  });

  it("the gate's whole text is off until the person turns it on", async () => {
    await open();
    const row = main().getByRole("switch", { name: /whole text/ });
    expect(row.getAttribute("aria-checked")).toBe("false");
    await userEvent.click(row);
    await waitFor(() => expect(posted[0]).toEqual({ send_detail: true }));
  });

  it("reads one person per line", () => {
    expect(parsePeople("U1 Ann Lee\n\n  W2  \n")).toEqual([{ id: "U1", name: "Ann Lee" }, { id: "W2", name: "" }]);
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});

describe("0.21.0 more from Slack: each power off until you turn it on", () => {
  const config = { on: true, channel: "C1", allowed: [{ id: "U1", name: "Ann" }, { id: "U2", name: "Bea" }], send_detail: false, project: null, team_name: "Northwind",
    allow_chat: false, chat_day_usd: 1, chat_day_tokens: 200000, allow_start: false, allow_all_gates: false, strict_allowed: [] as { id: string; name: string }[] };
  const view = (over: Partial<typeof config> = {}) => ({ linked: true, state: "connected", problem: null, bot_hint: "b", app_hint: "a", config: { ...config, ...over }, log: [],
    sends_slack: "To Slack go: titles.", sends_model: "To your model company: with the chat on, each message." });

  function serve(first = view(), sent: Record<string, unknown>[] = []) {
    let v = first;
    server.use(
      http.get("/api/plugins/slack", () => HttpResponse.json(v)),
      http.post("/api/plugins/slack", async ({ request }) => {
        const b = (await request.json()) as Record<string, unknown>;
        sent.push(b);
        v = { ...v, config: { ...v.config, ...b } };
        return HttpResponse.json(v);
      }),
    );
    return sent;
  }

  const open = async () => {
    location.hash = "#/settings/slack";
    render(<App />);
    return screen.findByText("More from Slack (all off until you turn them on)");
  };

  it("all three are off, and the page says what the chat sends and what stays in keel", async () => {
    serve();
    await open();
    expect(screen.getByText("Let people ask keel in Slack")).toBeInTheDocument();
    expect(screen.getByText("Let people start flows from Slack")).toBeInTheDocument();
    expect(screen.getByText("Allow every gate from Slack")).toBeInTheDocument();
    expect(screen.getByText(/goes to your model company/)).toBeInTheDocument();
    expect(screen.getByText("Budget and secret gates, always.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Who may answer these")).toBeNull();
    expect(screen.queryByLabelText(/Daily cap per person \(USD\)/)).toBeNull();
  });

  it("the chat's caps show when it is on, and they save", async () => {
    const sent = serve(view({ allow_chat: true }));
    await open();
    const usd = screen.getByLabelText(/Daily cap per person \(USD\)/);
    await userEvent.clear(usd);
    await userEvent.type(usd, "2.5");
    await userEvent.tab();
    await waitFor(() => expect(sent).toContainEqual({ chat_day_usd: 2.5 }));
  });

  it("every gate from Slack warns, and asks for the short list", async () => {
    const sent = serve(view({ allow_all_gates: true }));
    await open();
    expect(screen.getByText(/Slack is not keel's sign-in/)).toBeInTheDocument();
    const box = screen.getByLabelText("Who may answer these");
    await userEvent.type(box, "U1 Ann");
    await userEvent.tab();
    await waitFor(() => expect(sent).toContainEqual({ strict_allowed: [{ id: "U1", name: "Ann" }] }));
  });
});
