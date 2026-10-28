// The web half of the AC gate e2e (e2e/acgate/e2e.py): the criterion's code in the gate card, in headless Chromium.
//
//   npx -y -p playwright@1 node e2e/acgate/walk.mjs <url> <outdir> <project name> <thread>
//
// On the Flow page of the flow waiting at its AC gate (1600 px wide): the section "The code of this criterion" shows
// its summary, the RED / GREEN / Both tabs switch (aria-selected), a diff shows with its added lines on a coloured
// background, and the Steps column (the Blocks view) is centered: its left and right margins are about the same. At
// 390 px nothing scrolls the page sideways. It prints one JSON object {checks: [{what, ok, detail}]} and saves a
// screenshot of each stage into <outdir>.

import fs from "fs";
import { createRequire } from "module";
import os from "os";
import path from "path";

const [base, outDir, projectName, tid] = process.argv.slice(2);
if (!tid) {
  console.error("usage: npx -y -p playwright@1 node e2e/acgate/walk.mjs <url> <outdir> <project name> <thread>");
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
  throw new Error("playwright not found: run me with npx -y -p playwright@1 node e2e/acgate/walk.mjs …");
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

/** The Flow page of the waiting flow at this size and colour scheme (one page: keel signs a browser in once). */
async function open(width, height, scheme = "light") {
  await page.setViewportSize({ width, height });
  await page.emulateMedia({ colorScheme: scheme });
  // keel's api answers a signed-in browser only: e2e/keelauth.py hands a one-time link
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
  await page.evaluate((t) => (location.hash = `#/flow/${t}`), tid);
  const gate = page.getByRole("region", { name: "Gate waits for you" });
  await gate.waitFor({ timeout: 60_000 });
  return { page, gate };
}

try {
  // 1. 1600 px: the code section, its tabs and a coloured diff
  const { gate } = await open(1600, 1000);
  const code = gate.getByRole("region", { name: "The code of this criterion" });
  await code.waitFor({ timeout: 30_000 });
  const sum = (await code.getByTestId("acc-sum").innerText()).trim();
  check(`the gate shows “The code of this criterion”: ${sum}`, /\d+ commits? · \d+ files? · \+\d+ −\d+/.test(sum), sum);
  const tabs = code.getByRole("tab");
  const names = await tabs.allInnerTexts();
  check(`three tabs: ${names.join(" | ")}`, names.length === 3 && /^RED · /.test(names[0]) && /^GREEN · /.test(names[1]) && names[2] === "Both",
    names.join(" | "));
  await code.scrollIntoViewIfNeeded();
  await shot(page, "01-gate-red.png");
  const selected = async () => (await tabs.evaluateAll((els) => els.map((e) => e.getAttribute("aria-selected")))).join(",");
  const firstEnabled = (await tabs.evaluateAll((els) => els.findIndex((e) => !e.disabled)));
  check("the first tab with a commit is selected", (await selected()).split(",")[firstEnabled] === "true", await selected());

  const rows = code.locator(".acc-panel .dl");
  const added = code.locator(".acc-panel .dl.add");
  check(`a diff shows: ${await rows.count()} rows, ${await added.count()} added`, (await added.count()) > 0, await code.innerText());
  const bg = await added.first().evaluate((el) => getComputedStyle(el).backgroundColor);
  check(`an added line has a coloured background (${bg})`, bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent", bg);
  const hunkColor = await code.locator(".acc-panel .dl.hunk").first().evaluate((el) => getComputedStyle(el).color).catch(() => "");
  check(`hunk headers are dimmed (${hunkColor || "none"})`, !!hunkColor, "no hunk row");

  for (const [i, name] of [[1, "GREEN"], [2, "Both"], [0, "RED"]]) {
    if (await tabs.nth(i).isDisabled()) continue;
    await tabs.nth(i).click();
    await wait(200);
    const sel = (await selected()).split(",");
    check(`${name} tab: selected after a click`, sel[i] === "true" && sel.filter((x) => x === "true").length === 1, sel.join(","));
    if (name === "GREEN") await shot(page, "02-gate-green.png");
    if (name === "Both") {
      const groups = await code.locator(".acc-group").count();
      check(`Both shows the commits one after the other (${groups} group(s))`, groups >= 1, await code.innerText());
      await shot(page, "03-gate-both.png");
    }
  }
  // the box scrolls sideways inside, never the page
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(`1600 px: the page does not scroll sideways (${wide} px over)`, wide <= 1, wide);

  // 2. the Steps column (Blocks view) is centered with a max width
  const sx = page.locator(".fl-scroll.is-blocks > .sx").first();
  await sx.waitFor({ timeout: 15_000 });
  const m = await sx.evaluate((el) => {
    const p = el.parentElement;
    const cs = getComputedStyle(p);
    const pr = p.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const inL = pr.left + parseFloat(cs.paddingLeft);
    const inR = pr.right - parseFloat(cs.paddingRight);
    return { left: Math.round(r.left - inL), right: Math.round(inR - r.right), width: Math.round(r.width), inner: Math.round(inR - inL) };
  });
  check(`1600 px: the Steps column is centered (margins ${m.left} / ${m.right} px, width ${m.width} of ${m.inner})`,
    Math.abs(m.left - m.right) <= 4 && m.width <= 1040, JSON.stringify(m));
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "04-steps-1600.png", { fullPage: true });
  // a wide screen: room on both sides of the 1040 px column, the same on each side
  const big = await open(2400, 1200);
  const sxBig = big.page.locator(".fl-scroll.is-blocks > .sx").first();
  await sxBig.waitFor({ timeout: 15_000 });
  const mb = await sxBig.evaluate((el) => {
    const p = el.parentElement;
    const cs = getComputedStyle(p);
    const pr = p.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return { left: Math.round(r.left - pr.left - parseFloat(cs.paddingLeft)), right: Math.round(pr.right - parseFloat(cs.paddingRight) - r.right),
      width: Math.round(r.width) };
  });
  check(`2400 px: the Steps column is 1040 px wide and centered (margins ${mb.left} / ${mb.right} px)`,
    mb.width === 1040 && mb.left > 20 && Math.abs(mb.left - mb.right) <= 4, JSON.stringify(mb));
  await big.page.evaluate(() => window.scrollTo(0, 0));
  await shot(big.page, "04b-steps-2400.png");

  // 3. dark mode, the same card
  const dark = await open(1600, 1000, "dark");
  const darkCode = dark.gate.getByRole("region", { name: "The code of this criterion" });
  await darkCode.waitFor({ timeout: 30_000 });
  await darkCode.scrollIntoViewIfNeeded();
  await shot(dark.page, "05-gate-dark.png");

  // 4. 390 px: nothing scrolls the page sideways
  const phone = await open(390, 844);
  const phoneCode = phone.gate.getByRole("region", { name: "The code of this criterion" });
  await phoneCode.waitFor({ timeout: 30_000 });
  await phoneCode.scrollIntoViewIfNeeded();
  await wait(300);
  const over = await phone.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  const boxScrolls = await phoneCode.locator(".diff-rows").first().evaluate((el) => el.scrollWidth > el.clientWidth).catch(() => false);
  check(`390 px: the page does not scroll sideways (${over} px over); a long line scrolls inside its box (${boxScrolls})`, over <= 1, over);
  await shot(phone.page, "06-gate-390.png");
  await shot(phone.page, "07-page-390.png", { fullPage: true });

  const bad = failed.filter((f) => !/\/api\/web\/code/.test(f));
  check("no console error and no failed request on the way", !errors.length && !bad.length, [...errors, ...bad].join(" | "));
} catch (e) {
  check("the walk ran to its end", false, e instanceof Error ? e.stack || e.message : String(e));
}
await browser.close();
console.log(JSON.stringify({ checks, errors, failed }));
