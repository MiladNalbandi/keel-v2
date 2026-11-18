// 0.40 (#201) Rules: Normal | Safety only — the Flow page's chip and switch, the Start a flow picker.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { RULES_MODES, RulesChip, RulesModePicker, RulesSwitch } from "../components/RulesMode";
import { AppProvider } from "../state";
import { db, server } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("rules mode", () => {
  it("Normal comes first: the default everywhere", () => {
    expect(RULES_MODES[0][0]).toBe("normal");
  });

  it("the chip shows only in Safety only", () => {
    const { rerender } = render(<RulesChip mode="normal" />);
    expect(screen.queryByTestId("rules-chip")).toBeNull();
    rerender(<RulesChip mode={undefined} />);
    expect(screen.queryByTestId("rules-chip")).toBeNull();
    rerender(<RulesChip mode="safety-only" />);
    expect(screen.getByTestId("rules-chip")).toHaveTextContent("Safety only");
  });

  it("the picker says what each mode keeps", async () => {
    const user = userEvent.setup();
    const seen: string[] = [];
    render(<RulesModePicker value="normal" onChange={(m) => seen.push(m)} />);
    expect(screen.getByRole("radio", { name: /^Normal/ })).toBeChecked();
    expect(screen.getByText(/anything that cannot be undone is refused/)).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /^Safety only/ }));
    expect(seen).toEqual(["safety-only"]);
  });

  it("<RulesSwitch/> changes a running flow's rules", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/threads/:tid/rules", async ({ request, params }) => {
      const body = await request.json();
      db.calls.push({ method: "POST", path: `/api/threads/${params.tid}/rules`, body });
      return HttpResponse.json({ thread_id: params.tid, status: "waiting", rules_mode: (body as { mode: string }).mode });
    }));
    const seen: string[] = [];
    render(<AppProvider><RulesSwitch threadId="th_7f3a" mode="normal" onChange={(t) => seen.push(t.rules_mode ?? "")} /></AppProvider>);
    const sel = screen.getByLabelText("Rules");
    expect(sel).toHaveValue("normal");
    await user.selectOptions(sel, "safety-only");
    await waitFor(() => expect(calls("POST", "/api/threads/th_7f3a/rules")[0]?.body).toEqual({ mode: "safety-only" }));
    await waitFor(() => expect(seen).toEqual(["safety-only"]));
    expect(await screen.findByRole("status")).toHaveTextContent("Rules: Safety only, from the next agent call.");
  });
});
