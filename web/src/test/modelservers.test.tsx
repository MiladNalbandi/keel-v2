// 0.18.46 local model servers and Vertex AI: their forms in Connections (Test connection, keys never shown back), the
// model picker's "local" badge and "no tools" mark, and the project setting "Local models only".

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type {
  Catalog,
  Connections,
  LocalModels,
  Model,
  VertexView,
} from "../api";
import { ModelPicker, normalizeCatalog } from "../components/ModelPicker";
import { connState } from "../pages/Connections";
import { MODEL_ROWS } from "../pages/Settings";
import { providerModels } from "./fixtures";
import { server } from "./setup";

const conns: Connections = {
  providers: [
    {
      id: "vertex",
      label: "Vertex AI",
      selected: "api",
      key_set: false,
      modes: [
        {
          id: "api",
          label: "Vertex AI (Google Cloud)",
          ready: false,
          detail: "Set the project and region.",
        },
      ],
    },
    {
      id: "local",
      label: "Local models",
      selected: "api",
      key_set: false,
      modes: [
        {
          id: "api",
          label: "Local model server",
          ready: false,
          detail: "Add a model server.",
        },
      ],
    },
  ],
  machine: [],
};

/** A provider's card once the check answered (before that a "Checking" card has the same test id). */
async function realCard(id: string) {
  await waitFor(() => expect(screen.getByTestId(id)).not.toHaveAttribute("aria-busy"));
  return screen.getByTestId(id);
}

function servers() {
  const calls: { method: string; path: string; body?: unknown }[] = [];
  let local: LocalModels = {
    enabled: false,
    endpoints: [],
    kinds: ["ollama", "lmstudio", "vllm", "llamacpp", "openai-compatible"],
    defaults: {
      ollama: "http://host.docker.internal:11434",
      lmstudio: "http://host.docker.internal:1234/v1",
    },
  };
  let vertex: VertexView = {
    project: "",
    region: "",
    credentials: "adc",
    key_set: false,
    client_email: null,
    ready: false,
  };
  server.use(
    http.get("/api/connections", () => HttpResponse.json(conns)),
    http.get("/api/providers/local", () => HttpResponse.json(local)),
    http.put(
      "/api/providers/local/endpoints/:name",
      async ({ request, params }) => {
        const body = (await request.json()) as Record<string, unknown>;
        calls.push({
          method: "PUT",
          path: `/api/providers/local/endpoints/${params.name}`,
          body,
        });
        local = {
          ...local,
          enabled: true,
          endpoints: [
            {
              name: String(params.name),
              kind: body.kind as "ollama",
              url: String(body.url),
              context: (body.context as number) ?? null,
              key_set: !!body.key,
              key_hint: body.key ? "…xyz" : null,
            },
          ],
        };
        return HttpResponse.json(local);
      },
    ),
    http.post("/api/providers/local/endpoints/:name/test", ({ params }) => {
      calls.push({
        method: "POST",
        path: `/api/providers/local/endpoints/${params.name}/test`,
      });
      return HttpResponse.json({
        ok: true,
        ms: 120,
        models: [{ id: "qwen3-coder:30b" }, { id: "llama3.2:3b" }],
      });
    }),
    http.get("/api/providers/vertex", () => HttpResponse.json(vertex)),
    http.put("/api/providers/vertex", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      calls.push({ method: "PUT", path: "/api/providers/vertex", body });
      vertex = {
        project: String(body.project),
        region: String(body.region),
        credentials: body.credentials_json ? "service-account" : "adc",
        key_set: !!body.credentials_json,
        client_email: body.credentials_json
          ? "keel@p.iam.gserviceaccount.com"
          : null,
        ready: true,
      };
      return HttpResponse.json(vertex);
    }),
  );
  return calls;
}

describe("Connections: local model servers", () => {
  it("adds an Ollama server with its default URL, keeps the key secret, and tests the connection", async () => {
    const calls = servers();
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    const card = await realCard("conn-local");
    expect(within(card).getByText("Not set up")).toBeInTheDocument();
    // Set up opens the provider's row (one row is open at a time)
    await user.click(within(card).getByRole("button", { name: "Set up" }));
    await user.click(
      await within(card).findByRole("button", { name: "Add a server" }),
    );
    expect(within(card).getByLabelText("URL")).toHaveValue(
      "http://host.docker.internal:11434",
    );
    expect(within(card).getByLabelText("Name")).toHaveValue("ollama");
    await user.type(
      within(card).getByLabelText("Key (optional)"),
      "sk-local-xyz",
    );
    await user.type(within(card).getByLabelText("Context (tokens)"), "16384");
    await user.click(within(card).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        kind: "ollama",
        url: "http://host.docker.internal:11434",
        key: "sk-local-xyz",
        context: 16384,
        price_in: null,
        price_out: null,
      }),
    );
    const row = await within(card).findByTestId("ls-ollama");
    expect(row).toHaveTextContent("key …xyz");
    expect(screen.queryByDisplayValue("sk-local-xyz")).not.toBeInTheDocument();

    await user.click(
      within(row).getByRole("button", { name: "Test connection" }),
    );
    expect(await within(row).findByTestId("ls-ollama-test")).toHaveTextContent(
      /Reached.*2 models: qwen3-coder:30b, llama3.2:3b/,
    );
  });

  it("switching the kind moves a default URL along", async () => {
    servers();
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    const card = await realCard("conn-local");
    await user.click(within(card).getByRole("button", { name: "Set up" }));
    await user.click(
      await within(card).findByRole("button", { name: "Add a server" }),
    );
    await user.selectOptions(within(card).getByLabelText("Server"), "lmstudio");
    expect(within(card).getByLabelText("URL")).toHaveValue(
      "http://host.docker.internal:1234/v1",
    );
  });
});

describe("Connections: Vertex AI", () => {
  it("saves the project, region and a service-account key, then shows only the account", async () => {
    const calls = servers();
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    const card = await realCard("conn-vertex");
    const project = await within(card).findByLabelText("Project id");
    await user.type(project, "my-proj");
    const region = within(card).getByLabelText("Region");
    await user.clear(region);
    await user.type(region, "us-east5");
    const key = within(card).getByLabelText("Service-account key (JSON)");
    await user.click(key);
    await user.paste(
      '{"type": "service_account", "private_key": "-----BEGIN"}',
    );
    await user.click(within(card).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.path === "/api/providers/vertex")?.body,
      ).toMatchObject({ project: "my-proj", region: "us-east5" }),
    );
    expect(
      await within(card).findByText("keel@p.iam.gserviceaccount.com"),
    ).toBeInTheDocument();
    expect(key).toHaveValue("");
    expect(
      within(card).getByRole("button", { name: "Remove the key (use ADC)" }),
    ).toBeInTheDocument();
  });

  it("a provider set up with a form is ready when set up, else needs setting up", () => {
    expect(connState({ ...conns.providers[0], key_set: true })).toMatchObject({
      tone: "ok",
    });
    expect(connState(conns.providers[1])).toMatchObject({
      tone: "warn",
      text: "Not set up",
      need: "key",
    });
  });
});

const withLocal: Catalog = {
  ...providerModels,
  local: {
    label: "Local models",
    source: "server",
    efforts: [],
    default: { mode: "api", model: "box/qwen3-coder:30b" },
    modes: {
      api: [
        {
          id: "box/qwen3-coder:30b",
          label: "qwen3-coder:30b · box",
          efforts: [],
          badge: "local",
          tools: true,
          context: 32768,
        },
        {
          id: "box/llama3.2:3b",
          label: "llama3.2:3b · box",
          efforts: [],
          badge: "local",
          tools: false,
        },
      ],
    },
  },
  vertex: {
    label: "Vertex AI",
    source: "builtin",
    efforts: ["low", "high"],
    default: { mode: "api", model: "claude-sonnet-5-5" },
    modes: {
      api: [
        {
          id: "claude-sonnet-5-5",
          label: "Claude Sonnet 5.5",
          efforts: ["low", "high"],
        },
        { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", efforts: [] },
      ],
    },
  },
};

describe("model picker: local and Vertex AI models", () => {
  it("keeps the badge, tools and context, and the server source", () => {
    const c = normalizeCatalog(withLocal);
    expect(c.local.source).toBe("server");
    expect(c.local.modes.api?.[1]).toEqual({
      id: "box/llama3.2:3b",
      label: "llama3.2:3b · box",
      efforts: [],
      badge: "local",
      tools: false,
    });
    expect(c.local.modes.api?.[0].context).toBe(32768);
  });

  it("lists Vertex AI and local models after the others, with the local badge and a no-tools mark", async () => {
    server.use(
      http.get("/api/providers/models", () => HttpResponse.json(withLocal)),
    );
    const changes: Model[] = [];
    function Harness() {
      const [m, setM] = useState<Model>({
        provider: "local",
        mode: "api",
        model: "box/qwen3-coder:30b",
      });
      return (
        <ModelPicker
          id="t"
          value={m}
          onChange={(x) => {
            changes.push(x);
            setM(x);
          }}
        />
      );
    }
    const user = userEvent.setup();
    render(<Harness />);
    await waitFor(() =>
      expect(screen.getByTestId("t-source")).toHaveTextContent(
        "from the server",
      ),
    );
    expect(
      screen.getByTestId("t-model").querySelector("svg.pi"),
    ).toHaveAttribute("data-brand", "local");
    expect(screen.queryByLabelText("Runs on")).not.toBeInTheDocument();       // #101 one way: the API (here a local server)
    expect(screen.queryByTestId("t-noeffort")).toBeInTheDocument();
    await user.click(screen.getByTestId("t-model"));
    const list = screen.getByRole("listbox");
    const order = within(list)
      .getAllByRole("group")
      .map((g) => g.getAttribute("data-provider"));
    expect(order.indexOf("vertex")).toBeGreaterThan(order.indexOf("copilot"));
    expect(order.indexOf("local")).toBeGreaterThan(order.indexOf("vertex"));
    expect(order[order.length - 1]).toBe("fake");
    const small = within(list).getByRole("option", {
      name: "llama3.2:3b · box",
    });
    expect(small).toHaveTextContent("local");
    expect(small).toHaveTextContent("no tools");
    await user.click(small);
    expect(changes.at(-1)).toEqual({
      provider: "local",
      mode: "api",
      model: "box/llama3.2:3b",
    });
    expect(await screen.findByTestId("t-notools")).toHaveTextContent(
      "Ask, review check and summaries only",
    );
  });
});

describe("Settings: Local models only", () => {
  it("is a row of Models & keys › Models", () => {
    const row = MODEL_ROWS.find((r) => r.key === "local_only")!;
    expect(row.label).toBe("Local models only");
    expect(row.kind.t).toBe("bool");
  });
});
