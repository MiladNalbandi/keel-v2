import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

describe("Connections: CLI logins", () => {
  it("saves the Claude login token as its own encrypted secret and shows only the hint", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    const field = await screen.findByLabelText(/Claude login token/);
    await user.type(field, "sk-ant-oat01-secret-9KQ");
    const save = field.parentElement!.querySelector("button")!;
    await user.click(save);
    await waitFor(() => expect(db.calls.find((c) => c.method === "PUT" && c.path === "/api/secrets/CLAUDE_CODE_OAUTH_TOKEN")?.body)
      .toEqual({ value: "sk-ant-oat01-secret-9KQ" }));
    expect(await screen.findByPlaceholderText("…9KQ (saved)")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("sk-ant-oat01-secret-9KQ")).not.toBeInTheDocument();
  });
});
