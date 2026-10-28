// v0.15.8 keel_dashboard links (#/projects/<id>/<page>) and the "started via MCP" timeline line.
import { describe, expect, it } from "vitest";
import { eventLine } from "../components/events";
import { parseHash } from "../routes";
import type { EngineEvent } from "../api";

describe("dashboard links", () => {
  it("a link that names its project opens that project's page", () => {
    expect(parseHash("#/projects/shop/flow")).toEqual({ page: "flow", arg: undefined, project: "shop" });
    expect(parseHash("#/projects/shop/map/er")).toEqual({ page: "map", arg: "er", project: "shop" });
    expect(parseHash("#/projects/my%20app/map/system")).toEqual({ page: "map", arg: "system", project: "my app" });
  });

  it("the All projects page still works", () => {
    expect(parseHash("#/projects")).toEqual({ page: "projects", arg: undefined });
  });
});

describe("timeline", () => {
  const started = (data: Record<string, unknown>) =>
    ({ type: "thread.started", thread_id: "t", project_id: "p", at: "2026-10-14T10:00:00Z", data }) as unknown as EngineEvent;

  it("says when a flow was started via MCP", () => {
    expect(eventLine(started({ via: "mcp" })).text).toBe("flow started via MCP");
    expect(eventLine(started({})).text).toBe("flow started");
  });

  it("0.18.64 says how far the base is behind the remotes when the flow started", () => {
    expect(eventLine(started({ note: "main is 2 commits behind upstream/main" })).text)
      .toBe("flow started · main is 2 commits behind upstream/main");
  });

  it("says who acted through MCP (v0.16.0)", () => {
    const ev = { type: "mcp.call", thread_id: "t", project_id: "p", at: "x", data: { client: "Claude Code", tool: "keel_stop_flow", ok: true } } as unknown as EngineEvent;
    expect(eventLine(ev).text).toBe("via MCP · Claude Code: keel_stop_flow");
  });
});
