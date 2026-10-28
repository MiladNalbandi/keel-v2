// The web half of the review-base e2e (e2e/reviewbase/e2e.py), in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/reviewbase/walk.mjs <url> <outdir> <project name> <project id> review|base
//
// review: the Review page (#/review) reviews the branch the folder is on against github/main (1 file), with the warning
// that the local master is 3 behind and 2 ahead of github/main; "Compare with" master shows 4 files and says why; back
// on github/main; the classic Code › Review shows the same. base: base_branch names main, which has no local copy:
// Start a flow asks, "Use github/main" answers it; Settings › General › Base branch shows what keel compares with.
// It prints one JSON object {checks: [{what, ok, detail}]} and saves a screenshot of each stage into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, projectName, pid, stage] = process.argv.slice(2);
if (!stage) {
  console.error("usage: npx -y -p playwright@1 node e2e/reviewbase/walk.mjs <url> <outdir> <project name> <project id> review|base");
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
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/reviewbase/walk.mjs …");
}

/** Chromium: playwright's own, else the newest headless shell in the browser cache (as e2e/acgate/walk.mjs). */
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

const checks = [];
const errors = [];
const failed = [];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (what, ok, detail = "") => checks.push({ what, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
const shot = (page, name, opts = {}) => page.screenshot({ path: path.join(outDir, name), ...opts }).catch(() => undefined);

const { chromium } = loadPlaywright();
const browser = await launch(chromium);
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, colorScheme: "light", reducedMotion: "reduce" });
const page = await ctx.newPage();
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().replaceAll(base, "").slice(0, 300)); });
page.on("pageerror", (e) => errors.push("page error: " + String(e.message).slice(0, 300)));
page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${new URL(r.url()).pathname}`); });

/** A page of keel with the project picked (keel signs a browser in once). */
async function open(hash, width = 1600, height = 1000) {
  await page.setViewportSize({ width, height });
  if (process.env.KEEL_SIGNIN && !open.signedIn) {
    await page.goto(process.env.KEEL_SIGNIN);
    open.signedIn = true;
  }
  await page.goto(base + "/#/flow");
  await page.reload();
  const btn = page.getByRole("button", { name: /^Project: / });
  await btn.waitFor({ timeout: 30_000 });
  if (!new RegExp(`^Project: ${projectName}(,|$)`).test((await btn.getAttribute("aria-label")) ?? (await btn.innerText()))) {
    await btn.click();
    await page.getByRole("listbox", { name: "Projects" }).getByRole("option", { name: projectName, exact: true }).click();
  }
  await page.evaluate((h) => (location.hash = h), hash);
  await wait(500);
}

async function review() {
  // 1. the Review page: this branch, against github/main
  await open("#/review");
  const side = page.getByRole("region", { name: "Review" }).first();
  await side.waitFor({ timeout: 30_000 });
  const bw = side.getByTestId("branch-base-warning");
  await bw.waitFor({ timeout: 30_000 });
  const said = (await bw.innerText()).trim();
  check(`the list says it: ${said}`, said.includes("3 commits behind and 2 ahead of github/main"), said);
  await side.getByRole("button", { name: "Review it" }).click();
  const one = page.getByRole("region", { name: /^Review of / }).first();
  await one.waitFor({ timeout: 30_000 });
  const warn = one.getByTestId("review-base-warning");
  await warn.waitFor({ timeout: 30_000 });
  const text = (await warn.innerText()).trim();
  check(`the branch review warns: ${text}`,
    text === "Your local master is 3 commits behind and 2 ahead of github/main: this review compares against github/main.", text);
  const meta = (await one.locator(".rvn-prmeta").innerText()).replace(/\s+/g, " ");
  check(`it compares against github/main, 1 file: ${meta}`, /into github\/main/.test(meta) && /\b1 file\b/.test(meta), meta);
  const pick = one.getByRole("combobox", { name: "Compare with" });
  check("Compare with offers github/main and master", (await pick.locator("option").allInnerTexts()).join("|").includes("master"),
    (await pick.locator("option").allInnerTexts()).join("|"));
  await shot(page, "01-review-remote.png");
  // 2. the local master instead: the other change's files show, and the warning says why
  await pick.selectOption("local");
  await one.getByText("compares against your local master").waitFor({ timeout: 30_000 });
  const meta2 = (await one.locator(".rvn-prmeta").innerText()).replace(/\s+/g, " ");
  check(`Compare with master: 4 files against master: ${meta2}`, /into master/.test(meta2) && /\b4 files\b/.test(meta2), meta2);
  await shot(page, "02-review-local.png");
  await pick.selectOption("remote");
  await one.getByText("this review compares against github/main.").waitFor({ timeout: 30_000 });
  const meta3 = (await one.locator(".rvn-prmeta").innerText()).replace(/\s+/g, " ");
  check(`back on github/main: 1 file again (${meta3})`, /\b1 file\b/.test(meta3), meta3);
  // 3. the classic Code › Review
  await open("#/repo");
  await page.getByRole("button", { name: "Review", exact: true }).click();
  const classic = page.getByRole("region", { name: "Review" }).first();
  await classic.waitFor({ timeout: 30_000 });
  const cw = classic.getByTestId("branch-base-warning");
  await cw.waitFor({ timeout: 30_000 });
  await classic.getByRole("button", { name: "Review this branch" }).click();
  const cwarn = classic.getByTestId("review-base-warning");
  await cwarn.waitFor({ timeout: 30_000 });
  check(`the classic view warns too: ${(await cwarn.innerText()).trim()}`, (await cwarn.innerText()).includes("compares against github/main"));
  await shot(page, "03-classic-review.png");
  // 4. 390 px: the warning wraps, nothing scrolls the page sideways
  await open("#/review", 390, 844);
  const phone = page.getByRole("region", { name: "Review" }).first();
  await phone.getByTestId("branch-base-warning").waitFor({ timeout: 30_000 });
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(`390 px: the page does not scroll sideways (${over} px over)`, over <= 1, over);
  await shot(page, "04-review-390.png");
}

async function baseStage() {
  // 5. Start a flow asks which base, the answer goes to .keel/config.yml
  await open("#/flow");
  await page.getByRole("button", { name: "Start a flow" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Start a flow" });
  await dialog.waitFor({ timeout: 30_000 });
  const q = dialog.getByTestId("base-missing");
  await q.waitFor({ timeout: 30_000 });
  const qt = (await q.innerText()).replace(/\s+/g, " ");
  check(`Start a flow asks: ${qt.slice(0, 160)}`, qt.includes("no local branch main") && qt.includes("Use github/main"), qt);
  await q.scrollIntoViewIfNeeded();
  await shot(page, "05-start-asks.png");
  await q.getByRole("button", { name: "Use github/main" }).click();
  await q.waitFor({ state: "detached", timeout: 30_000 });
  check("the answer is taken and the question goes", true);
  await shot(page, "06-start-answered.png");
  // 6. Settings › General › Base branch
  await open(`#/settings/general/${pid}`);
  const sec = page.getByTestId("sec-base");
  await sec.waitFor({ timeout: 30_000 });
  if (!(await sec.getByTestId("base-panel").isVisible().catch(() => false))) await sec.getByRole("button", { name: /Base branch/ }).first().click();
  const ref = sec.getByTestId("base-ref");
  await ref.waitFor({ timeout: 30_000 });
  check(`Settings › General › Base branch: keel compares with ${(await ref.innerText()).trim()}`, (await ref.innerText()).trim() === "github/main",
    await sec.innerText());
  const remote = sec.getByRole("combobox", { name: "Canonical remote" });
  check(`the canonical remote is github: ${await remote.inputValue()}`, (await remote.inputValue()) === "github");
  await sec.scrollIntoViewIfNeeded();
  await shot(page, "07-settings-base.png");
}

try {
  if (stage === "review") await review();
  else await baseStage();
  check("no console error and no failed request on the way", !errors.length && !failed.filter((f) => !/\/api\/web\/code/.test(f)).length,
    [...errors, ...failed].join(" | "));
} catch (e) {
  check("the walk ran to its end", false, e instanceof Error ? e.stack || e.message : String(e));
}
await browser.close();
console.log(JSON.stringify({ checks, errors, failed }));
