// keel 0.18.9 KeelBot's thinking effort: /model then its effort, /effort [level], and the effort on job lines,
// agent notices and KeelBot's turn footer.
import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";
import { complete, run } from "../src/core/commands";
import { effortsOf, modelText } from "../src/core/effort";
import { KeelCore } from "../src/core/keel";
import { notice } from "../src/core/notices";
import * as fmt from "../src/core/format";
import type { Catalog, Job } from "../src/core/sdk";
import { App, type Probe } from "../src/ui/App";
import { stripAnsi } from "../src/script";
import { stubKeel, tick, waitFor } from "./stub";

const CATALOG = {
  claude: { label: "Claude", modes: { api: [{ id: "sonnet", label: "Sonnet" }, { id: "haiku", label: "Haiku", efforts: [] }] },
    efforts: ["low", "medium", "high"], default: { mode: "api", model: "sonnet" }, source: "builtin" },
  fake: { label: "Fake", modes: { api: [{ id: "fake", label: "Fake" }] }, efforts: [], default: { mode: "api", model: "fake" }, source: "builtin" },
} as Catalog;

/** The stub keel plus the catalog and a chat's PATCH. */
function keel() {
  const stub = stubKeel();
  const patches: unknown[] = [];
  const fetch: typeof globalThis.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    const json = (d: unknown) => new Response(JSON.stringify(d), { status: 200, headers: { "content-type": "application/json" } });
    if (url.pathname === "/api/providers/models") return json(CATALOG);
    if (init?.method === "PATCH" && url.pathname === "/api/projects/demo/helper/sessions/s1") {
      const body = JSON.parse(String(init.body));
      patches.push(body);
      stub.state.session = { ...stub.state.session, ...body };
      return json(stub.state.session);
    }
    return stub.fetch(input, init);
  };
  const core = new KeelCore({ url: "http://keel.test", fetch, noLive: true });
  return { stub, core, patches, ctx: { core, width: 100 } };
}

describe("thinking effort", () => {
  it("takes a model's own efforts, else its provider's; [] = none", () => {
    expect(effortsOf(CATALOG, "claude", "api", "sonnet")).toEqual(["low", "medium", "high"]);
    expect(effortsOf(CATALOG, "claude", "api", "haiku")).toEqual([]);
    // #101 a model saved for a subscription runs on the API: it gets the API's efforts
    expect(effortsOf(CATALOG, "claude", "subscription", "sonnet")).toEqual(["low", "medium", "high"]);
    expect(effortsOf(CATALOG, "fake", "api", "fake")).toEqual([]);
    expect(effortsOf(null, "claude", "subscription", "sonnet")).toEqual([]);
    expect(modelText({ provider: "claude", model: "sonnet", effort: "high" })).toBe("claude/sonnet · high");
    expect(modelText({ provider: "claude", model: "sonnet", effort: null })).toBe("claude/sonnet");
  });

  it("/model then the effort pick sets the model with its effort", async () => {
    const { core, ctx } = keel();
    const r = await run(ctx, "/model");
    expect(r.pick?.title).toBe("KeelBot's model");
    const sonnet = r.pick!.choices.find((c) => c.label === "claude/sonnet")!;
    const r2 = await r.pick!.then(sonnet.value);
    expect(r2.pick?.title).toBe("Thinking effort for claude/sonnet");
    expect(r2.pick?.choices.map((c) => c.value)).toEqual(["default", "low", "medium", "high"]);
    expect(r2.pick?.index).toBe(0);
    const r3 = await r2.pick!.then("high");
    expect(core.snapshot.chat.model).toEqual({ provider: "claude", mode: "api", model: "sonnet", effort: "high" });
    expect(r3.blocks[0].text).toBe("KeelBot answers with claude/sonnet · high (api).");
    // again: the title says what is set, the cursors start on it
    const again = await run(ctx, "/model");
    expect(again.pick?.title).toBe("KeelBot's model (now claude/sonnet · high)");
    expect(again.pick?.choices[again.pick.index ?? 0].label).toBe("claude/sonnet");
    const e = await again.pick!.then(sonnet.value);
    expect(e.pick?.choices[e.pick.index ?? 0].value).toBe("high");
  });

  it("/model on a model without efforts sets it at once", async () => {
    const { core, ctx } = keel();
    const r = await run(ctx, "/model");
    const r2 = await r.pick!.then(r.pick!.choices.find((c) => c.label === "claude/haiku")!.value);
    expect(r2.pick).toBeUndefined();
    expect(core.snapshot.chat.model).toEqual({ provider: "claude", mode: "api", model: "haiku" });
  });

  it("/effort high sets it on the open chat, /effort default clears it, a wrong one is refused", async () => {
    const { stub, core, patches, ctx } = keel();
    await core.start();
    stub.state.session = { id: "s1", project: "demo", root: "/work/demo", mode: "ask", title: "t", model: { provider: "claude", mode: "subscription", model: "sonnet" },
      status: "idle", tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0, turns: 0, created_at: "x", updated_at: "x" };
    await core.openChat("s1");
    const r = await run(ctx, "/effort high");
    expect(r.blocks[0]).toEqual({ kind: "ok", text: "KeelBot answers with claude/sonnet · high (subscription)." });
    expect(patches.at(-1)).toEqual({ model: { provider: "claude", mode: "subscription", model: "sonnet", effort: "high" } });
    expect(complete(ctx, "/effort h").map((x) => x.value)).toEqual(["/effort high"]);
    const p = await run(ctx, "/effort");
    expect(p.pick?.choices[p.pick.index ?? 0].value).toBe("high");
    await run(ctx, "/effort default");
    expect(patches.at(-1)).toEqual({ model: { provider: "claude", mode: "subscription", model: "sonnet" } });
    expect(core.snapshot.chat.model?.effort).toBeUndefined();
    const bad = await run(ctx, "/effort turbo");
    expect(bad.blocks[0]).toEqual({ kind: "error", text: 'claude/sonnet has no effort "turbo". Its choices: default, low, medium, high.' });
  });

  it("/effort on a model without an effort setting says so", async () => {
    const { core, ctx } = keel();
    await core.setChatModel({ provider: "claude", mode: "subscription", model: "haiku" });
    expect((await run(ctx, "/effort high")).blocks[0].text).toBe("claude/haiku has no effort setting: keel ignores it.");
    const { ctx: none } = keel();
    expect((await run(none, "/effort")).blocks[0].text).toContain("/model picks one");
  });

  it("shows an agent's effort on its job lines and its start notice", () => {
    const job = { id: "j1", project_id: "demo", agent: "explorer", provider: "claude", model: "sonnet", effort: "high", step: "spec", status: "running",
      started_at: "2026-10-16T10:00:00Z", tokens_in: 1, tokens_out: 1, cost_usd: 0 } as unknown as Job;
    expect(fmt.jobs([job])).toContain("claude/sonnet · high · 2 tokens");
    expect(fmt.live([job], {})).toContain("claude/sonnet · high · 2 tokens");
    expect(fmt.jobs([{ ...job, effort: null }])).toContain("claude/sonnet · 2 tokens");
    expect(notice({ type: "agent.started", at: "x", step: "spec", data: { agent: "explorer", provider: "claude", model: "sonnet", effort: "high" } } as never))
      .toBe("agent explorer started · spec · claude/sonnet · high");
  });

  it("shows KeelBot's effort in its turn footer", async () => {
    const stub = stubKeel();
    const core = new KeelCore({ url: "http://keel.test", fetch: stub.fetch, noLive: true, publicUrl: "http://localhost:8080" });
    const probe: Probe = { started: false, busy: false, pick: false, ask: false, chatBusy: false, answers: 0 };
    const r = render(<App core={core} probe={probe} width={100} />);
    await waitFor(() => probe.started);
    r.stdin.write("hi");
    await tick(30);
    r.stdin.write("\r");
    await waitFor(() => probe.chatBusy);
    stub.answer("Hello.");
    (stub.state.messages.at(-1)!.data as Record<string, unknown>).effort = "high";
    core.onMessage({ event: "helper.finished", data: JSON.stringify({ type: "helper.finished", thread_id: "s1", project_id: "demo", at: "x", data: { status: "done" } }) });
    await waitFor(() => probe.answers === 1);
    await tick(50);
    expect(stripAnsi(r.lastFrame() ?? "")).toMatch(/✓ Finished in \d+\.\ds · KeelBot · fake\/fake · high · 1\.5k tokens/);
    r.unmount();
  });
});
