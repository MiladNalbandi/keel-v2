// J20 (0.34) a task handed to the Org: the task card's flows in groups (keel's pick, 3 of its group, More flows), Hand to
// Org, the task following the mission (In progress → In review while the lead's report waits → Done), and a task for
// each part the lead gave out. The fake model plays the lead and the team; the SAFE driver answers their gates.
import { expect, test, type Page } from "@playwright/test";
import { api, driveFlow, note, projectNamed, raw, signIn, until, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";
type Node = { id: string; role: string; stage: string; status: string; thread_id: string | null; children: Node[];
  waiting?: { step: string; title: string; person: boolean } | null };

const nodes = (n: Node): Node[] => [n, ...n.children.flatMap(nodes)];

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe("J20 Task to the Org", () => {
  test("J20 flows in groups, Hand to Org, the task follows the mission and its parts become tasks", async ({ page }, info) => {
    test.setTimeout(25 * 60_000);
    const errors = watchConsole(page);
    await useFakeModel();
    await signIn(page);
    const shop = await projectNamed("shop");
    const tpl = await api("GET", "/org/templates/small-team");
    await api("PUT", "/org/orgs/small-team", { yaml: tpl.yaml });
    const task = await api("POST", `/projects/${shop.id}/tasks`, {
      title: "Show the cart count", type: "story",
      description: "Show how many items are in the cart, next to the cart icon.",
    });

    // the card: keel's pick and three more of its group; More flows shows every flow under its group
    await page.goto(`/#/tasks/${task.id}`);
    const d = page.getByRole("dialog", { name: /Show the cart count/ });
    const s = d.getByRole("region", { name: "Start a flow" });
    await expect(s.getByTestId("suggested-flow")).toHaveText("feature flow", { timeout: 30_000 });
    await expect(s.getByRole("group", { name: "Pick a flow" }).getByRole("button")).toHaveText(
      ["feature (keel's pick)", "change", "quick", "address"]);
    await s.getByRole("button", { name: "More flows" }).click();
    const all = s.getByRole("group", { name: "Pick another flow" });
    await expect(all.getByRole("heading", { level: 4 })).toHaveText(["Build", "Fix", "Check", "Plan", "Ship & set up"]);
    await expect(all).not.toContainText("lead a mission");
    await expect(all).not.toContainText("review the team's work");
    await shot(page, "j11-flows-in-groups");
    await s.getByRole("button", { name: "Fewer flows" }).click();

    // Hand to Org
    await s.getByRole("button", { name: "Hand to Org" }).click();
    const form = s.getByRole("group", { name: "Hand to Org" });
    await expect(form.getByLabel("Org", { exact: true })).toHaveValue("small-team");
    await shot(page, "j11-hand-to-org");
    await form.getByRole("button", { name: "Give to Small team" }).click();
    const m = d.getByRole("region", { name: "Org mission" });
    await expect(m).toContainText("Org small-team", { timeout: 30_000 });
    await expect(d.getByText("In progress", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    const t1 = await api("GET", `/tasks/${task.id}`);
    const mid = t1.mission.id as string;
    note(info, `mission ${mid}`);

    // drive the lead's plan and the team's flows; stop when the lead's report waits for the person
    let reportTid = "";
    const end = Date.now() + 20 * 60_000;
    while (Date.now() < end && !reportTid) {
      const tree = (await api("GET", `/org/missions/${mid}`)).mission as Node;
      if (["failed", "stopped"].includes(tree.status)) throw new Error(`the mission ${tree.status}`);
      for (const n of nodes(tree)) {
        if (n.status !== "running" || !n.thread_id || !n.waiting) continue;
        if (n.id === mid && n.stage === "review") { reportTid = n.thread_id; break; }
        const r = await driveFlow(shop.id, n.thread_id, { maxDecisions: 3, waitMs: 4_000 });
        note(info, `${n.role}/${n.stage}: ${r.stoppedBecause} ${r.gates.join(" | ")}`);
      }
      await page.waitForTimeout(2_000);
    }
    expect(reportTid, "the lead's report waits for the person").not.toBe("");

    // the task is In review while the report waits; each part is a task of its own
    await page.reload();
    await expect(d.getByText("In review", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    await expect(m).toContainText("reported");
    await expect(m).toContainText("Its report waits for you");
    const parts = m.getByRole("list", { name: "Parts" }).getByRole("listitem");
    await expect(parts).toHaveCount(2);
    await expect(parts.nth(0)).toContainText("Show the cart count (fe)");
    await shot(page, "j11-in-review");
    const t2 = await api("GET", `/tasks/${task.id}`);
    for (const p of t2.parts) {
      const pt = await api("GET", `/tasks/${p.id}`);
      expect(pt.parent_task_id).toBe(task.id);
      expect(pt.mission.org).toBe("small-team");
    }

    // the person accepts the report: the task and its parts are Done
    const r = await raw("POST", `/api/threads/${reportTid}/resume`, { body: { decision: "approve", why: "Looks good.", payload: { choice: "accept" } } });
    expect(r.status, r.text).toBe(200);
    await until("the task done", async () => ((await api("GET", `/tasks/${task.id}`)).status === "done" ? true : null), 180_000);
    await page.reload();
    await expect(d.getByText("Done", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
    const done = await api("GET", `/tasks/${task.id}`);
    expect(done.parts.map((p: { status: string }) => p.status)).toEqual(["done", "done"]);
    await shot(page, "j11-done");
    await expectNoConsoleErrors(errors);
  });
});
