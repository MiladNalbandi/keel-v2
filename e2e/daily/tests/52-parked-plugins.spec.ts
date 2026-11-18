// J25 (0.38, #196) keel's focus: the parked plugins are in every keel but off until a person turns them on. A fresh keel
// shows only the core in its menu; the parked ones are Off on their card in Add features; one click and a restart turn
// one on (Goals here), and its page is back.
import { expect, test, type Page } from "@playwright/test";
import { api, signIn, until, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";
const PARKED = ["org", "goals", "answers", "desk", "map", "record", "slack", "notify", "product"];
const CORE_KEEL_WIDE = ["keelbot", "tasks", "code", "graph", "wiki", "jira", "github-issues"];

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: false });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

type Installed = { name: string; on?: boolean; status?: string };

test.describe("J25 Parked plugins", () => {
  test("J25 parked plugins start off, the core is on, and one click and a restart turn one on", async ({ page }) => {
    test.setTimeout(6 * 60_000);
    const errors = watchConsole(page);
    const list = async () => (await api("GET", "/plugins/installed")) as Installed[] | { plugins: Installed[] };
    const by = async () => {
      const l = await list();
      return Object.fromEntries((Array.isArray(l) ? l : l.plugins).map((p) => [p.name, p]));
    };
    const first = await by();
    for (const n of PARKED) expect(first[n]?.on, `${n} is parked: off`).toBe(false);
    for (const n of CORE_KEEL_WIDE) expect(first[n]?.on, `${n} is core: on`).not.toBe(false);

    await signIn(page);
    await page.goto("/#/");
    await expect(page.locator("body")).toContainText(/KeelBot/, { timeout: 30_000 });
    for (const href of ["#/org", "#/goals", "#/desk", "#/answers", "#/map"]) {
      await expect(page.locator(`a[href="${href}"]`), `no menu link to ${href}`).toHaveCount(0);
    }
    await shot(page, "j25-core-menu");

    await page.goto("/#/features");
    const goals = page.getByRole("group", { name: /^Goals.* for this keel/i });
    await expect(goals).toBeVisible({ timeout: 30_000 });
    await expect(goals.getByRole("button", { name: "Off" })).toHaveAttribute("aria-pressed", "true");
    await shot(page, "j25-features-parked-off");
    await goals.getByRole("button", { name: "On" }).click();
    await expect(goals.getByRole("button", { name: "On" })).toHaveAttribute("aria-pressed", "true", { timeout: 60_000 });
    await page.getByRole("button", { name: "Restart keel now" }).first().click();
    await until("Goals loaded after the restart", async () => (await by()).goals?.status === "loaded", 180_000, 3000);

    await page.goto("/#/goals");
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/Goals/i, { timeout: 60_000 });
    await shot(page, "j25-goals-on");
    const after = await by();
    expect(after.org?.on).toBe(false);           // only the one turned on changed
    await expectNoConsoleErrors(errors.filter((e) => !/Failed to fetch|NetworkError|ERR_CONNECTION|502|503/.test(e)));
  });
});
