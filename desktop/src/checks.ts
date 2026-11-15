// What keel needs from the computer before it starts: git (every flow, the demo project). Java, Python and ripgrep
// are inside the app.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { which } from "./shellenv";

export type GitCheck = { ok: true; git: string } | { ok: false; hint: string; canInstall: boolean };

/** The install command for this Linux distribution (/etc/os-release ID and ID_LIKE). */
export function linuxGitHint(osRelease: string): string {
  const field = (k: string) => (osRelease.match(new RegExp(`^${k}="?([^"\\n]*)"?$`, "m")) || [])[1] || "";
  const ids = `${field("ID")} ${field("ID_LIKE")}`.toLowerCase().split(/\s+/);
  if (ids.some((i) => ["debian", "ubuntu", "linuxmint", "pop"].includes(i))) return "sudo apt install git";
  if (ids.some((i) => ["fedora", "rhel", "centos"].includes(i))) return "sudo dnf install git";
  if (ids.some((i) => ["arch", "manjaro", "endeavouros"].includes(i))) return "sudo pacman -S git";
  if (ids.some((i) => i.includes("suse"))) return "sudo zypper install git";
  return "install git with your distribution's package manager";
}

export function checkGit(pathValue: string, platform: NodeJS.Platform = process.platform): GitCheck {
  const git = which("git", pathValue);
  if (platform === "darwin") {
    // /usr/bin/git is only a stand-in until Apple's Command Line Tools are installed: running it would open Apple's
    // install dialog, so ask xcode-select first
    if (!git || git === "/usr/bin/git") {
      const clt = spawnSync("xcode-select", ["-p"], { stdio: "ignore", timeout: 5000 }).status === 0;
      if (git && clt) return { ok: true, git };
      if (!git || !clt)
        return { ok: false, canInstall: true, hint: "Install Apple's Command Line Tools (they bring git), or Homebrew's git: brew install git" };
    }
    return { ok: true, git };
  }
  if (git) return { ok: true, git };
  let rel = "";
  try {
    rel = fs.readFileSync("/etc/os-release", "utf8");
  } catch {
    /* none */
  }
  return { ok: false, canInstall: false, hint: linuxGitHint(rel) };
}
