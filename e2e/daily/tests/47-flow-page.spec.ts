// J21 (0.34, #181) the Flow page's board: a gate step shows a small "waits for you" label above its own name (not "wait
// for you your answers"), the agent/model pill stays one line with a long model name on a narrow card, and "What keel
// applied" is a card. The fake model runs a feature flow to its first gate.
import { expect, test, type Page } from "@playwright/test";
import { api, driveFlow, note, projectNamed, signIn, startFlow, useFakeModel, watchConsole, expectNoConsoleErrors } from "../lib/keel";

const OUT = process.env.KEEL_E2E_OUT || "report";

async function shot(page: Page, name: string) {
  for (const [w, h, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test.describe("J21 Flow page board", () => {
  test("J21 gate label, one-line model pill, the applied card", async ({ page }, info) => {
    test.setTimeout(12 * 60_000);
    const errors = watchConsole(page);
    await useFakeModel();
    await signIn(page);
    const shop = await projectNamed("shop");
    const tid = await startFlow(shop.id, "feature", "Show the shipping weight");
    const r = await driveFlow(shop.id, tid, { stopAtStep: /spec/i, maxDecisions: 6, waitMs: 420_000 });
    note(info, `drive: ${r.stoppedBecause}; gates: ${r.gates.join(" | ")}`);

    await page.goto(`/#/flow/${tid}`);
    const board = page.locator("li.sb").first();
    await expect(board).toBeVisible({ timeout: 30_000 });

    // a gate: the label is its own small line, the name is the step's name; never the two run together
    const gate = page.locator("li.sb.k-gate").first();
    await expect(gate.locator(".sb-head.is-tag")).toHaveText(/waits for you/i);
    await expect(page.locator(".sb-name", { hasText: /wait for you your/i })).toHaveCount(0);
    const tag = await gate.locator(".sb-head.is-tag").boundingBox();
    const nameBox = await gate.locator(".sb-name").boundingBox();
    expect(tag && nameBox && tag.height < nameBox.height, "the label sits above the name").toBeTruthy();

    // the agent pill: a long model name stays on one line inside the card (cut with an ellipsis), never a bubble
    const pill = page.locator(".sb-agent").first();
    await expect(pill).toBeVisible();
    await pill.evaluate((el) => {
      let m = el.querySelector(".sb-model");
      if (!m) { m = document.createElement("span"); m.className = "sb-model"; el.appendChild(m); }
      m.textContent = "claude-opus-5.5 · medium · a-very-long-model-name-for-the-test";
    });
    for (const w of [1280, 390]) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.waitForTimeout(300);
      const pb = await pill.boundingBox();
      const card = await pill.locator("xpath=ancestor::li[contains(@class,'sb')][1]").boundingBox();
      expect(pb!.height, `one line at ${w}px`).toBeLessThan(26);
      expect(pb!.x + pb!.width, `inside the card at ${w}px`).toBeLessThanOrEqual(card!.x + card!.width + 1);
      const cut = await pill.locator(".sb-model").evaluate((el) => el.scrollWidth > el.clientWidth);
      expect(cut, `the model name is cut with an ellipsis at ${w}px`).toBeTruthy();
    }
    await page.setViewportSize({ width: 1280, height: 900 });

    // "What keel applied": a card with a border, not a bare bar
    const applied = page.locator("details.dh-applied");
    if (await applied.count()) {
      const style = await applied.evaluate((el) => { const s = getComputedStyle(el); return { b: s.borderTopStyle, r: s.borderTopLeftRadius }; });
      expect(style.b).not.toBe("none");
      expect(parseFloat(style.r)).toBeGreaterThan(0);
      await applied.locator("summary").click();
      await expect(applied.locator("ul li").first()).toBeVisible();
    } else {
      note(info, "the flow applied nothing yet: no What keel applied card");
    }
    await shot(page, "j21-flow-board");
    await api("POST", `/threads/${tid}/stop`, undefined, [200, 204, 409]).catch(() => undefined);
    await expectNoConsoleErrors(errors);
  });
});
