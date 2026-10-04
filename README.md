# keel v2

AI agents write **tested** code for your project, one small step at a time, and you approve the important steps.
Everything runs in one Docker container.

![How keel v2 works: install, start, log in, describe, approve, done](docs/keel-v2.gif)

## Start in 3 steps

You need [Docker](https://docs.docker.com/get-started/get-docker/) (Docker Desktop on a Mac or Windows/WSL 2).

**1. Install** (once)

```bash
curl -fsSL https://raw.githubusercontent.com/MiladNalbandi/keel-v2/main/install.sh | bash
```

**2. Start keel for your project** (a git folder)

```bash
keel2 start ~/path/to/your/project
```

The browser opens http://localhost:8080.

**3. Log in.** Go to **Control › Connections › Set up login** and pick Claude, Codex/GPT or GitHub Copilot (or paste
an API key).

Then go to **Run › Flow › Start a flow**, describe what you want, and approve at the ◆ gates.

## How it works

```
 you: "show each player's rank"
   │
   ▼
 spec ─◆─ red ─ green ─◆─ … (one loop per criterion) … ─ review ─◆─ done
        │     │       │                                          │
        │     │       └ you approve each criterion               └ you approve the end
        │     └ an agent writes the smallest code that passes; keel commits it
        └ you approve the criteria; an agent writes a failing test, keel checks it fails, then commits it
```

- The flow is a [LangGraph](https://github.com/langchain-ai/langgraph) graph; the agents are LangChain or CLI agents.
- keel runs the tests and makes the commits itself, on its own branch (`feat/…`). It never pushes, and it never
  commits files you changed yourself.
- Every step, file change, command and token is visible: **Live agents**, **Jobs**, **Budget**.
- When something needs you, keel plays a sound, shows a pop-up and its mascot jumps.
- [keel v1](https://github.com/MiladNalbandi/keel) is inside: its rules, agents and dashboard (`/keel-v1/`) work on
  the same project.

## Everyday commands

| Command | What it does |
|---|---|
| `keel2 start [folder]` | start keel for a project |
| `keel2 status` | is it running, where, for which project |
| `keel2 stop` · `keel2 restart` | stop it · start it again the same way |
| `keel2 doctor` | find setup problems and say how to fix them |
| `keel2 update` | get the newest version |
| `keel2 help` | all commands (logs, tokens, backup, restore, uninstall …) |

## Problems?

Run `keel2 doctor` first. The most common ones:

| You see | Do this |
|---|---|
| "Docker is not running" | start Docker Desktop (keel2 starts it on a Mac) |
| "not a git repository" | say yes when keel2 offers `git init` |
| port 8080 is busy | nothing: keel2 uses the next free port and tells you |
| an agent "is not logged in" | Control › Connections › Set up login |
| your tests start containers | `keel2 start --docker <folder>` (gives keel your Docker; only for projects you trust) |

<details>
<summary><b>More: logins, Docker mode, folders, without the script</b></summary>

### Logins

The image has claude, codex, copilot and opencode installed. Nothing from your computer is copied in. Each login is
saved encrypted in keel's own database (the `keel-data` volume), so it survives restarts and updates.

| Tool | Log in from the dashboard | Or paste |
|---|---|---|
| Claude (subscription) | sign in on claude.com, paste the code it shows | `claude setup-token` on your computer |
| Codex (ChatGPT) | open the device link, type the code | `~/.codex/auth.json` |
| GitHub Copilot | open github.com/login/device, type the code | a token with "Copilot Requests" |

From the terminal: `keel2 token <claude|codex|copilot>`, and `keel2 tokens` to list them.

### Docker mode

The image has JDK 21, Node 20, Python 3.12 and the Docker CLI (compose, buildx). `keel2 start --docker` mounts your
Docker socket and the project at the **same path** as on your computer, so Testcontainers and compose bind mounts
work. Docker access is as strong as root on your computer.

### Folders in the container

| Folder | What |
|---|---|
| `/workspace` | your project (one git repo, or a folder of repos); its real path with `--docker` |
| `/data` | keel's database, flow checkpoints, encrypted logins |
| `/opt/keel` | keel v1 |

### Without the script

```bash
docker run -d --name keel-v2 -p 127.0.0.1:8080:8080 \
  -v /path/to/your/project:/workspace -v keel-data:/data \
  ghcr.io/miladnalbandi/keel-v2
```

</details>

<details>
<summary><b>Develop keel v2</b></summary>

| Part | Tech | Run | Tests |
|---|---|---|---|
| `engine/` | Python 3.12, FastAPI, LangGraph, LangChain, MCP | `uv run keel-engine` (:8090) | `uv run pytest` |
| `api/` | Kotlin, Spring Boot 3, SQLite, Flyway | `./gradlew bootRun` (:8080) | `./gradlew test` |
| `web/` | React, TypeScript, Vite | `npm run dev` (:5173) | `npm test` |

How the parts talk: [docs/CONTRACT.md](docs/CONTRACT.md).

```bash
docker build -t keel-v2 .                                # everything is installed in the image
docker build --build-arg KEEL_REF=v0.67.0 -t keel-v2 .   # pin keel v1
uv run --with pillow docs/gif/make_gif.py                # redraw the GIF above
```

A version tag (`v*`) builds amd64 + arm64 images and pushes them to `ghcr.io/miladnalbandi/keel-v2` (and to Docker
Hub when the `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are set).

</details>

## License

MIT, see [LICENSE](LICENSE). keel v1 is MIT as well.
