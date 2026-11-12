// The Cluster page inside keel's web (web/src/test/setup.ts runs every plugin's setup()): the page is in the menu, the search finds
// a pod by its IP, `:svc` shows the services, j/k move the selection, a read-only cluster has no Delete, a change asks in the page
// first, Connect saves what keel may do, and the plugin imports only react, @keel/web-sdk and its own files.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { allPages } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import { age, command, deployOf, execCommand, loopback, matches, type Contexts, type Pod, type Snapshot } from "../clusterApi";

const pod = (name: string, ip: string, status = "Running", extra: Partial<Pod> = {}): Pod => ({
  name, ns: "shop", ready: status === "Running" ? "1/1" : "0/1", status, restarts: status === "Running" ? 0 : 7, ip, node: "node-a", age: 600,
  app: name.split("-")[0], containers: [name.split("-")[0]], images: [`example/${name.split("-")[0]}:a1c94e2`], commit: "a1c94e2", owner: `ReplicaSet/${name.split("-").slice(0, 2).join("-")}`, ...extra,
});

const SNAP: Snapshot = {
  context: "kind-dev", at: "2026-11-05T12:00:00Z", namespaces: ["shop"],
  pods: [pod("web-5c8d7f9d4-qq7lr", "10.42.1.23"), pod("web-5c8d7f9d4-m8zvt", "10.42.2.9"), pod("worker-6b7c9d8f5-h4n2s", "10.42.3.4", "CrashLoopBackOff")],
  deployments: [
    { name: "web", ns: "shop", ready: "2/2", desired: 2, available: 2, updated: 2, images: ["example/web:a1c94e2"], commit: "a1c94e2", age: 600 },
    { name: "worker", ns: "shop", ready: "0/1", desired: 1, available: 0, updated: 1, images: ["example/worker:c77e0a9"], commit: "c77e0a9", age: 600 },
  ],
  services: [{ name: "web", ns: "shop", type: "ClusterIP", cluster_ip: "10.43.8.15", external: [], ports: ["80→80/TCP"] }],
  nodes: [{ name: "node-a", ready: true, roles: ["control-plane"], internal_ip: "192.168.49.2", external_ip: null, version: "v1.31.2", cpu: "8", memory: "16Gi", pods: 3 }],
  events: [{ type: "Warning", reason: "BackOff", object: "pod/worker-6b7c9d8f5-h4n2s", ns: "shop", message: "Back-off restarting failed container", age: 60, count: 4 }],
  ingresses: [], problems: [{ kind: "crash", object: "pod/shop/worker-6b7c9d8f5-h4n2s", ns: "shop", name: "worker-6b7c9d8f5-h4n2s", title: "worker keeps restarting", line: "7 restarts" }],
  metrics: false, warnings: [],
};

let contexts: Contexts;
let calls: { path: string; body?: unknown }[];
const main = () => within(document.getElementById("main")!);
const open = (hash: string) => { location.hash = hash; render(<App />); };

beforeEach(() => {
  contexts = {
    kubectl: true, kubectl_version: "v1.32.3", kubeconfig: "/home/keel/.kube/config", in_docker: true,
    contexts: [
      { name: "kind-dev", cluster: "kind-dev", server: "https://127.0.0.1:42133", namespace: "", current: true, local: true, on: true, access: "ask" },
      { name: "prod-eu", cluster: "prod-eu", server: "https://k8s.prod.example.net", namespace: "", current: false, local: false, on: true, access: "read" },
    ],
  };
  calls = [];
  server.use(
    http.get("/api/plugins/cluster/contexts", () => HttpResponse.json(contexts)),
    http.get("/api/plugins/cluster/snapshot", ({ request }) => HttpResponse.json({ ...SNAP, context: new URL(request.url).searchParams.get("context") })),
    http.get("/api/plugins/cluster/logs", () => HttpResponse.json({ lines: ["worker starting", "ERROR KeyError customer_email"], container: "worker", containers: ["worker"] })),
    http.get("/api/plugins/cluster/describe", () => HttpResponse.json({ text: "Name: worker" })),
    http.get("/api/plugins/cluster/history", () => HttpResponse.json({ items: [] })),
    http.post("/api/plugins/cluster/change", async ({ request }) => {
      calls.push({ path: "change", body: await request.json() });
      return HttpResponse.json({ done: true, message: "The pod is deleted." });
    }),
    http.post("/api/plugins/cluster/exec", async ({ request }) => {
      calls.push({ path: "exec", body: await request.json() });
      return HttpResponse.json({ output: "app.py\n", code: 0 });
    }),
    http.put("/api/plugins/cluster/settings", async ({ request }) => {
      const body = (await request.json()) as { clusters: Record<string, { on: boolean; access: "read" | "ask" | "free" }> };
      calls.push({ path: "settings", body });
      contexts = { ...contexts, contexts: contexts.contexts.map((c) => ({ ...c, ...body.clusters[c.name] })) };
      return HttpResponse.json(contexts);
    }),
  );
});

const podTable = () => main().findByRole("table", { name: "Pods" });
const rows = (t: HTMLElement) => within(t).getAllByRole("row").slice(1);

describe("the Cluster page", () => {
  it("is in the menu under Watch, after Record (#93)", () => {
    const run = allPages().filter((p) => p.group === "watch").sort((a, b) => a.order - b.order).map((p) => p.id);
    expect(run.indexOf("cluster")).toBe(run.indexOf("record") + 1);
  });

  it("lists the pods and finds one by its IP", async () => {
    open("#/cluster");
    const t = await podTable();
    expect(rows(t)).toHaveLength(3);
    await userEvent.type(main().getByRole("searchbox", { name: "Search" }), "10.42.3");
    await waitFor(() => expect(rows(t)).toHaveLength(1));
    expect(within(t).getByRole("button", { name: "worker-6b7c9d8f5-h4n2s" })).toBeTruthy();
  });

  it("shows the services after :svc and Enter", async () => {
    open("#/cluster");
    await podTable();
    const box = main().getByRole("searchbox", { name: "Search" });
    await userEvent.type(box, ":svc{Enter}");
    expect(await main().findByRole("table", { name: "Services" })).toBeTruthy();
    expect((box as HTMLInputElement).value).toBe("");
    expect(main().getByText("10.43.8.15")).toBeTruthy();
  });

  it("moves the selection with j and k, and opens the pod's log", async () => {
    open("#/cluster");
    const t = await podTable();
    await userEvent.click(within(t).getByRole("button", { name: "web-5c8d7f9d4-qq7lr" }));
    expect(rows(t)[0].getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(within(t).getByRole("button", { name: "web-5c8d7f9d4-qq7lr" }), { key: "j" });
    await waitFor(() => expect(rows(t)[1].getAttribute("aria-selected")).toBe("true"));
    fireEvent.keyDown(rows(t)[1], { key: "j" });
    await waitFor(() => expect(rows(t)[2].getAttribute("aria-selected")).toBe("true"));
    expect(await main().findByText("ERROR KeyError customer_email")).toBeTruthy();
    fireEvent.keyDown(rows(t)[2], { key: "k" });
    await waitFor(() => expect(rows(t)[1].getAttribute("aria-selected")).toBe("true"));
  });

  it("deletes a pod only after the page's own confirm", async () => {
    open("#/cluster");
    const t = await podTable();
    await userEvent.click(within(t).getByRole("button", { name: "worker-6b7c9d8f5-h4n2s" }));
    await userEvent.click(await main().findByRole("button", { name: "Delete pod" }));
    expect(calls).toEqual([]);
    const confirm = main().getByRole("group", { name: "Confirm" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls).toEqual([{ path: "change", body: { context: "kind-dev", action: "delete", kind: "pod", ns: "shop", name: "worker-6b7c9d8f5-h4n2s" } }]));
  });

  it("has no change on a read-only cluster", async () => {
    localStorage.setItem("keel.cluster.context", "prod-eu");
    open("#/cluster");
    const t = await podTable();
    expect(main().getByText("read only")).toBeTruthy();
    await userEvent.click(within(t).getByRole("button", { name: "worker-6b7c9d8f5-h4n2s" }));
    expect((await main().findByRole("button", { name: "Delete pod" })).hasAttribute("disabled")).toBe(true);
    expect((main().getByRole("button", { name: "Restart worker" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(main().getByRole("tab", { name: /Shell/ }));
    expect((main().getByRole("textbox", { name: "Command" }) as HTMLInputElement).disabled).toBe(true);
  });

  it("runs one command and shows its output", async () => {
    open("#/cluster");
    const t = await podTable();
    await userEvent.click(within(t).getByRole("button", { name: "web-5c8d7f9d4-qq7lr" }));
    await userEvent.click(await main().findByRole("tab", { name: /Shell/ }));
    await userEvent.type(main().getByRole("textbox", { name: "Command" }), "ls{Enter}");
    expect(await main().findByText("app.py")).toBeTruthy();
    expect(calls).toEqual([{ path: "exec", body: { context: "kind-dev", ns: "shop", name: "web-5c8d7f9d4-qq7lr", container: "web", command: "ls" } }]);
  });

  it("Connect saves what keel may do, and says keel in Docker cannot reach 127.0.0.1", async () => {
    open("#/cluster/connect");
    const row = await main().findByRole("group", { name: "prod-eu" });
    const pick = within(row).getByRole("combobox", { name: "What keel may do on prod-eu" }) as HTMLSelectElement;
    // a cluster that is not on this computer gets no "change without asking"
    expect([...pick.options].map((o) => o.value)).toEqual(["read", "ask"]);
    await userEvent.selectOptions(pick, "ask");
    await userEvent.click(main().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls).toEqual([{ path: "settings", body: { clusters: { "kind-dev": { on: true, access: "ask" }, "prod-eu": { on: true, access: "ask" } } } }]));
    const dev = main().getByRole("group", { name: "kind-dev" });
    expect(within(dev).getByText(/keel cannot reach it/)).toBeTruthy();
    expect([...(within(dev).getByRole("combobox") as HTMLSelectElement).options].map((o) => o.value)).toContain("free");
  });

  it("Connect marks a GKE context and saves a key for clusters only, never showing it again", async () => {
    contexts = {
      ...contexts,
      contexts: [...contexts.contexts, { name: "gke-prod", cluster: "gke_demo_europe-west1_prod", server: "https://203.0.113.10", namespace: "", current: false, local: false, on: true, access: "read", auth: "gke" }],
      google: { source: "vertex", email: null, own_key_set: false, own_email: null, vertex_email: null },
    };
    const sent: unknown[] = [];
    server.use(
      http.put("/api/plugins/cluster/google", async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({ source: "own", email: "gke@demo.iam.gserviceaccount.com", own_key_set: true, own_email: "gke@demo.iam.gserviceaccount.com", vertex_email: null });
      }),
      http.post("/api/plugins/cluster/test", () => HttpResponse.json({ ok: true, ms: 120, version: "v1.31.2", nodes: 3, as: "gke@demo.iam.gserviceaccount.com" })),
    );
    open("#/cluster/connect");
    const gke = await main().findByRole("group", { name: "gke-prod" });
    expect(within(gke).getByText("GKE")).toBeTruthy();
    const panel = main().getByRole("radiogroup", { name: "How GKE clusters sign in" });
    expect(within(panel).getByText(/no service-account key saved/)).toBeTruthy();
    await userEvent.click(within(panel).getByRole("radio", { name: /A key for clusters only/ }));
    const key = '{"type":"service_account","client_email":"gke@demo.iam.gserviceaccount.com","private_key":"x"}';
    fireEvent.change(within(panel).getByLabelText("Service-account key (JSON)"), { target: { value: key } });
    await userEvent.click(within(panel).getByRole("button", { name: "Save sign-in" }));
    await waitFor(() => expect(sent).toEqual([{ source: "own", credentials_json: key }]));
    expect(await within(panel).findByText(/GKE clusters sign in as/)).toBeTruthy();
    expect((within(panel).getByLabelText("Service-account key (JSON)") as HTMLTextAreaElement).value).toBe("");
    await userEvent.click(within(gke).getByRole("button", { name: "Test" }));
    expect(await within(gke).findByText(/as gke@demo\.iam\.gserviceaccount\.com/)).toBeTruthy();
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});

describe("the helpers", () => {
  it("reads k9s commands and searches", () => {
    expect(command(":svc")).toEqual({ kind: "svc" });
    expect(command(":ns shop")).toEqual({ ns: "shop" });
    expect(command(":ns all")).toEqual({ ns: "" });
    expect(command(":nope")).toBeNull();
    expect(matches("10.42", "web", "10.42.1.2")).toBe(true);
    expect(matches(":svc", "anything")).toBe(true);
    expect(age(45)).toBe("45s");
    expect(age(3 * 3600)).toBe("3h");
    expect(loopback("https://127.0.0.1:42133")).toBe(true);
    expect(loopback("https://k8s.example.net")).toBe(false);
    expect(execCommand("kind-dev", "shop", "web-1", "web")).toBe("kubectl --context kind-dev -n shop exec -it web-1 -c web -- sh");
    expect(execCommand("a b", "shop", "p", "")).toBe("kubectl --context 'a b' -n shop exec -it p -- sh");
    expect(deployOf(pod("web-5c8d7f9d4-qq7lr", "1"), SNAP.deployments)).toBe("web");
  });
});
