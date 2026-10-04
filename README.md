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
  ghcr.io/miladnalbandi/keel-v2
```

Open http://localhost:8080. Your project must be a git repository. Or use the launcher from this repo:

```bash
curl -fsSLO https://raw.githubusercontent.com/MiladNalbandi/keel-v2/main/keel2 && chmod +x keel2
./keel2 /path/to/your/project
```

keel works on its own branch (`feat/<title>`), never commits your uncommitted files, and asks before
anything that would cost real model usage on a real project.

The first time, every agent uses the built-in **fake** model, so you can try a whole flow without
any login. Then open **Control › Connections** and pick Claude, Codex/GPT or GitHub Copilot
(CLI login, OpenCode, or an API key).

Logins: the image has claude, codex, copilot and opencode installed. Nothing from your computer is copied in.
Open **Control › Connections** and press **Set up login**:

| Tool | Log in here (in the container) | Or paste |
|---|---|---|
| Claude (subscription) | sign in on claude.com, paste the code the page shows | `claude setup-token` on your computer |
| Codex (ChatGPT) | open the device link, type the code | `~/.codex/auth.json` from your computer |
| GitHub Copilot | open github.com/login/device, type the code | a fine-grained token with "Copilot Requests" |

Every login is saved encrypted in keel's SQLite database on the `keel-data` volume, so it survives restarts and new
images. `./keel2 token <claude|codex|copilot>` saves one from the terminal; `./keel2 tokens` lists what is saved.

### Projects whose tests need Docker

The image has a full JDK 21, Node 20, Python 3.12 and the Docker CLI (with compose and buildx). When your tests
start containers (Testcontainers, `docker compose`), start keel with `--docker`:

```bash
./keel2 --docker /path/to/your/project
```

This gives keel your computer's Docker (it mounts `/var/run/docker.sock`) and mounts the project at the **same
path** as on your computer, so bind mounts in compose files and Testcontainers work. Only use it with projects you
trust: access to the Docker socket is the same as root on your computer.

| Folder in the container | What |
|---|---|
| `/workspace` | your project (one git repo, or a folder of repos) |
| `/data` | all keel v2 state: database, LangGraph checkpoints, encrypted keys, notifications |
| `/opt/keel` | keel v1 (CLI, MCP server, agents, skills, stacks) |

## Build the image yourself

```bash
docker build -t keel-v2 .                                   # installs everything; keel v1 is cloned from GitHub
docker build --build-arg KEEL_REF=v0.67.0 -t keel-v2 .      # pin keel v1 to a tag or branch
docker build --build-arg INSTALL_CLIS=0 -t keel-v2:slim .   # without the CLIs
docker compose up --build                                         # same, with PROJECT=/path/to/repo
```

Publish: push a tag such as `v0.2.0`; `.github/workflows/docker.yml` builds amd64 + arm64 and pushes to
`ghcr.io/<owner>/keel-v2`, and to Docker Hub as well when the secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are set.

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

## License

MIT — see [LICENSE](LICENSE). keel v1 (cloned into the image) is MIT as well.
