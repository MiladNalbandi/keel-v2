# keel v2

keel's test-first flow as a **LangGraph** graph, with **LangChain** agents, in one Docker image.
You give it a project folder; it gives you a dashboard to run flows, watch agents, approve gates,
and see tokens and cost.

- **Run** — Flow (the live graph, gates, checkpoints, rewind), Live agents, Jobs
- **Project** — Repo (branch, files, keel docs, memory), Map (system, modules, database ER, API, journeys), Wiki
- **Build** — Workflows (diagram editor, library, import/export), Agents, Skill hub, Stacks, Tools (MCP)
- **Control** — Budget and limits, Settings (general + per project), Connections (Claude, Codex/GPT, GitHub Copilot)

## Start

```bash
docker run -d --name keel-v2 -p 127.0.0.1:8080:8080 \
  -v /path/to/your/project:/workspace -v keel-data:/data \
  <dockerhub-user>/keel-v2
```

Open http://localhost:8080. Or with the launcher in this repo:

```bash
./keel2 /path/to/your/project
```

The first time, every agent uses the built-in **fake** model, so you can try a whole flow without
any login. Then open **Control › Connections** and pick Claude, Codex/GPT or GitHub Copilot
(CLI login, OpenCode, or an API key). `./keel2` mounts `~/.claude`, `~/.codex` and `~/.copilot`
read-only when they exist, so CLI logins on your machine work inside the container.

| Folder in the container | What |
|---|---|
| `/workspace` | your project (one git repo, or a folder of repos) |
| `/data` | all keel v2 state: database, LangGraph checkpoints, encrypted keys, notifications |
| `/opt/keel` | keel v1 (CLI, MCP server, agents, skills, stacks) |

## Build the image yourself

```bash
docker build --build-context keel=../keel -t keel-v2 .          # keel v1 source next to this repo
docker build --build-context keel=../keel --build-arg INSTALL_CLIS=1 -t keel-v2 .   # with claude, codex, copilot, opencode CLIs
docker compose up --build                                         # same, with PROJECT=/path/to/repo
```

Publish: push a tag `v0.1.0`; `.github/workflows/docker.yml` builds amd64 + arm64 and pushes to Docker Hub
(set secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`).

## Develop

```bash
cd engine && uv run keel-engine            # 127.0.0.1:8090
cd api && ./gradlew bootRun                # :8080
cd web && npm run dev                      # :5173
```

How the parts talk: [docs/CONTRACT.md](docs/CONTRACT.md). The approved design: [docs/mockup.html](docs/mockup.html).

| Part | Tech | Tests |
|---|---|---|
| `engine/` | Python 3.12, FastAPI, LangGraph, LangChain, MCP | `uv run pytest` (includes parity with keel v1's rule tables) |
| `api/` | Kotlin, Spring Boot 3, SQLite, Flyway | `./gradlew test` |
| `web/` | React, TypeScript, Vite | `npm test` |
