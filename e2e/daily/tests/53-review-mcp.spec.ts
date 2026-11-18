// J26 (0.39, #198) the Review page over MCP: keel2 mcp --write reads a branch review the way the page does (files with
// draft ids, the conversation, a file at head and base, a symbol's definition and usages, the diagrams) and acts on it
// (a draft added, changed and deleted, a file marked viewed); a branch has no threads, and the tools say so. The shop
// project gets a branch with a new file; main is checked out again at the end.
import { execFileSync, spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { KEEL2, api, projectNamed } from "../lib/keel";

const SHOP = process.env.KEEL_E2E_SHOP || "";
const git = (...a: string[]) =>
  execFileSync("git", ["-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "-c", "commit.gpgsign=false", ...a], { cwd: SHOP, encoding: "utf8" });

type Call = { name: string; arguments: Record<string, unknown> };

/** One keel2 mcp session: each call in order (the next waits for the answer before it), the text of each answer. */
function session(args: string[], calls: ((prev: string[]) => Call)[]): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const p = spawn(KEEL2, ["mcp", ...args], { stdio: ["pipe", "pipe", "pipe"], env: process.env });
    const out: string[] = [];
    let buf = "";
    const timer = setTimeout(() => { p.kill(); reject(new Error(`keel2 mcp timed out after ${out.length} answer(s): ${buf.slice(-300)}`)); }, 120_000);
    const send = (o: object) => p.stdin.write(JSON.stringify(o) + "\n");
    const next = () => {
      if (out.length === calls.length) { clearTimeout(timer); p.kill(); resolve(out); return; }
      send({ jsonrpc: "2.0", id: 10 + out.length, method: "tools/call", params: calls[out.length](out) });
    };
    p.stdout.on("data", (d) => {
      buf += d;
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line.startsWith("{")) continue;
        const m = JSON.parse(line);
        if (m.id === 1) { send({ jsonrpc: "2.0", method: "notifications/initialized" }); next(); continue; }
        if (typeof m.id === "number" && m.id >= 10) {
          out.push(m.error ? `ERROR ${JSON.stringify(m.error)}` : (m.result?.content ?? []).map((c: any) => c.text).join("\n"));
          next();
        }
      }
    });
    send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "e2e", version: "0" } } });
  });
}

test.describe("J26 Review over MCP", () => {
  test.beforeAll(() => {
    test.skip(!SHOP, "no KEEL_E2E_SHOP");
    git("checkout", "-q", "-B", "review/mcp", "main");
    writeFileSync(join(SHOP, "shop/coupon.py"), [
      '"""Coupons of the shop."""',
      "",
      "def coupon_value(price: int, code: str) -> int:",
      "    return price // 10 if code == \"TEN\" else 0",
      "",
      "",
      "def total(price: int, code: str) -> int:",
      "    return price - coupon_value(price, code)",
      "",
    ].join("\n"));
    git("add", "-A");
    git("commit", "-q", "-m", "feat: coupons");
  });
  test.afterAll(() => {
    if (SHOP) git("checkout", "-q", "main");
  });

  test("J26 keel2 mcp reads a branch review and acts on it like the Review page", async () => {
    test.setTimeout(4 * 60_000);
    const shop = await projectNamed("shop");
    await api("PUT", `/projects/${shop.id}/plugins/review`, { enabled: true, scope: "project" }, [200, 204]);
    const b = { project: "shop", branch: "review/mcp" };
    const draftId = (files: string) => (files.match(/^ {2}(dr_\w+) {2}shop\/coupon\.py:4 /m) ?? [])[1] ?? "none";
    const r = await session(["--write"], [
      () => ({ name: "keel_review_comment", arguments: { ...b, path: "shop/coupon.py", line: 4, body: "Magic number" } }),
      () => ({ name: "keel_review_files", arguments: b }),
      (p) => ({ name: "keel_review_draft", arguments: { project: "shop", draft_id: draftId(p[1]), body: "Name the 10" } }),
      () => ({ name: "keel_review_viewed", arguments: { ...b, path: "shop/coupon.py" } }),
      () => ({ name: "keel_review_files", arguments: b }),
      () => ({ name: "keel_review_conversation", arguments: b }),
      () => ({ name: "keel_review_file", arguments: { ...b, path: "shop/coupon.py", start_line: 3, end_line: 4 } }),
      () => ({ name: "keel_review_symbol", arguments: { ...b, symbol: "coupon_value" } }),
      () => ({ name: "keel_review_symbol", arguments: { ...b, symbol: "coupon_value", kind: "usages" } }),
      () => ({ name: "keel_review_diagrams", arguments: b }),
      () => ({ name: "keel_review_threads", arguments: b }),
      (p) => ({ name: "keel_review_draft", arguments: { project: "shop", draft_id: draftId(p[1]), delete: true } }),
      () => ({ name: "keel_review_files", arguments: b }),
    ]);
    const [added, files, edited, viewed, after, conv, file, def, uses, diagrams, threads, deleted, last] = r;
    expect(added).toMatch(/^Draft dr_\w+ on shop\/coupon\.py:4 saved in keel/);
    expect(files).toMatch(/^Branch review\/mcp: .* · review key branch:review\/mcp/);
    expect(files).toContain("  A  shop/coupon.py  +8 −0");
    expect(draftId(files)).not.toBe("none");
    expect(edited).toBe(`Draft ${draftId(files)} on shop/coupon.py:4 now reads: Name the 10`);
    expect(viewed).toMatch(/^shop\/coupon\.py is viewed · 1 file\(s\) viewed in branch review\/mcp\.$/);
    expect(after).toContain("  A  shop/coupon.py  +8 −0  (viewed)");
    expect(after).toContain("  shop/coupon.py:4 RIGHT  Name the 10");
    expect(conv).toMatch(/^Branch review\/mcp: /);
    expect(conv).toContain("feat: coupons");
    expect(file.split("\n")).toEqual([
      expect.stringMatching(/^shop\/coupon\.py at head \(.*\) · lines 3-4 of \d+$/),
      "3  def coupon_value(price: int, code: str) -> int:",
      '4      return price // 10 if code == "TEN" else 0',
    ]);
    expect(def).toMatch(/^Definition of coupon_value at .*: 1\n {2}shop\/coupon\.py:3 {2}def coupon_value.* · declaration · changed here$/);
    expect(uses).toContain("shop/coupon.py:8");
    expect(diagrams).toMatch(/^Diagrams of branch review\/mcp/);
    expect(diagrams).toContain("--- database");
    expect(threads).toBe("keel: A branch has no threads: name its pull request (pr).");
    expect(deleted).toBe(`Draft ${draftId(files)} deleted.`);
    expect(last).not.toContain("draft comment(s)");
    // the page reads the same records: the viewed mark is in keel, the draft is gone
    const view = (await api("GET", `/projects/${shop.id}/review/open?ref=review/mcp`)) as any;
    expect(view.viewed).toEqual(["shop/coupon.py"]);
    expect(view.drafts).toEqual([]);
  });

  test("J26 keel2 mcp without --write has the review's reads only", async () => {
    const r = await new Promise<string[]>((resolve, reject) => {
      const p = spawn(KEEL2, ["mcp"], { stdio: ["pipe", "pipe", "pipe"], env: process.env });
      let buf = "";
      const timer = setTimeout(() => { p.kill(); reject(new Error("keel2 mcp timed out")); }, 60_000);
      p.stdout.on("data", (d) => {
        buf += d;
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (!line.startsWith("{")) continue;
          const m = JSON.parse(line);
          if (m.id === 1) {
            p.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\n");
            p.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }) + "\n");
          }
          if (m.id === 2) { clearTimeout(timer); p.kill(); resolve(m.result.tools.map((t: any) => t.name)); }
        }
      });
      p.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "e2e", version: "0" } } }) + "\n");
    });
    const review = r.filter((n) => n.startsWith("keel_review_"));
    expect(review).toEqual(expect.arrayContaining(["keel_review_threads", "keel_review_thread", "keel_review_conversation", "keel_review_file",
      "keel_review_symbol", "keel_review_diagrams"]));
    expect(review.filter((n) => /comment|draft|viewed|check|merge|address|checkout|finding\b/.test(n))).toEqual([]);
  });
});
