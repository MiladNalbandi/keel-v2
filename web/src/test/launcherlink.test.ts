import { afterEach, describe, expect, it, vi } from "vitest";
import { linkAction, webUrl } from "../components/launcher/sources";

afterEach(() => vi.restoreAllMocks());

describe("the launcher's web links", () => {
  it("open only http and https addresses", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    linkAction("Open", "https://example.com/pr/1").run();
    expect(open).toHaveBeenCalledWith("https://example.com/pr/1", "_blank", "noopener");
    open.mockClear();
    for (const bad of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///etc/passwd", "vbscript:x"]) {
      const a = linkAction("Open", bad);
      expect(a.disabled).toBeTruthy();
      a.run();
    }
    expect(open).not.toHaveBeenCalled();
  });

  it("knows a web address", () => {
    expect(webUrl("http://localhost:8088/x")).toBe(true);
    expect(webUrl("https://gitlab.com")).toBe(true);
    expect(webUrl("javascript:void(0)")).toBe(false);
    expect(webUrl("blob:https://x/1")).toBe(false);
  });
});
