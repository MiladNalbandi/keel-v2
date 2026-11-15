// keel desktop's logic without Electron: node --test out/test/ (npm test).
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { linuxGitHint } from "../src/checks";
import { cleanEnv, keelEnv } from "../src/keelenv";
import { bundleIn, pathsFor } from "../src/paths";
import { anyFree, firstFree, isFree } from "../src/ports";
import { addFolder, readSettings, writeSettings } from "../src/settings";
import { loginShellPath, mergePath, parseMarked, which } from "../src/shellenv";
import { readPorts } from "../src/supervisor";
import net from "node:net";

test("data folders per OS, never keel2's ~/.keel2", () => {
  assert.deepEqual(pathsFor("darwin", "/Users/ana", {}), {
    root: "/Users/ana/Library/Application Support/keel",
    data: "/Users/ana/Library/Application Support/keel/data",
    run: "/Users/ana/Library/Application Support/keel/run",
    logs: "/Users/ana/Library/Logs/keel",
    settings: "/Users/ana/Library/Application Support/keel/desktop.json",
  });
  assert.equal(pathsFor("linux", "/home/ana", {}).data, "/home/ana/.local/share/keel/data");
  assert.equal(pathsFor("linux", "/home/ana", { XDG_DATA_HOME: "/x/share" }).logs, "/x/share/keel/logs");
  assert.equal(pathsFor("linux", "/home/ana", { XDG_DATA_HOME: "relative" }).root, "/home/ana/.local/share/keel");
  assert.equal(pathsFor("darwin", "/Users/ana", { KEEL_DESKTOP_HOME: "/tmp/k" }).logs, "/tmp/k/logs");
  assert.equal(bundleIn("/Applications/keel.app/Contents/Resources").java, "/Applications/keel.app/Contents/Resources/runtime/jre/bin/java");
});

test("the login shell's PATH: between the marks only, whatever its start files print", async () => {
  assert.equal(parseMarked("motd\n__KEEL_PATH__/opt/homebrew/bin:/usr/bin__KEEL_PATH__\nbye"), "/opt/homebrew/bin:/usr/bin");
  assert.equal(parseMarked("no marks"), null);
  assert.equal(parseMarked("__KEEL_PATH__nothing__KEEL_PATH__"), null);
  const p = await loginShellPath("/bin/bash", { ...process.env, PATH: "/usr/bin:/bin" });
  assert.ok(p === null || p.includes("/"), String(p));
  // a shell that hangs is given up after the timeout
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kd-"));
  const slow = path.join(dir, "slow-shell");
  fs.writeFileSync(slow, "#!/bin/sh\nsleep 30\n", { mode: 0o755 });
  const t = Date.now();
  assert.equal(await loginShellPath(slow, process.env, 300), null);
  assert.ok(Date.now() - t < 3000);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("PATH: the person's first, no doubles, the app's own tools last", () => {
  assert.equal(mergePath("/opt/homebrew/bin:/usr/bin", "/usr/bin:/bin", "/app/runtime/bin"), "/opt/homebrew/bin:/usr/bin:/bin:/app/runtime/bin");
  assert.equal(mergePath(null, "/usr/bin", "/app/bin"), "/usr/bin:/app/bin");
  // the AppImage's mount on the inherited PATH: left out, only the app's own tools stay (last)
  assert.equal(
    mergePath("/usr/bin", "/tmp/.mount_keelX:/tmp/.mount_keelX/usr/sbin:/usr/local/bin", "/tmp/.mount_keelX/resources/runtime/bin", "/tmp/.mount_keelX"),
    "/usr/bin:/usr/local/bin:/tmp/.mount_keelX/resources/runtime/bin",
  );
  assert.equal(which("sh", "/nowhere:/bin:/usr/bin")?.endsWith("/sh"), true);
  assert.equal(which("no-such-program-x", "/bin:/usr/bin"), null);
});

test("keel's environment: the app's Python and Java, never on PATH; the AppImage's mount and Electron's settings left out", () => {
  const base = {
    PATH: "/ignored",
    HOME: "/home/ana",
    APPDIR: "/tmp/.mount_keelXY",
    APPIMAGE: "/home/ana/keel.AppImage",
    LD_LIBRARY_PATH: "/tmp/.mount_keelXY/usr/lib",
    ELECTRON_RUN_AS_NODE: "1",
    KEEL_PORT: "9999",
    GOOGLE_API_KEY: "kept",
    LANG: "de_DE.UTF-8",
  };
  assert.deepEqual(Object.keys(cleanEnv(base)).sort(), ["GOOGLE_API_KEY", "HOME", "LANG", "PATH"]);
  const b = bundleIn("/r");
  const env = keelEnv(base, {
    bundle: b,
    paths: pathsFor("linux", "/home/ana", {}),
    ports: { api: 8180, engine: 40001, back: 40002 },
    folders: ["/home/ana/shop", "/home/ana/libs"],
    token: "t0k",
    pathValue: "/usr/local/bin:/usr/bin:/r/runtime/bin",
    version: "0.27.0",
  });
  assert.equal(env.PATH, "/usr/local/bin:/usr/bin:/r/runtime/bin");
  assert.equal(env.KEEL_PORT, "8180");
  assert.equal(env.KEEL_BACK_PORT, "40002");
  assert.equal(env.KEEL_FOLDERS, "/home/ana/shop:/home/ana/libs");
  assert.equal(env.KEEL_WORKSPACE, "/home/ana/shop");
  assert.equal(env.KEEL_PROJECT_NAME, "shop");
  assert.equal(env.KEEL_JAVA, "/r/runtime/jre/bin/java");
  assert.equal(env.KEEL_PYTHON, "/r/runtime/python/bin/python3");
  assert.equal(env.KEEL_FRONT, "1");
  assert.equal(env.KEEL_DESKTOP, "1");
  assert.equal(env.KEEL_PLUGINS, "on");
  assert.equal(env.LANG, "de_DE.UTF-8");
  assert.equal(env.LD_LIBRARY_PATH, undefined);
  assert.ok(!String(env.PATH).includes("jre") && !String(env.PATH).includes("python"));
  // the demo: no folder, so keel makes its demo project
  const demo = keelEnv({}, { bundle: b, paths: pathsFor("linux", "/h", {}), ports: { api: 1, engine: 2, back: 3 }, folders: [], token: "x", pathValue: "/usr/bin", version: "1", safe: true });
  assert.equal(demo.KEEL_WORKSPACE, "");
  assert.equal(demo.KEEL_PROJECT_NAME, undefined);
  assert.equal(demo.KEEL_PLUGINS, "image");
  assert.equal(demo.LANG, "en_US.UTF-8");
});

test("desktop.json: read what is valid, written 0600; folders once", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kd-"));
  const f = path.join(dir, "sub", "desktop.json");
  assert.deepEqual(readSettings(f), { accepted: false, folders: [] });
  writeSettings(f, { accepted: true, folders: ["/home/ana/shop"], port: 8181 });
  assert.equal(fs.statSync(f).mode & 0o777, 0o600);
  assert.deepEqual(readSettings(f), { accepted: true, folders: ["/home/ana/shop"], port: 8181, bounds: undefined });
  fs.writeFileSync(f, JSON.stringify({ accepted: "yes", folders: ["relative", "/ok"], port: 80 }));
  assert.deepEqual(readSettings(f), { accepted: false, folders: ["/ok"], port: undefined, bounds: undefined });
  fs.writeFileSync(f, "{broken");
  assert.equal(readSettings(f).accepted, false);
  const one = ["/home/ana/code"];
  assert.equal(addFolder(one, "/home/ana/code"), one);
  assert.equal(addFolder(one, "/home/ana/code/shop"), one);
  assert.deepEqual(addFolder(one, "/home/ana/codex"), ["/home/ana/code", "/home/ana/codex"]);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("ports: a taken one is skipped", async () => {
  const s = net.createServer();
  await new Promise<void>((r) => s.listen(0, "127.0.0.1", () => r()));
  const taken = (s.address() as net.AddressInfo).port;
  assert.equal(await isFree(taken), false);
  const p = await firstFree(taken);
  assert.notEqual(p, taken);
  assert.ok((await anyFree([p])) !== p);
  s.close();
  assert.deepEqual(readPorts("api=8180\nengine=40001\nback=40002\n"), { api: 8180, engine: 40001, back: 40002 });
  assert.equal(readPorts("api=8180\n"), null);
});

test("git's install command per Linux", () => {
  assert.equal(linuxGitHint('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n'), "sudo apt install git");
  assert.equal(linuxGitHint("ID=fedora\n"), "sudo dnf install git");
  assert.equal(linuxGitHint('ID="arch"\n'), "sudo pacman -S git");
  assert.equal(linuxGitHint("ID=opensuse-tumbleweed\nID_LIKE=\"opensuse suse\"\n"), "sudo zypper install git");
  assert.match(linuxGitHint(""), /package manager/);
});
