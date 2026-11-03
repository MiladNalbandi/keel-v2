// keel 0.22.0 (issue #66) a flow that started behind its base's remote says so on its page, not only in the start's
// answer: keel's own clone was 35 commits behind and nothing said so (a field report).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { openShopFlow } from "./shopFlow";

describe("Flow page: an old base", () => {
  it("shows a banner when the flow started behind its remote", async () => {
    openShopFlow({ base_behind: "main is 35 commits behind origin/main" });
    render(<App />);
    const banner = await screen.findByRole("alert", { name: "The base is not up to date" });
    expect(banner).toHaveTextContent("This flow started from an old base");
    expect(banner).toHaveTextContent("main is 35 commits behind origin/main");
  });

  it("shows nothing when the base was up to date", async () => {
    openShopFlow();
    render(<App />);
    await screen.findByRole("region", { name: "This flow" });
    expect(screen.queryByRole("alert", { name: "The base is not up to date" })).toBeNull();
  });
});
