import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

const row = (label: string) => screen.getByLabelText(label).closest(".setrow") as HTMLElement;

describe("Settings", () => {
  it("shows overrides per project and 'Use general' removes one", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    expect(await screen.findByText("1 setting differ from General")).toBeInTheDocument();
    expect(within(row("Cap per flow (tokens)")).getByText("this project")).toBeInTheDocument();
    expect(within(row("Gate mode")).getByText("from General: every AC")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use general for Cap per flow (tokens)" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "PUT" && c.path === "/api/projects/ludus-engine/settings")?.body).toEqual({ cap_tokens: null }));
    expect(await screen.findByText("0 settings differ from General")).toBeInTheDocument();
    expect(within(row("Cap per flow (tokens)")).getByText("from General: 500k tokens")).toBeInTheDocument();
  });

  it("changing a value in the project tab makes an override", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("1 setting differ from General");
    await user.selectOptions(screen.getByLabelText("Gate mode"), "end");
    await waitFor(() => expect(db.calls.at(-1)?.body).toEqual({ gates_mode: "end" }));
    expect(await screen.findByText("2 settings differ from General")).toBeInTheDocument();
    expect(within(row("Gate mode")).getByText("this project")).toBeInTheDocument();
  });

  it("the General tab saves for every project", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("1 setting differ from General");
    await user.click(screen.getByRole("tab", { name: "General" }));
    await user.selectOptions(await screen.findByLabelText("When a cap is hit"), "stop");
    await waitFor(() => expect(db.calls.find((c) => c.path === "/api/settings/general")?.body).toEqual({ on_cap: "stop" }));
  });
});
