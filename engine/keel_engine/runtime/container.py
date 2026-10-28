"""A project whose toolchain lives only in a container (keel 0.18.57).

Some projects have PHP (or Python, Node) and its extensions only inside a docker compose service: `.keel/config.yml`
runs every command through it (`docker compose exec -T app vendor/bin/phpunit`). Three things follow:

  setup     a flow's new worktree must not run a stack's bare `composer install` on keel's side: the configured
            dependency command (commands.deps_api / deps_web) runs instead, or nothing (stack_setup.worktree_jobs)
  preflight before the first agent step, a cheap check that the container answers: commands.preflight, else the test
            runner's `--version` through the same exec (preflight_commands); a failure stops at a plain gate
  worktrees `docker compose exec app ...` runs in the container's own folder, a mount of the project folder: a flow in
            .keel/worktrees/<name> would test the project folder's code, not its own. worktree_fit says so (from the
            compose file's volumes), and the api starts such flows in the project folder. An agent may not start
            containers from a worktree (rules.compose_in_worktree: `docker compose up` there made a second stack that
            stayed running), and whatever compose made from a worktree goes when the flow ends (cleanup_worktree)

    exec_of(cmd)             {prefix, service, compose, files, rest} of a command that runs in a container, or None
    container_commands(root) [(key, cmd, exec)] for the configured commands that do
    preflight_commands(root) [(what, cmd, folder)] the preflight runs (configured, else derived)
    worktree_fit(root)       {ok, reason, service}: can a worktree flow run its tests?
    exec_prefix(service, f)  `docker compose [-f f] exec -T <service>`, the prefix init writes for a compose service
    wrap(cmds, prefix)       the backend commands (WRAP_KEYS) not in a container yet, run through the prefix
    devcontainer(root)       {prefix, source}: how a command runs in the project's dev container (.devcontainer)
    boxed(cmd)               does the command already go through docker, compose or the devcontainer CLI?
    wrap_one(cmd, prefix)    one command run through the prefix (a shell line through `sh -c`)
    cleanup_worktree(path)   [ids]: removes the containers docker compose made from a worktree
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import yaml

from .. import rules

# the options of `docker exec`, `docker compose exec` and `docker compose run` that take a value
VALUE_OPTS = {"-u", "--user", "-w", "--workdir", "-e", "--env", "--index", "--entrypoint", "-v", "--volume", "--name",
              "--env-file", "-l", "--label", "-p", "--publish", "--detach-keys"}
GLOBAL_VALUE_OPTS = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory",
                     "--context", "-c", "--host", "-H", "--log-level", "--ansi", "--progress", "--parallel"}
SHELLS = {"sh", "bash", "ash", "zsh", "dash"}
INTERPRETERS = {"php", "python", "python3", "node", "ruby", "npx", "pnpm", "yarn", "uv", "poetry", "pipenv"}
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml",
                 "compose.override.yaml", "compose.override.yml", "docker-compose.override.yaml", "docker-compose.override.yml")
# the configured commands whose container tells where the project's tests and tools run, most telling first
TEST_KEYS = ("api_test_module", "unit_tests", "web_test_module", "api_test_ac", "web_test_ac", "api_test_paths",
             "web_test_paths", "deps_api", "deps_web", "api_compile", "web_typecheck")


# the backend commands that run with the project's toolchain: init runs them in the compose service the person chose
WRAP_KEYS = ("api_test_module", "api_test_ac", "api_test_pkg", "api_test_paths", "unit_tests", "deps_api", "api_compile",
             "static_checks", "coverage_api", "migrate", "testcontainers_test")
DEFAULT_COMPOSE = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


def exec_prefix(service: str, compose_file: str | None = None) -> str:
    """`docker compose exec -T app`: -T so it runs without a terminal (keel's commands have none); `-f <file>` when the
    compose file is not one docker compose finds by itself."""
    f = f" -f {shlex.quote(compose_file)}" if compose_file and compose_file not in DEFAULT_COMPOSE else ""
    return f"docker compose{f} exec -T {shlex.quote(service)}"


def wrap(cmds: dict, prefix: str) -> dict:
    """{key: command} of the WRAP_KEYS commands that do not run in a container yet, each run through `prefix`."""
    return {k: f"{prefix} {cmds[k]}" for k in WRAP_KEYS
            if isinstance(cmds.get(k), str) and cmds[k].strip() and not boxed(cmds[k])}


# the first words of a command that starts or reaches a container itself: it runs on keel's side as it is
BOXED_TOOLS = {"docker", "docker-compose", "podman", "podman-compose", "devcontainer"}
_SHELL_LINE = re.compile(r"&&|\|\||[;|<>`$]")


def boxed(cmd: str | None) -> bool:
    """Does the command run in (or manage) a container already: docker / compose exec, `devcontainer exec`, or a
    command whose first word is a container tool (`docker compose up -d db`)?"""
    if exec_of(cmd):
        return True
    try:
        tokens = shlex.split(str(cmd or ""))
    except ValueError:
        return False
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if tokens[:1] == ["env"]:
        tokens = [t for t in tokens[1:] if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)]
    return bool(tokens) and Path(tokens[0]).name in BOXED_TOOLS


def wrap_one(cmd: str, prefix: str) -> str:
    """`cmd` run through `prefix`; a line with shell syntax (&&, pipes, $VAR) goes through `sh -c` inside, so all of it
    runs in the container and not only its first part. A command that is boxed already stays as it is."""
    if boxed(cmd):
        return cmd
    return f"{prefix} sh -c {shlex.quote(cmd)}" if _SHELL_LINE.search(cmd) else f"{prefix} {cmd}"


DEVCONTAINER_FILES = (".devcontainer/devcontainer.json", ".devcontainer.json")
_JSONC_COMMENT = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.S)
_TRAILING_COMMA = re.compile(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])')


def _jsonc(text: str) -> dict | None:
    """devcontainer.json is JSON with comments and trailing commas."""
    import json

    text = _JSONC_COMMENT.sub(lambda m: m.group(1) or "", text)
    text = _TRAILING_COMMA.sub(lambda m: m.group(1) or m.group(2), text)
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def devcontainer(root: str) -> dict | None:
    """{prefix, source} for a project whose tools run in its dev container: a devcontainer.json on a compose service
    runs commands through `docker compose -f <its files> exec -T <service>`; any other dev container through the
    devcontainer CLI (`devcontainer exec --workspace-folder .`). None without a devcontainer.json."""
    for rel in DEVCONTAINER_FILES:
        f = Path(root) / rel
        if not f.is_file():
            continue
        try:
            cfg = _jsonc(f.read_text(errors="replace")) or {}
        except OSError:
            continue
        files = cfg.get("dockerComposeFile")
        files = [files] if isinstance(files, str) else [x for x in files or [] if isinstance(x, str)]
        svc = cfg.get("service")
        if files and isinstance(svc, str) and svc.strip():
            base = f.parent.relative_to(Path(root))
            opts = "".join(f" -f {shlex.quote(os.path.normpath((base / x).as_posix()))}" for x in files)
            return {"prefix": f"docker compose{opts} exec -T {shlex.quote(svc.strip())}", "source": rel}
        return {"prefix": "devcontainer exec --workspace-folder .", "source": rel}
    return None


def exec_of(cmd: str | None) -> dict | None:
    """The container part of a command: `docker compose -f x.yml exec -T app php vendor/bin/phpunit` ->
    {prefix: "docker compose -f x.yml exec -T app", service: "app", compose: True, files: ["x.yml"],
    rest: ["php", "vendor/bin/phpunit"]}. None when the command does not run in a container."""
    try:
        tokens = shlex.split(str(cmd or ""))
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok not in ("docker", "docker-compose"):
            continue
        j = i + 1
        compose = tok == "docker-compose"
        files: list[str] = []
        if not compose and j < len(tokens) and tokens[j] == "compose":
            compose, j = True, j + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            opt = tokens[j]
            name = opt.split("=", 1)[0]
            if "=" not in opt and name in GLOBAL_VALUE_OPTS and j + 1 < len(tokens):
                if name in ("-f", "--file"):
                    files.append(tokens[j + 1])
                j += 2
            else:
                if name in ("-f", "--file") and "=" in opt:
                    files.append(opt.split("=", 1)[1])
                j += 1
        if not compose and j < len(tokens) and tokens[j] == "container":
            j += 1
        if j >= len(tokens) or tokens[j] not in (("exec", "run") if compose else ("exec",)):
            continue
        j += 1
        while j < len(tokens) and tokens[j].startswith("-"):
            name = tokens[j].split("=", 1)[0]
            j += 2 if ("=" not in tokens[j] and name in VALUE_OPTS) else 1
        if j >= len(tokens):
            return None
        rest = []
        for t in tokens[j + 1:]:
            if t in ("&&", "||", ";", "|"):
                break
            rest.append(t)
        return {"prefix": shlex.join(tokens[i:j + 1]), "service": tokens[j], "compose": compose, "files": files,
                "rest": rest}
    return None


def _commands(root: str) -> dict:
    if not (Path(root) / ".keel" / "config.yml").is_file():
        return {}
    cmds = rules.load_config(root).get("commands") or {}
    return cmds if isinstance(cmds, dict) else {}


def container_commands(root: str) -> list[tuple[str, str, dict]]:
    """[(key, command, exec_of)] for each configured command that runs in a container, TEST_KEYS first."""
    cmds = _commands(root)
    keys = [k for k in TEST_KEYS if k in cmds] + sorted(k for k in cmds if k not in TEST_KEYS)
    out = []
    for k in keys:
        if isinstance(cmds.get(k), str):
            ex = exec_of(cmds[k])
            if ex:
                out.append((k, cmds[k], ex))
    return out


def in_container(root: str) -> bool:
    """Do the project's tests run in a container (a test command goes through docker exec / compose exec)?"""
    return any(k in TEST_KEYS for k, _c, _e in container_commands(root))


def _check_of(ex: dict) -> str:
    """The cheap check for one exec: the runner's --version through the same prefix (`... app php vendor/bin/phpunit
    --version`), or `true` for a shell command."""
    rest = ex["rest"]
    if not rest or rest[0] in SHELLS:
        return f"{ex['prefix']} true"
    take = 1
    if rest[0] in INTERPRETERS and len(rest) > 1 and not rest[1].startswith("-"):
        take = 3 if rest[1] in ("exec", "run", "x") and len(rest) > 2 else 2
    elif rest[0] == "bundle" and len(rest) > 2 and rest[1] == "exec":
        take = 3
    return f"{ex['prefix']} {shlex.join(rest[:take])} --version"


def preflight_commands(root: str) -> list[tuple[str, str, str]]:
    """[(what, command, folder relative to the root)]: commands.preflight (a command or a list), else one derived check
    per container the test commands use. [] when there is nothing to check."""
    from ..tools import testcmd

    cmds = _commands(root)
    given = cmds.get("preflight")
    if given:
        items = given if isinstance(given, list) else [given]
        return [("commands.preflight", str(c), "") for c in items if str(c).strip()]
    out, seen = [], set()
    for key, _cmd, ex in container_commands(root):
        if key not in TEST_KEYS[:7] or ex["service"] in seen:
            continue
        seen.add(ex["service"])
        out.append((f"the container {ex['service']} (from commands.{key})", _check_of(ex), testcmd.dir_for_key(root, key)))
    return out


# ------------------------------------------------------------------ worktrees

def compose_files(root: str, named: list[str] | None = None) -> list[Path]:
    r = Path(root)
    out = [r / f for f in named or [] if (r / f).is_file()]
    return out + [r / f for f in COMPOSE_FILES if (r / f).is_file() and r / f not in out]


def _binds(service: dict, base: Path) -> list[tuple[str, str, int]]:
    """[(absolute source, target, line)] of a compose service's bind mounts (named volumes left out)."""
    out = []
    for v in service.get("volumes") or []:
        if isinstance(v, str):
            parts = v.split(":")
            if len(parts) < 2:
                continue
            src, dst = parts[0], parts[1]
        elif isinstance(v, dict) and v.get("type", "bind") == "bind":
            src, dst = str(v.get("source") or ""), str(v.get("target") or "")
        else:
            continue
        src = src.replace("${PWD}", str(base)).replace("$PWD", str(base))
        if not (src.startswith((".", "/", "~", "$"))):
            continue                    # a named volume
        if src.startswith("$"):
            continue                    # a variable keel cannot resolve: not a mount it can reason about
        path = Path(os.path.expanduser(src))
        out.append((os.path.normpath(str(path if path.is_absolute() else base / path)), dst, 0))
    return out


def service_mounts(root: str, service: str, named: list[str] | None = None) -> tuple[list[tuple[str, str]], Path | None]:
    """([(source, target)], the compose file that defines the service) of a service's bind mounts."""
    found: Path | None = None
    mounts: list[tuple[str, str]] = []
    for f in compose_files(root, named):
        try:
            doc = yaml.safe_load(f.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        svc = ((doc.get("services") or {}) if isinstance(doc, dict) else {}).get(service)
        if isinstance(svc, dict):
            found = found or f
            mounts += [(s, t) for s, t, _l in _binds(svc, f.parent)]
    return mounts, found


def worktree_fit(root: str) -> dict:
    """Can a flow in a worktree (.keel/worktrees/<name>) run this project's tests? {ok, reason, service, file}.

    ok True: the tests do not run in a container, or the container mounts keel's worktrees folder itself. ok False: the
    test commands exec into a container whose mounts give it only the project folder (or keel cannot tell what it
    mounts): its commands run on the project folder's code, so a worktree flow would test the wrong copy."""
    execs = [(k, c, e) for k, c, e in container_commands(root) if k in TEST_KEYS]
    if not execs:
        return {"ok": True, "reason": "", "service": None}
    _key, _cmd, ex = execs[0]
    svc = ex["service"]
    mounts, file = service_mounts(root, svc, ex["files"]) if ex["compose"] else ([], None)
    wt = os.path.realpath(str(Path(root) / ".keel" / "worktrees"))
    if any(os.path.realpath(src) == wt or os.path.realpath(src).startswith(wt + os.sep) for src, _t in mounts):
        return {"ok": True, "reason": "", "service": svc, "file": str(file.name) if file else None}
    return {"ok": False, "service": svc, "file": str(file.name) if file else None,
            "reason": f"tests run in the container `{svc}`, which sees only the project folder"}


def start_check(root: str) -> dict:
    """What a flow start should know (the api's Start a flow and its own start): {worktree: worktree_fit, base: the
    local base's drift from its remote or None}. Never raises; nothing is fetched (it answers at once)."""
    out: dict = {"worktree": {"ok": True, "reason": "", "service": None}, "base": None}
    try:
        out["worktree"] = worktree_fit(root)
    except Exception:            # noqa: BLE001
        pass
    try:
        from . import verdict_actions

        out["base"] = verdict_actions.base_drift(root, rules.load_config(root))
    except Exception:            # noqa: BLE001
        pass
    return out


# ------------------------------------------------------------------ a worktree's own containers

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_DIR = "com.docker.compose.project.working_dir"


def _docker(args: list[str], timeout: int = 30) -> tuple[int, str]:
    import shutil
    import subprocess

    exe = shutil.which("docker")
    if not exe:
        return 127, ""
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, r.stdout or ""


def worktree_containers(path: str) -> list[tuple[str, str]]:
    """[(container id, compose project)] of the containers docker compose made from a folder in this worktree (their
    working_dir label is the worktree or a folder in it). The project folder's own containers are never among them."""
    if not rules.in_keel_worktree(path):
        return []
    mine = {os.path.normpath(str(path)), os.path.realpath(str(path))}
    code, out = _docker(["ps", "-a", "--filter", f"label={COMPOSE_PROJECT}",
                         "--format", f'{{{{.ID}}}}\t{{{{.Label "{COMPOSE_PROJECT}"}}}}\t{{{{.Label "{COMPOSE_DIR}"}}}}'])
    if code != 0:
        return []
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].strip():
            continue
        cid, project, wd = parts[0].strip(), parts[1].strip(), os.path.normpath(parts[2].strip() or "/")
        if any(wd == m or wd.startswith(m + os.sep) for m in mine):
            found.append((cid, project))
    return found


def cleanup_worktree(path: str) -> list[str]:
    """0.18.57 a flow's worktree containers go when the flow ends (and when its worktree is removed): the containers
    docker compose made from the worktree (`up`, `run` and their dependencies), then their compose project's networks.
    Volumes stay (they may hold data). Returns the ids removed; [] when there are none or docker is not there."""
    found = worktree_containers(path)
    if not found:
        return []
    ids = [cid for cid, _p in found]
    code, _out = _docker(["rm", "-f", "-v", *ids], timeout=120)
    if code != 0:
        return []
    for project in sorted({p for _c, p in found if p}):
        _c, nets = _docker(["network", "ls", "-q", "--filter", f"label={COMPOSE_PROJECT}={project}"])
        net_ids = [n.strip() for n in nets.splitlines() if n.strip()]
        if net_ids:
            _docker(["network", "rm", *net_ids])
    return ids
