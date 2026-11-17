// A plugin that is off in this project: its command explains how to turn it on instead of "No command /x".
import { afterEach, describe, expect, it } from "vitest";
import { complete, run, setOffPluginCommands } from "../src/core/commands";
import { offHere, type TuiPluginView } from "../src/core/plugins";

const view = (name: string, title: string, cmd: string): TuiPluginView =>
  ({
    name,
    title,
    version: "1",
    commands: [{ name: cmd, summary: `${title} things` }],
  }) as TuiPluginView;
const ctx = {} as never;

afterEach(() => setOffPluginCommands([]));

describe("a plugin's command in a project where the plugin is off", () => {
  it("offHere lists the plugins keel has that this project does not get", () => {
    const all = [
      view("git", "Git", "git"),
      view("supervisor", "Supervisor", "supervisor"),
    ];
    expect(offHere(all, [all[0]])).toEqual([
      { name: "supervisor", title: "Supervisor", commands: ["supervisor"] },
    ]);
  });

  it("running it says the plugin is off here and where to turn it on", async () => {
    setOffPluginCommands([{ title: "Supervisor", commands: ["supervisor"] }]);
    const r = await run(ctx, "/supervisor status");
    const text = JSON.stringify(r);
    expect(text).toContain("The Supervisor plugin is off for this project");
    expect(text).toContain("Settings › Plugins");
    expect(text).not.toContain("No command /supervisor");
  });

  it("typing it suggests the command with an 'off in this project' hint", () => {
    setOffPluginCommands([{ title: "Supervisor", commands: ["supervisor"] }]);
    const hit = complete(ctx, "/super").find((c) =>
      c.value.startsWith("/supervisor"),
    );
    expect(hit?.hint).toBe("Supervisor plugin · off in this project");
  });

  it("an unknown command still says there is no such command", async () => {
    expect(JSON.stringify(await run(ctx, "/nothing-like-this"))).toContain(
      "No command /nothing-like-this",
    );
  });
});
