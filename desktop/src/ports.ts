import net from "node:net";

/** True when nothing listens on host:port (we can bind it ourselves for a moment). */
export function isFree(port: number, host = "127.0.0.1"): Promise<boolean> {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.unref();
    s.once("error", () => resolve(false));
    s.listen({ port, host, exclusive: true }, () => s.close(() => resolve(true)));
  });
}

/** The first free port from `from` (50 tries), never one of `taken`. */
export async function firstFree(from: number, taken: number[] = []): Promise<number> {
  for (let p = from; p < from + 50; p++) {
    if (!taken.includes(p) && (await isFree(p))) return p;
  }
  throw new Error(`no free port between ${from} and ${from + 49}`);
}

/** Any free port the system gives, never one of `taken`. */
export async function anyFree(taken: number[] = []): Promise<number> {
  for (let i = 0; i < 10; i++) {
    const p = await new Promise<number>((resolve, reject) => {
      const s = net.createServer();
      s.unref();
      s.once("error", reject);
      s.listen({ port: 0, host: "127.0.0.1" }, () => {
        const a = s.address();
        const port = typeof a === "object" && a ? a.port : 0;
        s.close(() => resolve(port));
      });
    });
    if (p && !taken.includes(p)) return p;
  }
  throw new Error("no free port");
}
