// Records the README GIF from a REAL keel v2 dashboard while a flow runs (nothing is drawn or mocked).
//
//   1. Start keel on a scratch project and start a feature flow; let it run until an AC gate waits.
//   2. npx -y -p playwright@1 node docs/gif/record.js          (URL=http://localhost:8080 by default)
//   3. ffmpeg -i docs/gif/out/*.webm -vf "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" docs/keel-v2.gif
//
// The tour: all projects, the live flow graph, an agent at work, then the gate: it clicks Approve for real.
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
  await go("projects", 3000);
  await go("flow", 2500);
  await page.mouse.move(700, 400);
  for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, 300); await wait(600); }
  await page.mouse.wheel(0, -3000); await wait(600);
  await go("live", 5000);
  await go("flow", 1500);
  const approve = page.getByRole("button", { name: /^Approve/ }).first();
  await approve.scrollIntoViewIfNeeded(); await wait(2000);
  const box = await approve.boundingBox();
  if (box) { await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 15 }); await wait(700); }
  await approve.click(); await wait(3000);
  await ctx.close(); await browser.close();
})();
