// electron-builder afterPack: a macOS build without a Developer ID (electron-builder.yml mac.identity: null) is signed
// ad hoc (codesign -s -): Apple chips run no code without a signature. No Apple account is needed for that; Gatekeeper
// still asks the person once (System Settings › Privacy & Security › Open Anyway). With a Developer ID (CSC_LINK) the
// real signing replaces this.
const { execFileSync } = require("node:child_process");
const path = require("node:path");

exports.default = async function afterPack(ctx) {
  if (ctx.electronPlatformName !== "darwin" || process.env.CSC_LINK || process.env.CSC_NAME) return;
  const app = path.join(ctx.appOutDir, `${ctx.packager.appInfo.productFilename}.app`);
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", app], { stdio: "inherit" });
  execFileSync("codesign", ["--verify", "--deep", "--strict", app], { stdio: "inherit" });
};
