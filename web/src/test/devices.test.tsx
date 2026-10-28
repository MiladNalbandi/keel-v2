// Settings › Devices: the paired phones, Remove (asks in the page), and Pair a phone (QR code, address picker, countdown).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { countdown, DevicesSection } from "../components/Devices";
import { qrMatrix } from "../components/QrCode";
import { db, server } from "./setup";

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const pixel = {
  id: "dev-1",
  name: "Pixel 8",
  platform: "android",
  createdAt: ago(600),
  lastSeenAt: ago(5),
  lastIp: "192.168.1.50",
};
const tablet = {
  id: "dev-2",
  name: "Tab S9",
  platform: "android",
  createdAt: ago(900),
  lastSeenAt: null,
  lastIp: null,
};

describe("Settings › Devices", () => {
  it("lists the paired phones with platform, last seen and last address", async () => {
    db.devices = [pixel, tablet];
    render(<DevicesSection />);
    const list = await screen.findByRole("list", { name: "Paired devices" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Pixel 8");
    expect(rows[0]).toHaveTextContent(
      "Android · last seen 5 min ago · 192.168.1.50",
    );
    expect(rows[1]).toHaveTextContent("last seen never");
  });

  it("says when no phone is paired", async () => {
    render(<DevicesSection />);
    expect(
      await screen.findByText("No phone is paired yet."),
    ).toBeInTheDocument();
  });

  it("Remove asks first, in the page; Cancel keeps the phone, Remove removes it", async () => {
    const user = userEvent.setup();
    db.devices = [pixel];
    render(<DevicesSection />);
    await user.click(
      await screen.findByRole("button", { name: "Remove Pixel 8" }),
    );
    const ask = screen.getByRole("group", { name: "Confirm" });
    expect(ask).toHaveTextContent("Remove Pixel 8? It is signed out at once");
    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(db.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(screen.getByText("Pixel 8")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove Pixel 8" }));
    await user.click(
      within(screen.getByRole("group", { name: "Confirm" })).getByRole(
        "button",
        { name: "Remove" },
      ),
    );
    await waitFor(() =>
      expect(db.calls.find((c) => c.method === "DELETE")?.path).toBe(
        "/api/devices/dev-1",
      ),
    );
    expect(
      await screen.findByText("No phone is paired yet."),
    ).toBeInTheDocument();
  });

  it("Pair a phone shows the QR code for the address, a picker between addresses and the code's countdown", async () => {
    const user = userEvent.setup();
    const qr = (u: string) =>
      `keel://pair?u=${encodeURIComponent(u)}&c=K7M2P9QX4T&n=keel-v2`;
    server.use(
      http.post("/api/devices/pairing", () =>
        HttpResponse.json({
          code: "K7M2P9QX4T",
          expiresAt: new Date(Date.now() + 125_000).toISOString(),
          name: "keel-v2",
          reachable: true,
          qr: qr("https://mac.tail1234.ts.net"),
          urls: [
            {
              kind: "tailscale",
              url: "https://mac.tail1234.ts.net",
              qr: qr("https://mac.tail1234.ts.net"),
            },
            {
              kind: "wifi",
              url: "http://192.168.1.20:8088",
              qr: qr("http://192.168.1.20:8088"),
            },
            {
              kind: "local",
              url: "http://localhost:8088",
              qr: qr("http://localhost:8088"),
            },
          ],
        }),
      ),
    );
    render(<DevicesSection />);
    await user.click(
      await screen.findByRole("button", { name: "Pair a phone" }),
    );
    const img = await screen.findByRole("img", {
      name: "Pairing QR code for https://mac.tail1234.ts.net",
    });
    expect(img).toHaveAttribute("data-qr", qr("https://mac.tail1234.ts.net"));
    expect(img.querySelector("path")?.getAttribute("d")).toMatch(/^M\d/);
    expect(screen.getByTestId("pair-url")).toHaveTextContent(
      "https://mac.tail1234.ts.net",
    );
    expect(screen.getByText("K7M2P9QX4T")).toBeInTheDocument();
    expect(screen.getByTestId("pair-left").textContent).toMatch(/^2:0[3-5]$/);
    expect(screen.queryByRole("note")).not.toBeInTheDocument(); // reachable: no 127.0.0.1 hint

    const picker = screen.getByRole("radiogroup", {
      name: "Address for the phone",
    });
    expect(
      within(picker)
        .getAllByRole("radio")
        .map((r) => r.textContent),
    ).toEqual(["Tailscale", "Wi-Fi", "This computer"]);
    await user.click(within(picker).getByRole("radio", { name: "Wi-Fi" }));
    expect(screen.getByTestId("pair-url")).toHaveTextContent(
      "http://192.168.1.20:8088",
    );
    expect(
      screen.getByRole("img", { name: /Pairing QR code/ }),
    ).toHaveAttribute("data-qr", qr("http://192.168.1.20:8088"));
    expect(
      screen.getByText(/Plain http inside your Wi-Fi/),
    ).toBeInTheDocument();
  });

  it("when keel only answers on this computer, it says how to open it to a phone", async () => {
    const user = userEvent.setup();
    render(<DevicesSection />);
    await user.click(
      await screen.findByRole("button", { name: "Pair a phone" }),
    );
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("127.0.0.1");
    expect(note).toHaveTextContent("keel2 start --wifi");
    expect(note).toHaveTextContent("keel2 share tailscale");
    expect(screen.getByTestId("pair-url")).toHaveTextContent(
      "http://localhost:8080",
    );
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("0.18.66 is part of Settings › Access: the old link opens it, with Pair a phone", async () => {
    location.hash = "#/settings/devices";
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Access" }),
    ).toBeInTheDocument();
    expect(location.hash).toBe("#/settings/access");
    expect(
      await screen.findByRole("button", { name: "Pair a phone (QR code)" }),
    ).toBeInTheDocument();
  });
});

describe("pairing helpers", () => {
  it("counts down in minutes and seconds, never below zero", () => {
    const now = Date.parse("2026-10-23T10:00:00Z");
    expect(countdown("2026-10-23T10:10:00Z", now)).toBe("10:00");
    expect(countdown("2026-10-23T10:00:59Z", now)).toBe("0:59");
    expect(countdown("2026-10-23T09:59:00Z", now)).toBe("0:00");
  });

  it("a pairing link fits a QR code (square, a valid size)", () => {
    const m = qrMatrix(
      "keel://pair?u=https%3A%2F%2Fmilis-macbook-pro.tail1234.ts.net%3A8088&c=K7M2P9QX4T&n=keel-v2",
    );
    expect(m.length).toBe(m[0].length);
    expect((m.length - 17) % 4).toBe(0);
    // the three finder patterns' corners are dark
    expect([m[0][0], m[0][m.length - 1], m[m.length - 1][0]]).toEqual([
      true,
      true,
      true,
    ]);
  });
});
