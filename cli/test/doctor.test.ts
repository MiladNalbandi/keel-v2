// #115 /doctor: the flow doctor's report in keel TUI (the dashboard's Watch › Flow doctor).
import { describe, expect, it } from "vitest";
import { complete, run } from "../src/core/commands";
import { KeelCore } from "../src/core/keel";
import * as fmt from "../src/core/format";
import type { DoctorReport } from "../src/core/sdk";
import { stubKeel } from "./stub";

const NOW = Date.parse("2026-11-14T12:00:00Z");
const kind = (k: Partial<DoctorReport["kinds"][number]>) => ({
  kind: "x", title: "", fix: "", count: 0, repaired: 0, retried: 0, waiting: 0, stopped: 0, failed: 0, last_at: null,
  steps: [], providers: [], where: [], advice: "", example: {}, ...k,
});
const REPORT: DoctorReport = {
  days: 7, since: "2026-11-07T12:00:00Z", until: "2026-11-14T12:00:00Z", total: 3, flows: 2,
  repaired: 2, retried: 0, waiting: 0, stopped: 1, failed: 0,
  kinds: [
    kind({ kind: "rate-limit", title: "The provider's rate limit was reached", count: 2, repaired: 2,
      advice: "The rate limit was hit 2×. Run fewer flows at once, or raise the limit at the provider.",
      example: { error: "\nError code: 429 - rate_limit_error\nmore" } }),
    kind({ kind: "keel-bug", title: "A bug in keel", count: 1, stopped: 1, where: ["runtime/actions.py:120 (1×)"],
      advice: "keel itself failed 1×. Report it with the place below." }),
  ],
  recent: [{ at: "2026-11-14T11:55:00Z", project: "shop", workflow: "quick", step: "make the change", kind: "keel-bug", title: "A bug in keel", end: "stopped" }],
  markdown: "# Flow doctor",
};

function keel(report: DoctorReport = REPORT) {
  const stub = stubKeel();
  const asked: string[] = [];
  const fetch: typeof globalThis.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    if (url.pathname === "/api/flow-doctor/report") {
      asked.push(url.search);
      return new Response(JSON.stringify(report), { status: 200, headers: { "content-type": "application/json" } });
    }
    return stub.fetch(input, init);
  };
  const core = new KeelCore({ url: "http://keel.test", fetch, noLive: true, publicUrl: "http://localhost:8080" });
  return { asked, ctx: { core, width: 100 } };
}

describe("/doctor", () => {
  it("prints what crashed, how it ended, what to improve and where in keel's code", () => {
    const t = fmt.doctor(REPORT, undefined, NOW);
    expect(t).toContain("3 crashes in 2 flows: repaired 2 · stopped 1");
    expect(t).toContain("The provider's rate limit was reached (rate-limit) · 2× · repaired 2");
    expect(t).toContain("    e.g. Error code: 429 - rate_limit_error");
    expect(t).toContain("    The rate limit was hit 2×. Run fewer flows at once, or raise the limit at the provider.");
    expect(t).toContain("    in keel's code: runtime/actions.py:120 (1×)");
    expect(t).toContain("5m ago    shop · quick · make the change · A bug in keel → stopped");
    expect(fmt.doctor({ ...REPORT, total: 0, kinds: [], recent: [] })).toContain("No step crashed in keel's flows");
  });

  it("asks keel for the days given (7 when none, 1 to 90) and links the dashboard's page", async () => {
    const { asked, ctx } = keel();
    const r = await run(ctx, "/doctor");
    expect(r.blocks[0].text).toContain("3 crashes in 2 flows");
    expect(r.blocks[0].text).toContain("http://localhost:8080/#/doctor");
    await run(ctx, "/doctor 30");
    await run(ctx, "/doctor 400");
    expect(asked).toEqual(["?days=7", "?days=30", "?days=90"]);
    expect((await run(ctx, "/doctor soon")).blocks[0]).toMatchObject({ kind: "error" });
    expect(complete(ctx, "/doctor 3").map((x) => x.value)).toEqual(["/doctor 30"]);
  });
});
