// Records the README GIF from a REAL keel v2 dashboard while a flow runs (nothing is drawn or mocked).
//
//   1. Start keel on a small scratch project (with SQL migrations, so the Map has a database diagram) and start a
//      feature flow; let it run until a gate waits.
//   2. npx -y -p playwright@1 node docs/gif/record.js          (URL=http://localhost:8080 by default)
//   3. ffmpeg -i docs/gif/out/*.webm -vf "fps=6,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" docs/keel-v2.gif
//      (about 5 MB for 38 seconds; 8 fps and 128 colours make it 8 MB)
//
// The tour, with the budget bar on top all the way: all projects, the live flow, the inbox, the database diagram,
// the code graph (packages, one package, one function in the middle), the repo, then the gate: it clicks Approve
// for real.
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.URL || "http://localhost:8080";
const OUT = process.env.OUT || path.join(__dirname, "out");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 760 }, colorScheme: "dark",
    recordVideo: { dir: OUT, size: { width: 1280, height: 760 } } });
  const page = await ctx.newPage();
  const go = async (hash, ms) => {
    await page.goto(`${BASE}/#/${hash}`);
    await page.waitForLoadState("networkidle").catch(() => {});
    await wait(ms);
  };
  /** Move the pointer to an element like a person would, then double-click (or click) it. */
  const press = async (locator, { double = false, pause = 600 } = {}) => {
    const el = locator.first();
    if (!(await el.count())) return false;
    await el.scrollIntoViewIfNeeded().catch(() => {});
    const box = await el.boundingBox();
    if (box) { await page.mouse.move(box.x + box.width / 2, box.y + Math.min(box.height / 2, 16), { steps: 14 }); await wait(pause); }
    if (double) await el.dblclick(); else await el.click();
    return true;
  };

  // the Map opens on its database diagram
  const pid = await page.goto(`${BASE}/api/projects`).then((r) => r.json()).then((l) => l[0]?.id).catch(() => null);
  await page.goto(`${BASE}/#/projects`);
  if (pid) await page.evaluate((p) => localStorage.setItem(`keel2.map.${p}.tab`, "er"), pid);

  await go("projects", 2800);
  await go("flow", 2200);
  await page.mouse.move(700, 420);
  for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, 320); await wait(550); }
  await page.mouse.wheel(0, -3000); await wait(500);
  await go("inbox", 2600);
  await go("map", 3600);
  await go("graph", 2600);
  if (await press(page.locator("g.gbox", { hasText: "src/app" }), { double: true })) {
    await wait(2400);
    if (await press(page.locator("g.gbox", { hasText: "checkout" }), { double: true })) await wait(3600);
  }
  await go("repo", 2600);
  await go("flow", 1500);
  // "Go to it" brings the waiting gate into view; its decision buttons are named by the choice ("approve")
  if (await press(page.getByRole("button", { name: "Go to it" }), { pause: 700 })) await wait(1600);
  await press(page.getByRole("button", { name: "approve", exact: true }), { pause: 900 });
  await wait(3200);
  await ctx.close(); await browser.close();
})();
