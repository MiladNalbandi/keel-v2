import { describe, expect, it } from "vitest";
import { KeelApi, KeelError, normalizeBase, parseCatalog, effortsOf } from "../../src/api/client";
import { fakeFetch, json } from "./fakeFetch";

const api = (f: ReturnType<typeof fakeFetch>, token: string | undefined = "kd_test-token", onRemoved?: () => void) =>
  new KeelApi({ baseUrl: "http://192.168.1.20:8088", token: () => token, fetch: f.fetch, onRemoved });

describe("KeelApi", () => {
  it("pairs with the code and platform vscode, without a token", async () => {
    const f = fakeFetch([json(200, { token: "kd_new", device: { id: "d1", name: "VS Code on laptop", platform: "vscode" }, keel: { name: "keel", version: "0.18.53" } })]);
    const res = await api(f, "should-not-be-sent").pair(" 7KQ-4MZ ", "VS Code on laptop");
    expect(res.token).toBe("kd_new");
    expect(f.seen[0].method).toBe("POST");
    expect(f.seen[0].url).toBe("http://192.168.1.20:8088/api/devices/pair");
    expect(f.seen[0].headers.Authorization).toBeUndefined();
    expect(JSON.parse(f.seen[0].body!)).toEqual({ code: "7KQ-4MZ", name: "VS Code on laptop", platform: "vscode" });
  });

  it("sends the token as Bearer and says it is VS Code", async () => {
    const f = fakeFetch([json(200, { device: { id: "d1", platform: "vscode" } })]);
    const me = await api(f).me();
    expect(me.device?.id).toBe("d1");
    expect(f.seen[0].url).toBe("http://192.168.1.20:8088/api/devices/me");
    expect(f.seen[0].headers.Authorization).toBe("Bearer kd_test-token");
    expect(f.seen[0].headers["X-Keel-Via"]).toBe("vscode");
  });

  it("reads projects, runs and a flow with snake_case fields", async () => {
    const f = fakeFetch([
      json(200, [{ id: "shop", name: "shop", root: "/projects/shop", waiting: 1 }]),
      json(200, [{ thread_id: "t1", title: "Cart totals", status: "waiting", acs_done: 1, acs_total: 3, cost_usd: 0.42 }]),
      json(200, { thread: { thread_id: "t1", status: "waiting", waiting: { id: "q-7", options: ["approve", "reject"] } }, workflow: { id: "feature" } }),
    ]);
    const a = api(f);
    expect((await a.projects())[0].root).toBe("/projects/shop");
    const runs = await a.runs("shop");
    expect(runs[0].acs_total).toBe(3);
    expect(f.seen[1].url).toBe("http://192.168.1.20:8088/api/projects/shop/runs?limit=30");
    const flow = await a.flowOf("shop", "t1");
    expect(flow.thread?.waiting?.id).toBe("q-7");
  });

  it("binds an answer to its question and leaves out empty fields", async () => {
    const f = fakeFetch([json(200, { thread_id: "t1", status: "running" })]);
    await api(f).answer("t1", { decision: "approve", id: "q-7", why: null, payload: { choice: "approve" } });
    expect(f.seen[0].url).toBe("http://192.168.1.20:8088/api/inbox/t1/act");
    expect(JSON.parse(f.seen[0].body!)).toEqual({ decision: "approve", id: "q-7", payload: { choice: "approve" } });
  });

  it("a stale answer is a 409 with keel's words, not a removed device", async () => {
    let removed = 0;
    const f = fakeFetch([json(409, { error: "This flow now asks something else", hint: "Reload it." })]);
    const e = (await api(f, "t", () => removed++).answer("t1", { decision: "approve", id: "old" }).catch((x) => x)) as KeelError;
    expect(e).toBeInstanceOf(KeelError);
    expect(e.status).toBe(409);
    expect(e.say()).toBe("This flow now asks something else Reload it.");
    expect(e.deviceRemoved).toBe(false);
    expect(removed).toBe(0);
  });

  it("a removed device is told once", async () => {
    let removed = 0;
    const f = fakeFetch([json(403, { error: "This device was removed" })]);
    const e = (await api(f, "t", () => removed++).inbox().catch((x) => x)) as KeelError;
    expect(e.deviceRemoved).toBe(true);
    expect(removed).toBe(1);
  });

  it("keel off the network is status 0 with a hint", async () => {
    const f = fakeFetch([new TypeError("fetch failed")]);
    const e = (await api(f).inbox().catch((x) => x)) as KeelError;
    expect(e.unreachable).toBe(true);
    expect(e.hint).toContain("keel2 status");
  });

  it("a web page instead of data is refused", async () => {
    const f = fakeFetch([json(200, "<html>login</html>", "text/html")]);
    const e = (await api(f).projects().catch((x) => x)) as KeelError;
    expect(e.message).toContain("web page");
  });

  it("encodes path parts and review keys", async () => {
    const f = fakeFetch([json(200, { key: "pr:7", files: [] }), json(200, { path: "src/a b.ts", diff: "" })]);
    const a = api(f);
    await a.review("shop", "pr:7");
    await a.diff("shop", "pr:7", "src/a b.ts");
    expect(f.seen[0].url).toBe("http://192.168.1.20:8088/api/projects/shop/review/view?key=pr%3A7");
    expect(f.seen[1].url).toBe("http://192.168.1.20:8088/api/projects/shop/review/diff?key=pr%3A7&path=src%2Fa%20b.ts");
  });

  it("reads the review's whole file at its head and its base (Old | New)", async () => {
    const f = fakeFetch([json(200, { path: "src/a b.ts", ref: "#7", sha: "a1", text: "new\n", truncated: false }), json(200, { path: "src/a.ts", ref: "main", sha: "b2", text: "old\n", truncated: false })]);
    const a = api(f);
    expect((await a.reviewFile("shop", "pr:7", "src/a b.ts", "head")).text).toBe("new\n");
    expect((await a.reviewFile("shop", "pr:7", "src/a.ts", "base")).ref).toBe("main");
    expect(f.seen[0].url).toBe("http://192.168.1.20:8088/api/projects/shop/review/file?key=pr%3A7&path=src%2Fa%20b.ts&side=head");
    expect(f.seen[1].url).toBe("http://192.168.1.20:8088/api/projects/shop/review/file?key=pr%3A7&path=src%2Fa.ts&side=base");
  });

  it("starts a flow with snake_case body and reads job steps", async () => {
    const f = fakeFetch([json(200, { thread_id: "t9", title: "Add refunds" }), json(200, { steps: [{ n: 1, kind: "read", path: "src/orders.ts" }], running: true })]);
    const a = api(f);
    const t = await a.startFlow("shop", { workflow_id: "feature", title: "Add refunds", request: null, where: "worktree", options: { model: "m1" } });
    expect(t.thread_id).toBe("t9");
    expect(JSON.parse(f.seen[0].body!)).toEqual({ workflow_id: "feature", title: "Add refunds", where: "worktree", options: { model: "m1" } });
    const st = await a.jobSteps("c-1", 4);
    expect(st.steps?.[0].path).toBe("src/orders.ts");
    expect(f.seen[1].url).toBe("http://192.168.1.20:8088/api/jobs/c-1/steps?after=4");
  });

  it("builds the events url for one project with every project's notifications", () => {
    const a = api(fakeFetch([]));
    expect(a.eventsUrl("shop")).toBe("http://192.168.1.20:8088/api/events?project=shop&notify=all");
    expect(a.eventsUrl(undefined)).toBe("http://192.168.1.20:8088/api/events?project=*");
    expect(a.streamHeaders()).toEqual({ Accept: "text/event-stream", "X-Keel-Via": "vscode", Authorization: "Bearer kd_test-token" });
  });

  it("normalizes typed addresses", () => {
    expect(normalizeBase("192.168.1.20:8088")).toBe("http://192.168.1.20:8088/");
    expect(normalizeBase("https://keel.example.ts.net/api/")).toBe("https://keel.example.ts.net/");
    expect(normalizeBase(" http://localhost:8088 ")).toBe("http://localhost:8088/");
    expect(normalizeBase("ftp://x")).toBeNull();
    expect(normalizeBase("")).toBeNull();
    expect(() => new KeelApi({ baseUrl: "", token: () => undefined })).toThrow(KeelError);
  });

  it("reads the model catalog in both shapes", () => {
    const c = parseCatalog({
      fake: [{ id: "fake-1" }],
      claude: { label: "Claude", modes: { subscription: [{ id: "sonnet", label: "Sonnet", efforts: ["low", "high"] }], api: [] }, efforts: ["medium"] },
      broken: 3,
    });
    expect(c.map((p) => p.id)).toEqual(["claude", "fake"]);
    expect(Object.keys(c[0].modes)).toEqual(["subscription"]);
    expect(effortsOf(c[0], "sonnet", "subscription")).toEqual(["low", "high"]);
    expect(effortsOf(c[1], "fake-1", "api")).toEqual([]);
  });
});
