// The web half of the drafts e2e (e2e/drafts/e2e.py): an unsent answer to a waiting question, in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/drafts/walk.mjs <url> <outdir> <other project id> <project name> <other project name> <thread>
//
// On the Flow page of the waiting clarify flow it picks an option that is not the recommended one and writes a note,
// then switches to the other project and back, opens the Inbox and comes back, reloads the page: each time the option
// is still picked, the note still there and "Draft kept" shows. Then it sends the answer: the next question shows with
// nothing filled in and no draft is left in localStorage. It prints one JSON object {checks: [{what, ok, detail}]} and
// saves a screenshot of each stage into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, otherId, projectName, otherName, tid] = process.argv.slice(2);
if (!tid) {
  console.error("usage: npx -y -p playwright@1 node e2e/drafts/walk.mjs <url> <outdir> <other project id> <project name> <other project name> <thread>");
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

/** playwright from `npx -p playwright@1`: npx puts its node_modules/.bin on PATH (an ES module ignores NODE_PATH). */
function loadPlaywright() {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir.endsWith(path.join("node_modules", ".bin"))) continue;
    try {
      return createRequire(path.join(dir, "..", "_.js"))("playwright");
    } catch {
      /* the next one */
    }
  }
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/drafts/walk.mjs …");
}

/** Chromium: playwright's own, else the newest headless shell in the browser cache (as e2e/core/walk.mjs). */
async function launch(chromium) {
  try {
    return await chromium.launch();
  } catch (e) {
    const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, path.join(os.homedir(), "Library/Caches/ms-playwright"),
      path.join(os.homedir(), ".cache/ms-playwright")].filter(Boolean);
    for (const root of roots) {
      if (!fs.existsSync(root)) continue;
      const shells = fs.readdirSync(root).filter((d) => d.startsWith("chromium_headless_shell-"))
        .sort((x, y) => Number(y.split("-")[1]) - Number(x.split("-")[1]));
      for (const d of shells) {
        for (const sub of fs.readdirSync(path.join(root, d))) {
          // the newer cache names it chrome-headless-shell, an older one headless_shell (chrome-linux/)
          for (const name of ["chrome-headless-shell", "headless_shell"]) {
            const exe = path.join(root, d, sub, name);
            if (fs.existsSync(exe)) return chromium.launch({ executablePath: exe });
          }
        }
      }
    }
    throw e;
  }
}

const NOTE = "Keep the file small: only the name and the price.";
const WHY = "Anything else the explorer should know (optional)";
const checks = [];
const errors = [];
let picked = "";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (what, ok, detail = "") => checks.push({ what, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
const shot = (page, name) => page.screenshot({ path: path.join(outDir, name), fullPage: false }).catch(() => undefined);

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, colorScheme: "light", reducedMotion: "reduce" });
const page = await ctx.newPage();
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text().replaceAll(base, "").slice(0, 300));
});
page.on("pageerror", (e) => errors.push("page error: " + String(e.message).slice(0, 300)));
// a failed request by its path: the console's "Failed to load resource" line does not name it
const failed = [];
page.on("response", (r) => {
  if (r.status() >= 400) failed.push(`${r.status()} ${new URL(r.url()).pathname}`);
});

/** The menu's project switcher: open it, choose [name] (nothing when it is chosen already). */
async function switchTo(name) {
  const btn = page.getByRole("button", { name: /^Project: / });
  if (new RegExp(`^Project: ${name}(,|$)`).test((await btn.getAttribute("aria-label")) ?? (await btn.innerText()))) return;
  await btn.click();
  await page.getByRole("listbox", { name: "Projects" }).getByRole("option", { name, exact: true }).click();
  await page.getByRole("button", { name: new RegExp(`^Project: ${name}`) }).waitFor({ timeout: 15_000 });
}

try {
  // keel's api answers a signed-in browser only: e2e/keelauth.py hands a one-time link
  if (process.env.KEEL_SIGNIN) await page.goto(process.env.KEEL_SIGNIN);
  await page.goto(base + "/#/flow");
  // the project: picked with the menu's switcher, as a person does (keel shares the pick with its other windows)
  await page.getByRole("button", { name: /^Project: / }).waitFor({ timeout: 30_000 });
  await switchTo(projectName);
  await page.evaluate((t) => (location.hash = `#/flow/${t}`), tid);
  const gate = page.getByRole("region", { name: "Gate waits for you" });
  await gate.waitFor({ timeout: 60_000 });
  const title = (await gate.locator("h3").innerText()).trim();

  // 1. pick an option that is not the recommended one (that one comes first), write a note
  const radios = gate.getByRole("radio");
  const option = radios.nth(1);
  const label = (await option.locator("b").innerText()).trim();
  picked = label;
  await option.click();
  await gate.getByLabel(WHY).fill(NOTE);
  await wait(300);
  check(`picked “${label}” and wrote a note at “${title.replace(/^◆ waits for you\s*/, "")}”`,
    (await option.getAttribute("aria-checked")) === "true" && (await gate.getByLabel(WHY).inputValue()) === NOTE);
  check("no “draft kept” while the person is typing", !(await gate.getByTestId("draft-kept").count()));
  await gate.getByLabel(WHY).scrollIntoViewIfNeeded().catch(() => undefined);
  await shot(page, "01-filled.png");

  const restored = async (where, scope) => {
    const opt = scope.getByRole("radio", { name: new RegExp("^" + label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });
    const picked = (await opt.first().getAttribute("aria-checked")) === "true";
    const note = await scope.getByLabel(WHY).inputValue();
    const hint = await scope.getByTestId("draft-kept").isVisible().catch(() => false);
    // the screenshot shows the note and the hint (the end of the card)
    await scope.getByLabel(WHY).scrollIntoViewIfNeeded().catch(() => undefined);
    check(`${where}: the option is still picked, the note still there, “Draft kept” shows`, picked && note === NOTE && hint,
      `picked ${picked}, note “${note}”, hint ${hint}`);
  };

  // 2. another project, then back (the menu's project switcher)
  await switchTo(otherName);
  // the other project's own Flow page (the link to this flow's id is not a flow of that project)
  await page.evaluate(() => (location.hash = "#/flow"));
  await page.getByText("Nothing running here yet").first().waitFor({ timeout: 30_000 });
  check(`switched to the other project (${otherName}): the gate is gone`, !(await gate.count()));
  await shot(page, "02-other-project.png");
  await switchTo(projectName);
  await page.evaluate((t) => (location.hash = `#/flow/${t}`), tid);
  await gate.waitFor({ timeout: 30_000 });
  await restored(`back on ${projectName}`, gate);
  await shot(page, "03-back-restored.png");

  // 3. the Inbox shows the same draft; back on the Flow page it is still there
  await page.evaluate(() => (location.hash = "#/inbox"));
  const card = page.locator('[data-testid="inbox-item"]').filter({ hasText: title.replace(/^◆ waits for you\s*/, "") }).first();
  await card.waitFor({ timeout: 30_000 });
  await restored("in the Inbox", card);
  await shot(page, "04-inbox.png");
  await page.evaluate((t) => (location.hash = `#/flow/${t}`), tid);
  await gate.waitFor({ timeout: 30_000 });
  await restored("on the Flow page after the Inbox", gate);

  // 4. a reload of the page
  await page.reload();
  await gate.waitFor({ timeout: 30_000 });
  await restored("after a reload", gate);
  await shot(page, "05-after-reload.png");

  // 5. send: the next question is empty and nothing is kept
  await gate.locator("#approve").click();
  await page.waitForFunction((old) => {
    const h = document.querySelector('[aria-label="Gate waits for you"] h3');
    return h && !h.textContent.includes(old);
  }, title.replace(/^◆ waits for you\s*/, ""), { timeout: 120_000 });
  await wait(500);
  const next = (await gate.locator("h3").innerText()).trim();
  const left = await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("keel2.answer.")));
  const note = await gate.getByLabel(WHY).inputValue().catch(() => "");
  const hint = await gate.getByTestId("draft-kept").count();
  check(`sent: the next question (“${next.replace(/^◆ waits for you\s*/, "")}”) shows with nothing filled in and no hint`, !note && !hint,
    `note “${note}”, hint ${hint}`);
  check("sent: no draft is left in localStorage", left.length === 0, left.join(", "));
  await shot(page, "06-sent-next-question.png");
  // the switch to the other project happens on this flow's link: that project has no such flow (a 404, said on the page)
  const expected = (f) => f === `404 /api/projects/${otherId}/flows/${tid}`;
  const unexpected = failed.filter((f) => !expected(f));
  const noise = errors.filter((e) => !/Failed to load resource: .*404/.test(e) || !failed.some(expected));
  check("no console error and no failed request on the way (but the other project's 404 for this flow's link)",
    !noise.length && !unexpected.length, [...noise, ...unexpected].join(" | "));
} catch (e) {
  check("the walk ran to its end", false, e instanceof Error ? e.stack || e.message : String(e));
  await shot(page, "99-failed.png");
}
await browser.close();
console.log(JSON.stringify({ checks, errors, failed, picked }));
