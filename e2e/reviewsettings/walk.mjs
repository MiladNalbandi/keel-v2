// The web half of the Settings › Review e2e (e2e/reviewsettings/e2e.py), in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/reviewsettings/walk.mjs <url> <outdir> <project name> <review key>
//
// Settings › Review: keel's two reviewers; pick three; add the lens "Money rounding" on reviewer C. Then the Review
// page of the branch: the text above Find problems names the three reviewers. It prints one JSON object
// {checks: [{what, ok, detail}]} and saves a screenshot of each stage into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, projectName, reviewKey] = process.argv.slice(2);
if (!reviewKey) {
  console.error("usage: npx -y -p playwright@1 node e2e/reviewsettings/walk.mjs <url> <outdir> <project name> <review key>");
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
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/reviewsettings/walk.mjs …");
}

/** Chromium: playwright's own, else the newest headless shell in the browser cache (as e2e/drafts/walk.mjs). */
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

const THREE = "Three keel reviewers read the change (A: correctness and security, B: tests, the description and design, C: your own reviewer).";
const checks = [];
const errors = [];
const check = (what, ok, detail = "") => checks.push({ what, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
const shot = (page, name, full = true) => page.screenshot({ path: path.join(outDir, name), fullPage: full }).catch(() => undefined);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, colorScheme: "dark", reducedMotion: "reduce" });
const page = await ctx.newPage();
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text().replaceAll(base, "").slice(0, 300));
});
page.on("pageerror", (e) => errors.push("page error: " + String(e.message).slice(0, 300)));
const failed = [];
page.on("response", (r) => {
  if (r.status() >= 400) failed.push(`${r.status()} ${new URL(r.url()).pathname}`);
});

try {
  if (process.env.KEEL_SIGNIN) await page.goto(process.env.KEEL_SIGNIN);
  await page.goto(base + "/#/flow");
  const btn = page.getByRole("button", { name: /^Project: / });
  await btn.waitFor({ timeout: 30_000 });
  if (!new RegExp(`^Project: ${projectName}`).test((await btn.getAttribute("aria-label")) ?? (await btn.innerText()))) {
    await btn.click();
    await page.getByRole("listbox", { name: "Projects" }).getByRole("option", { name: projectName, exact: true }).click();
  }

  // 0. (0.18.68) How a review runs: the review flow at Standard, the depth cards, Your standards; pick Full, then Classic
  await page.evaluate(() => (location.hash = "#/settings/review"));
  await page.getByRole("heading", { level: 1, name: "Review" }).waitFor({ timeout: 30_000 });
  const flowRadio = page.getByRole("radio", { name: /The review flow/ });
  await flowRadio.waitFor({ timeout: 30_000 });
  check("Find problems runs the review flow by default", await flowRadio.isChecked());
  const depth = page.getByRole("radiogroup", { name: "Review depth" });
  check("the depth cards say Standard, with what each costs",
    (await depth.getByRole("radio", { name: /Standard/ }).getAttribute("aria-checked")) === "true" &&
      /~20k tokens/.test(await depth.getByRole("radio", { name: /Quick/ }).innerText()), await depth.innerText());
  const std = page.getByRole("group", { name: "Your standards" });
  check("Your standards lists what every reviewer reads first", /Rules/.test(await std.innerText()), await std.innerText());
  check("the Review page's text is the review flow's",
    (await page.getByTestId("rv-find-text").innerText()).startsWith("The review flow (Standard"), await page.getByTestId("rv-find-text").innerText());
  check("the classic reviewers are hidden while the flow runs",
    (await page.getByRole("radiogroup", { name: "Number of reviewers" }).count()) === 0);
  await shot(page, "00-how-a-review-runs.png");
  await depth.getByRole("radio", { name: /Full/ }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="Review depth"] [aria-checked="true"]')?.textContent?.includes("Full"),
    null, { timeout: 15_000 });
  check("Full is picked", true);
  await page.getByRole("radio", { name: /Classic/ }).click();

  // 1. Settings › Review: keel's two reviewers (classic)
  const count = page.getByRole("radiogroup", { name: "Number of reviewers" });
  await count.waitFor({ timeout: 30_000 });
  check("Settings › Review opens with keel's two reviewers",
    (await count.getByRole("radio", { name: "2" }).getAttribute("aria-checked")) === "true");
  const text = page.getByTestId("rv-find-text");
  check("the Review page's text says two reviewers", (await text.innerText()).startsWith("Two keel reviewers read the change (A: correctness and security"),
    await text.innerText());
  const final = await page.getByTestId("rv-final-line").innerText();
  check(`it says which model the last review uses: “${final}”`, /the final review uses/.test(final), final);
  await shot(page, "01-settings-review-default.png");

  // 2. three reviewers
  await count.getByRole("radio", { name: "3" }).click();
  await page.getByRole("group", { name: "Reviewer C" }).waitFor({ timeout: 15_000 });
  await page.waitForFunction(() => document.querySelector('[data-testid="rv-find-text"]')?.textContent?.startsWith("Three"), null, { timeout: 15_000 });
  check("three reviewers: a card for C, and the text says three", (await text.innerText()).startsWith(THREE), await text.innerText());
  await page.getByRole("group", { name: "Reviewer C" }).scrollIntoViewIfNeeded();
  await shot(page, "02-three-reviewers.png", false);

  // 3. a new lens on reviewer C
  await page.getByRole("button", { name: "+ New lens" }).click();
  const form = page.getByRole("region", { name: "New lens" });
  await form.getByLabel("Name").fill("Money rounding");
  await form.getByLabel("Reviewer").selectOption("C");
  await form.getByLabel(/Checklist/).fill("- amounts are integers in cents, never floats\n- an order total equals the sum of its lines");
  check("the form says where the lens goes", (await form.innerText()).includes(".keel/lenses/money-rounding.md"), await form.innerText());
  await form.scrollIntoViewIfNeeded();
  await shot(page, "03-new-lens-form.png", false);
  await form.getByRole("button", { name: "Save lens" }).click();
  const row = page.getByRole("row").filter({ hasText: "Money rounding" });
  await row.waitFor({ timeout: 15_000 });
  check("the lens is in the table, from .keel/lenses, on reviewer C",
    (await row.innerText()).includes(".keel/lenses/money-rounding.md") &&
      (await row.getByRole("combobox").inputValue()) === "2", await row.innerText());
  await row.scrollIntoViewIfNeeded();
  await shot(page, "04-lens-in-table.png", false);
  await row.getByRole("button", { name: "Edit" }).click();
  const file = page.getByRole("region", { name: /Lens file/ });
  await file.waitFor({ timeout: 15_000 });
  check("Edit opens the project's lens file", (await file.getByLabel("Lens text").inputValue()).startsWith("# Money rounding"));
  await shot(page, "05-settings-review-full.png");

  // 4. the Review page of the branch: the text above Find problems
  await page.evaluate((k) => (location.hash = `#/review/${k}`), reviewKey);
  const problems = page.getByRole("tab", { name: /^Problems/ });
  await problems.waitFor({ timeout: 60_000 });
  await problems.click();
  const said = page.getByText(/keel reviewers read the change/).first();
  await said.waitFor({ timeout: 30_000 });
  // keel's state for the review (GET …/review/ai) comes after the page: until then the tab says keel's default text
  await page.waitForFunction((t) => [...document.querySelectorAll(".rvn-call span")].some((s) => s.textContent?.startsWith(t)),
    THREE.slice(0, 40), { timeout: 30_000 }).catch(() => undefined);
  check("the Review page says three reviewers above Find problems", (await said.innerText()).startsWith(THREE), await said.innerText());
  await shot(page, "06-review-page-find-problems.png", false);
  // 0.18.68 Review › Files: each file's folder next to its name
  await page.getByRole("tab", { name: /^Files/ }).click();
  const dir = page.getByRole("list", { name: "Changed files" }).getByTestId("rvn-fdir").first();
  await dir.waitFor({ timeout: 15_000 });
  check("Review › Files shows the file's folder", (await dir.innerText()).startsWith("shop/"), await dir.innerText());
  await shot(page, "07-files-folder.png", false);
  const noise = errors.filter((e) => !/Failed to load resource/.test(e));
  check("no console error and no failed request on the way", !noise.length && !failed.length, [...noise, ...failed].join(" | "));
} catch (e) {
  check("the walk ran to its end", false, e instanceof Error ? e.stack || e.message : String(e));
  await shot(page, "99-failed.png");
}
await wait(100);
await browser.close();
console.log(JSON.stringify({ checks, errors, failed }));
