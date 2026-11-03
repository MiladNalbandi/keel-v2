import { describe, expect, it } from "vitest";
import { isLocal, localKeyAllowed, portOf, statusPort, urlFromWorkspace } from "../../src/logic/localKey";

const STATUS = "keel is \u001b[1mrunning\u001b[0m (ready)\n  dashboard  http://localhost:8088\n  version    keel v2 0.21.3\n";

describe("keel2's local key", () => {
  it("reads keel's port from keel2 status", () => {
    expect(statusPort(STATUS)).toBe(8088);
    expect(statusPort("keel is not running. Start it: keel2 start /path/to/project\n")).toBeUndefined();
    expect(statusPort("  dashboard  http://evil.example:8088\n")).toBeUndefined();
    expect(statusPort("  dashboard  http://localhost:99999\n")).toBeUndefined();
  });

  it("knows the port of an address", () => {
    expect(portOf("http://localhost:8088/")).toBe(8088);
    expect(portOf("http://localhost/")).toBe(80);
    expect(portOf("https://localhost/")).toBe(443);
    expect(portOf("nonsense")).toBeUndefined();
  });

  it("goes only to localhost on the port keel2 reports", () => {
    expect(localKeyAllowed("http://localhost:8088/", {}, 8088)).toBe(true);
    expect(localKeyAllowed("http://127.0.0.1:8088/", undefined, 8088)).toBe(true);
    // another program on another localhost port never gets it
    expect(localKeyAllowed("http://localhost:3000/", {}, 8088)).toBe(false);
    // keel2 runs no keel: nobody gets it
    expect(localKeyAllowed("http://localhost:8088/", {}, undefined)).toBe(false);
    expect(localKeyAllowed("http://192.168.1.20:8088/", {}, 8088)).toBe(false);
    expect(isLocal("https://keel.example.ts.net/")).toBe(false);
  });

  it("never to a keel.url that a workspace sets", () => {
    expect(urlFromWorkspace({ workspaceValue: "http://localhost:8088" })).toBe(true);
    expect(urlFromWorkspace({ workspaceFolderValue: "http://localhost:8088" })).toBe(true);
    expect(urlFromWorkspace({})).toBe(false);
    expect(localKeyAllowed("http://localhost:8088/", { workspaceValue: "http://localhost:8088" }, 8088)).toBe(false);
    expect(localKeyAllowed("http://localhost:8088/", { workspaceFolderValue: "http://localhost:8088/" }, 8088)).toBe(false);
  });
});
