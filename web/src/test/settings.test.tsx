import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db, server } from "./setup";

const row = (label: string) => screen.getByLabelText(label).closest(".sg-row") as HTMLElement;

describe("Settings", () => {
  it("marks a changed setting with a badge and the General value; Reset to General removes it", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    expect(await screen.findByText("1 setting changed for ludus-engine")).toBeInTheDocument();
    const cap = row("Cap per flow (tokens)");
    expect(cap).toHaveClass("is-changed");
    expect(within(cap).getByText("changed for ludus-engine")).toBeInTheDocument();
    expect(within(cap).getByText("General: 500k tokens")).toBeInTheDocument();
    expect(within(row("Gate mode")).getByText("from General")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reset Cap per flow (tokens) to General" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "PUT" && c.path === "/api/projects/ludus-engine/settings")?.body).toEqual({ cap_tokens: null }));
    expect(await screen.findByText("Everything follows General")).toBeInTheDocument();
    expect(within(row("Cap per flow (tokens)")).getByText("from General")).toBeInTheDocument();
    expect(screen.getByTestId("save-state")).toHaveTextContent("Saved: Cap per flow (tokens) follows General again");
  });

  it("changing a value in the project tab makes an override", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("1 setting changed for ludus-engine");
    await user.selectOptions(screen.getByLabelText("Gate mode"), "end");
    await waitFor(() => expect(db.calls.at(-1)?.body).toEqual({ gates_mode: "end" }));
    expect(await screen.findByText("2 settings changed for ludus-engine")).toBeInTheDocument();
    expect(within(row("Gate mode")).getByText("changed for ludus-engine")).toBeInTheDocument();
    expect(screen.getByTestId("save-state")).toHaveTextContent("Saved: Gate mode is end of flow for ludus-engine");
  });

  it("the General tab saves for every project", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("1 setting changed for ludus-engine");
    await user.click(screen.getByRole("tab", { name: "General" }));
    expect(screen.queryByText("1 setting changed for ludus-engine")).not.toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText("When a cap is hit"), "stop");
    await waitFor(() => expect(db.calls.find((c) => c.path === "/api/settings/general")?.body).toEqual({ on_cap: "stop" }));
  });

  it("every section says what it is for, every row what it does; the run mode line follows the choice", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    const flow = await screen.findByRole("region", { name: "Flow and gates" });
    expect(within(flow).getByText("How much a flow does by itself, and when it stops for you.")).toBeInTheDocument();
    for (const name of ["Models", "Budget", "Git", "Notifications", "Environment"]) expect(screen.getByRole("region", { name })).toBeInTheDocument();
    const mode = row("Run mode (new flows)");
    expect(mode).toHaveTextContent("Stops at every gate.");
    await user.selectOptions(screen.getByLabelText("Run mode (new flows)"), "important");
    await waitFor(() => expect(row("Run mode (new flows)")).toHaveTextContent("Approves a criterion's AC gate by itself"));
    // the cheaper model is Claude Haiku in General: the row says when it is used
    expect(screen.getByRole("group", { name: "Cheaper model" })).toHaveTextContent("Used only when a cap or a plan window is nearly used");
  });

  it("names KeelBot as co-author of keel's commits by default; the author can be set as Name <email>", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("1 setting changed for ludus-engine");
    const git = screen.getByRole("region", { name: "Git" });
    expect(within(git).getByLabelText("KeelBot as co-author")).toHaveValue("1");
    expect(row("KeelBot as co-author")).toHaveTextContent("Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>");
    const author = within(git).getByLabelText("Commit author");
    expect(author).toHaveValue("");
    expect(author).toHaveAttribute("placeholder", "Ada Lovelace <ada@example.com>");
    await user.type(author, "Ada Lovelace <ada@example.com>{Enter}");
    await waitFor(() => expect(db.calls.at(-1)?.body).toEqual({ commit_author: "Ada Lovelace <ada@example.com>" }));
    await user.selectOptions(screen.getByLabelText("KeelBot as co-author"), "0");
    await waitFor(() => expect(db.calls.at(-1)?.body).toEqual({ commit_coauthor: false }));
    await waitFor(() => expect(row("KeelBot as co-author")).toHaveTextContent("keel's commits name only their author."));
  });

  it("says when the cheaper model is the fake model (a real flow pauses instead)", async () => {
    db.overrides["ludus-engine"] = { ...db.overrides["ludus-engine"], cheaper_model: { provider: "fake", mode: "api", model: "fake" } };
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("2 settings changed for ludus-engine");
    const g = screen.getByRole("group", { name: "Cheaper model" });
    expect(within(g).getByText(/Not set: the fake model never runs a real flow/)).toHaveClass("is-warn");
  });

  it("'Only changed' leaves the changed rows; a failed save says so in the save state", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await screen.findByText("1 setting changed for ludus-engine");
    await user.click(screen.getByRole("checkbox", { name: "Only changed" }));
    expect(screen.getByLabelText("Cap per flow (tokens)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Gate mode")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Only changed" }));
    server.use(http.put("/api/projects/:pid/settings", () => HttpResponse.json({ error: "The settings file is read-only." }, { status: 500 })));
    await user.selectOptions(screen.getByLabelText("Gate mode"), "end");
    await waitFor(() => expect(screen.getByTestId("save-state")).toHaveTextContent("Not saved: The settings file is read-only."));
    expect(screen.getByRole("alert")).toHaveTextContent("The settings file is read-only.");
  });
});
