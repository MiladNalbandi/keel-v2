// Phone access: the phones paired with this keel (keel's Android app) and pairing a new one (Settings › Access).
// The api's device routes answer in camelCase (the app's contract), unlike the rest of keel's api.

import { del, get, post } from "./api";

/** A paired phone (GET /api/devices). */
export type Device = {
  id: string;
  name: string;
  platform: string;
  createdAt: string;
  lastSeenAt?: string | null;
  lastIp?: string | null;
};

/** An address a phone can reach this keel at, and the QR text (keel://pair?u=…&c=…&n=…) that carries it. */
export type PairUrl = { kind: "wifi" | "tailscale" | "tunnel" | "local" | string; url: string; qr: string };

/** A one-time pairing code (POST /api/devices/pairing): 10 minutes, used once. */
export type Pairing = { code: string; expiresAt: string; urls: PairUrl[]; qr: string; name?: string; reachable?: boolean };

export const devicesApi = {
  list: () => get<Device[]>("/devices"),
  remove: (id: string) => del<{ ok: boolean }>(`/devices/${encodeURIComponent(id)}`),
  pairing: () => post<Pairing>("/devices/pairing"),
};
