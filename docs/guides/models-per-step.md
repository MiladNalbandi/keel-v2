# Choose models per step

Pick the model and the thinking effort for each agent: for the whole project, for one flow, or for one step.

## First: log in

keel can only use a model when it has the login or the key.

1. Open **Settings › Models & logins** and press **Set up login**.
2. Pick Claude, Codex, GitHub Copilot, Vertex AI or a local model (Ollama, LM Studio, vLLM).

Settings › Models & logins shows a subscription as ready only when keel has its login. You can also save a login
from the terminal:

```bash
keel2 token claude        # also: codex, copilot, anthropic, openai, github
keel2 tokens              # which logins and keys are saved (hints only)
```

## Agents do the steps

Each step of a flow is done by an **agent**: a role with its own prompt, model and effort. Some examples:

| Role word | Agent | What it does |
|---|---|---|
| planning | explorer (in the spec phase) | reads the request and the code, and asks you questions |
| coding | implementer | writes the code |
| testing | test-author | writes the failing test |

The explorer also triages, sets up, and asks clarify's questions. So when you change the explorer for the whole
project, you change all of these.

## For the whole project: the Agents page

Open the **Agents** page. For each agent, pick a model and a thinking effort. This is per agent and per project.

```text
 Agents page (project)  ─►  every flow of this project uses it
      │
      └─ one flow or one step changed (from an AI tool)  ─►  only there; the rest keeps the project's choice
```

## Thinking effort

**Thinking effort** is how hard the model thinks before it acts. More effort is slower and uses more tokens. Less
is quicker and cheaper. **default** leaves it to the engine.

| Engine | Efforts |
|---|---|
| Claude Code (subscription) | low, medium, high, xhigh, max (not Haiku) |
| Claude API key | low, medium, high, xhigh, max (not Haiku) |
| Codex CLI and OpenAI key | what each model offers |
| Copilot through OpenCode | low, medium, high (newer GPT also xhigh; Gemini none) |
| Copilot CLI, GitHub Models key | none |

A model without an effort setting shows "no effort setting", and keel ignores an effort it cannot pass on.

## For one flow or one step

Connect an AI tool at the write level (see [Connect an AI tool (MCP)](#/help/connect-an-ai-tool)). Then:

- `keel_models` lists every provider, its models and the efforts each model takes. It also says which ones are set
  up. No secret is shown.
- `keel_agent_model` sets an agent's model and effort. It takes role words too (planning, coding, testing).
  - Pass `thread_id` to change only that flow. With a role word, only its steps in that phase change.
  - Pass `step` to change one step of that flow.
  - The project's Agents page and the other flows keep their models.
- A wrong provider, model or effort is refused, with a list of the ones there are.
- keel asks you first before it changes a model.

## KeelBot's model

KeelBot uses the `helper` agent's model (Agents page). You can change it, and its effort, for one chat in the
KeelBot panel. In keel TUI:

```
/model                 pick the model, then the effort
/effort high           set the effort
/effort default        clear it
```

## The final review

By default keel's last review of a flow runs on a **stronger** model than the project's. For example, when the
project uses Claude Sonnet or Haiku, the final review uses Opus at high effort. Change this in
[Settings › Review](#/help/settings-review). A model you pick for one step of one flow still wins.

## Where you see the model

The model and effort show next to each other everywhere: the Agents page, Jobs, Live agents, the Flow page,
KeelBot's answers ("sonnet · high"), and keel TUI. Each agent call keeps the effort it ran with, so the history
shows it too.

To compare models, **Build › Quality** runs small test projects through the flows with the models you pick and
gives each run a score from 0 to 100.
