"""The pipeline step: the project's own CI jobs, run by keel on CI's versions (keel 0.18.65).

keel's style check and tests run on this machine's runtime; CI may run them on others (PHP 7.4, 8.1 and 8.2, each
with its own framework version from the matrix), where a tool flags code the local run accepted. This step reads the
CI config (runtime/ci_config.py) and runs every job that is not a deploy and needs no secrets:

  with Docker   each run in a throw-away container: the job's own `container:` / `image:`, else the official image for
                its runtime and version (php:<v>-cli, node:<v>, python:<v>, eclipse-temurin:<v>, golang:<v>). keel
                makes the container, copies a temp copy of the flow's worktree into it (`docker cp`: no bind mount at
                all, so vendor/ and node_modules/ stay in the container), starts it, and removes it. Fixed argument
                lists, never a shell string with project text; never the docker socket, never --privileged, never
                the host network; an image not from the CI config and not on keel's official list is refused.
                Docker is there in `keel2 --docker` and native mode (docker_ready()).
  without       the runs whose runtime version is on keel's own shell run there, in a temp copy; every other run is
                "could not run: <runtime> <version> not available here (keel2 --docker runs it in a container)".

Agents never run this: it is engine code in a code step (and keel_ci_run, a write-level MCP tool).

    ci_pipeline   the action: the runs, the report (Markdown) in data.ci_pipeline_report, the `ci` verdict
    ci_report     the last report (read level)

Settings (.keel/config.yml `ci:`): run auto|on|off (auto: when a CI config exists), timeout (minutes per run, 15),
budget (minutes for all runs, 45), max_runs (24), jobs (only these job ids), extensions report|install (PHP: what
setup-php installs; install tries docker-php-ext-install / pecl in the container). A simulated flow (every model
fake) runs CI only with ci.run: on, as the lint step runs only the tools .keel/config.yml declares.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .. import rules
from ..tools import git
from . import ci_config, verdict_actions, verdicts

TIMEOUT_MIN = 15
BUDGET_MIN = 45
TAIL_LINES = 40
OUTPUT_MAX = 60_000
# keel's own images: the official image of a runtime at a version (anything else must come from the CI config)
OFFICIAL = re.compile(r"^(php:[0-9][\w.]*-cli|php:cli|node:[\w.-]+|python:[\w.-]+|eclipse-temurin:[\w.-]+|golang:[\w.-]+)$")
IMAGE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,250}$")
# the PHP extensions pecl builds (the rest: docker-php-ext-install)
PECL = {"pcov", "xdebug", "redis", "apcu", "imagick", "mongodb", "igbinary", "memcached", "swoole", "ds", "ast"}
# setup-php's tools as composer packages (installed with `composer global require` in an official PHP image)
PHP_TOOLS = {"php-cs-fixer": "friendsofphp/php-cs-fixer", "phpcs": "squizlabs/php_codesniffer",
             "phpcbf": "squizlabs/php_codesniffer", "phpstan": "phpstan/phpstan", "psalm": "vimeo/psalm",
             "phpunit": "phpunit/phpunit", "pint": "laravel/pint", "rector": "rector/rector", "phpmd": "phpmd/phpmd",
             "ecs": "symplify/easy-coding-standard", "infection": "infection/infection"}
# what prints a runtime's version on keel's own shell
LOCAL_VERSION = {
    "php": ["php", "-r", "echo PHP_VERSION;"],
    "node": ["node", "-v"],
    "python": ["python3", "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
    "java": ["java", "-version"],
    "go": ["go", "version"],
}
DOCKER_ENV = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
              "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "TMPDIR")
# a copy leaves out what a run builds itself and what is not the project's
COPY_SKIP = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", ".gradle", "target", "build", "dist"}


# ------------------------------------------------------------------ settings and docker

def settings(cfg: dict, params: dict | None = None) -> dict:
    c = cfg.get("ci") if isinstance(cfg.get("ci"), dict) else {}
    c = {**c, **{k: v for k, v in (params or {}).items() if k in ("jobs", "timeout", "budget", "max_runs", "extensions")}}
    mode = str(c.get("run", "auto")).strip().lower()
    mode = {"true": "on", "false": "off", "yes": "on", "no": "off"}.get(mode, mode)

    def num(k, d):
        try:
            return max(1, int(c.get(k) or d))
        except (TypeError, ValueError):
            return d
    jobs = c.get("jobs")
    return {"run": mode if mode in ("auto", "on", "off") else "auto", "timeout": num("timeout", TIMEOUT_MIN),
            "budget": num("budget", BUDGET_MIN), "max_runs": num("max_runs", ci_config.MAX_RUNS),
            "jobs": [str(j) for j in jobs] if isinstance(jobs, list) else [],
            "extensions": "install" if str(c.get("extensions") or "") == "install" else "report"}


def docker_env() -> dict:
    """The docker client's environment: what it needs to reach the engine, nothing of keel's own keys."""
    return {k: v for k, v in os.environ.items() if k in DOCKER_ENV}


def docker_ready() -> tuple[bool, str]:
    """(can keel run containers, why not). KEEL_CI_DOCKER=0 turns it off; else the docker client must reach an engine
    (keel2 --docker mounts the host's socket into keel's container; native mode uses the computer's docker)."""
    if os.environ.get("KEEL_CI_DOCKER", "") == "0":
        return False, "KEEL_CI_DOCKER=0"
    if not shutil.which("docker", path=docker_env().get("PATH")):
        return False, "no docker command here"
    try:
        p = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True,
                           timeout=15, env=docker_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker does not answer ({exc.__class__.__name__})"
    if p.returncode != 0:
        return False, "docker cannot reach an engine (" + ((p.stderr or p.stdout).strip().splitlines() or ["?"])[-1][:120] + ")"
    return True, ""


def official_image(runtime: str, version: str) -> str:
    v = version or "latest"
    if runtime == "php":
        return "php:cli" if v == "latest" else f"php:{v}-cli"
    return {"node": f"node:{v}", "python": f"python:{v}", "java": f"eclipse-temurin:{v}", "go": f"golang:{v}"}.get(runtime, "")


def image_for(run: dict) -> str:
    if run.get("image"):
        return run["image"]
    return official_image(run.get("runtime") or "", run.get("version") or "") if run.get("runtime") else ""


def image_allowed(image: str, config_images: set[str]) -> bool:
    """Only an image the CI config names, or keel's official image for a runtime; never one that reads as an option."""
    return bool(IMAGE_OK.match(image)) and (image in config_images or bool(OFFICIAL.match(image)))


# ------------------------------------------------------------------ the temp copy and the scripts

def temp_copy(root: str) -> str:
    """A throw-away copy of the worktree: the files git tracks plus the new ones it does not ignore (vendor/ and
    node_modules/ stay out: a run installs its own). Without git, the folder minus COPY_SKIP."""
    dest = tempfile.mkdtemp(prefix="keel-ci-")
    src = Path(root)
    files: list[str] = []
    if git.is_repo(root):
        out = git.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
        files = [f for f in out.split("\0") if f and not f.startswith(".keel/worktrees/")]
    else:
        for p in src.rglob("*"):
            rel = p.relative_to(src)
            if p.is_file() and not (set(rel.parts) & COPY_SKIP):
                files.append(rel.as_posix())
    for rel in files:
        s = src / rel
        if not s.is_file() or s.is_symlink() and not s.resolve().is_relative_to(src.resolve()):
            continue
        d = Path(dest) / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(s, d)
        except OSError:
            pass
    # a fresh repository of the copy: CI steps like `git diff --exit-code` after a formatter need one (the worktree's
    # own .git points into the project and stays out); keel's scripts are excluded from it
    snap = ["-c", "user.name=keel", "-c", "user.email=keel@localhost", "-c", "commit.gpgsign=false"]
    if git.git(dest, "init", "-q").returncode == 0:
        (Path(dest) / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (Path(dest) / ".git" / "info" / "exclude").write_text(".keel-ci/\n")
        git.git(dest, "add", "-A")
        git.git(dest, *snap, "commit", "-q", "--no-verify", "-m", "keel CI copy")
    return dest


def _q(v: str) -> str:
    return shlex.quote(str(v))


def script(run: dict, nonce: str, idx: int, docker: bool, ext_mode: str) -> tuple[str, dict[str, str]]:
    """(the driver script, {file name: step text}). The driver exports the job's env, prepares an official image
    (composer, setup-php's tools, its extensions) and runs each step in its folder with bash (-eo pipefail) or sh -e,
    between marker lines (with a nonce the project's output cannot guess) that say which step failed."""
    m = f"@@keel-{nonce}"
    lines = ["#!/bin/sh", f"# keel 0.18.65: the CI job {run['name']!r} from {run['source']}, run by keel in a throw-away copy",
             'W="${KEEL_CI_WORK:-/work}"', f"M={_q(m)}", "export CI=true"]
    if run["source"].startswith(".github/"):
        lines.append('export GITHUB_ACTIONS=true GITHUB_WORKSPACE="$W" RUNNER_OS=Linux')
    else:
        lines.append('export GITLAB_CI=true CI_PROJECT_DIR="$W"')
    for k, v in (run.get("env") or {}).items():
        if ci_config.ENV_NAME.match(k):
            lines.append(f"export {k}={_q(v)}")
    lines += ['SH="sh -e"', 'command -v bash >/dev/null 2>&1 && SH="bash --noprofile --norc -eo pipefail"',
              'echo "$M setup"']
    if docker and run.get("image_from") != "config":
        lines += _prelude(run, ext_mode)
    files: dict[str, str] = {}
    for n, step in enumerate(run.get("steps") or [], 1):
        name = f"run-{idx}-step-{n}.sh"
        files[name] = str(step.get("run") or "") + "\n"
        env = "".join(f" {k}={_q(v)}" for k, v in (step.get("env") or {}).items() if ci_config.ENV_NAME.match(k))
        shell = {"bash": "bash --noprofile --norc -eo pipefail", "sh": "sh -e", "python": "python3"}.get(
            (step.get("shell") or "").split()[0] if step.get("shell") else "", "$SH")
        d = str(step.get("dir") or ".").strip() or "."
        d = "." if d.startswith("/") or ".." in Path(d).parts else d
        lines += [f'echo "$M step {n} start"',
                  f'(cd "$W"/{_q(d)} && env{env} {shell} "$W/.keel-ci/{name}"); code=$?',
                  f'echo "$M step {n} exit $code"']
        if not step.get("allow_fail"):
            lines.append('[ "$code" = 0 ] || exit "$code"')
    lines.append('echo "$M done"')
    return "\n".join(lines) + "\n", files


def _prelude(run: dict, ext_mode: str) -> list[str]:
    """An official image has the runtime only: composer (and git, unzip) for PHP when a step names it or setup-php
    lists tools, the tools themselves, and a check of setup-php's extensions (installed only with ci.extensions:
    install)."""
    if run.get("runtime") != "php":
        return []
    out: list[str] = []
    text = "\n".join(s.get("run") or "" for s in run.get("steps") or [])
    tools = [t for t in run.get("tools") or [] if t]
    comp = next((t for t in tools if t.split(":")[0] == "composer"), "")
    others = [t for t in tools if t.split(":")[0] != "composer"]
    if "composer" in text or tools:
        major = {"v1": "--1", "1": "--1", "v2": "--2", "2": "--2"}.get(comp.split(":", 1)[1] if ":" in comp else "", "")
        out += ["export COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_HOME=/tmp/keel-composer COMPOSER_NO_INTERACTION=1",
                'export PATH="$PATH:/tmp/keel-composer/vendor/bin"',
                "if ! command -v git >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then",
                "  command -v apt-get >/dev/null 2>&1 && { apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq "
                "--no-install-recommends git unzip >/dev/null 2>&1 || echo \"$M note git and unzip could not be installed\"; }",
                "  command -v apk >/dev/null 2>&1 && apk add --no-cache -q git unzip >/dev/null 2>&1",
                "fi",
                "if ! command -v composer >/dev/null 2>&1; then",
                "  php -r \"copy('https://getcomposer.org/installer', '/tmp/composer-setup.php');\" && "
                f"php /tmp/composer-setup.php --quiet {major} --install-dir=/usr/local/bin --filename=composer "
                "|| echo \"$M note composer could not be installed\"",
                "fi"]
    for t in others:
        name, _, ver = t.partition(":")
        name = re.sub(r"[^\w.-]", "", name)[:60]
        pkg = PHP_TOOLS.get(name)
        if not pkg:
            out.append(f'echo "$M note tool {name} from setup-php is not installed here"')
            continue
        want = (f"^{ver}" if re.fullmatch(r"\d+", ver) else f"{ver}.*" if re.fullmatch(r"\d+\.\d+", ver) else ver) if ver and ver != "latest" else ""
        out.append(f"composer global require --quiet {_q(pkg + (':' + want if want else ''))} >/dev/null 2>&1 "
                   f"|| echo \"$M note tool {name} could not be installed\"")
    for e in run.get("extensions") or []:
        e = re.sub(r"[^A-Za-z0-9_]", "", re.split(r"-(?=\d)", e)[0].replace("-", "_"))    # "redis-5.3.7" -> redis
        if not e:
            continue
        if ext_mode == "install":
            how = (f"pecl install {e} >/dev/null 2>&1 && docker-php-ext-enable {e} >/dev/null 2>&1" if e.lower() in PECL
                   else f"docker-php-ext-install {e} >/dev/null 2>&1")
            out.append(f'php -m | grep -qix {e} || {{ {how} || echo "$M note extension {e} not available"; }}')
        else:
            out.append(f'php -m | grep -qix {e} || echo "$M note extension {e} not available"')
    return out


# ------------------------------------------------------------------ running

def _docker(args: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(["docker", *args], capture_output=True, text=True, errors="replace", timeout=timeout,
                           env=docker_env())
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return 124, out + f"\ntimed out after {timeout}s"
    except OSError as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_in_docker(run: dict, copy: str, idx: int, image: str, timeout: int) -> tuple[int, str]:
    """docker create (the job's script as its command) -> docker cp the copy to /work -> docker start -a, at most
    `timeout` seconds -> docker rm -f. Nothing of the host is mounted."""
    name = f"keel-ci-{uuid.uuid4().hex[:12]}"
    create = ["create", "--name", name, "--label", "keel.ci=1", "--workdir", "/work",
              "--security-opt", "no-new-privileges", "--pids-limit", "4096", "--network", "bridge",
              "--entrypoint", "/bin/sh", image, f"/work/.keel-ci/run-{idx}.sh"]
    try:
        code, out = _docker(create, max(60, min(timeout, 600)))     # it pulls the image when it is not here yet
        if code != 0:
            return -1, "could not make the container: " + out.strip()[-1500:]
        code, out = _docker(["cp", f"{copy}/.", f"{name}:/work"], 300)
        if code != 0:
            return -1, "could not copy the project into the container: " + out.strip()[-1500:]
        return _docker(["start", "--attach", name], timeout)
    finally:
        _docker(["rm", "--force", "--volumes", name], 60)


def run_local(run: dict, root: str, idx: int, nonce: str, ext_mode: str, timeout: int) -> tuple[int, str]:
    """The same script on keel's own shell, in its own temp copy (its installs land there, never in the project)."""
    copy = temp_copy(root)
    try:
        _write_scripts(copy, [(idx, run)], nonce, False, ext_mode)
        env = {**_local_env(), "KEEL_CI_WORK": copy}
        try:
            p = subprocess.run(["sh", f"{copy}/.keel-ci/run-{idx}.sh"], cwd=copy, capture_output=True, text=True,
                               errors="replace", timeout=timeout, env=env, start_new_session=True)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            return 124, (out or "") + f"\ntimed out after {timeout}s"
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def _local_env() -> dict:
    from ..tools.agent_tools import command_env
    return command_env()


def _write_scripts(copy: str, runs: list[tuple[int, dict]], nonce: str, docker: bool, ext_mode: str) -> None:
    d = Path(copy) / ".keel-ci"
    d.mkdir(parents=True, exist_ok=True)
    for idx, run in runs:
        text, files = script(run, nonce, idx, docker, ext_mode)
        (d / f"run-{idx}.sh").write_text(text)
        for name, body in files.items():
            (d / name).write_text(body)


def local_version(runtime: str) -> str:
    """The runtime's version on keel's own shell ("" when it is not there)."""
    argv = LOCAL_VERSION.get(runtime)
    if not argv:
        return ""
    for cmd in ([argv, ["python", *argv[1:]]] if runtime == "python" else [argv]):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=_local_env())
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"(\d+(?:\.\d+)+|\b\d+\b)", (p.stdout or "") + (p.stderr or ""))
        if p.returncode == 0 and m:
            return m.group(1)
    return ""


def version_fits(want: str, have: str) -> bool:
    """CI's "7.4" is met by 7.4.33, "3" by 3.12.1; "lts" / "latest" / "stable" by any version (said in the notes)."""
    if not have:
        return False
    if not want or want in ("lts", "latest", "stable", "current", "node"):
        return True
    w, h = want.split("."), have.split(".")
    return h[:len(w)] == w


def outcome(code: int, out: str, nonce: str, steps: list[dict]) -> dict:
    """{result, step, cmd, code, tail, notes} from the run's output and its marker lines."""
    m = f"@@keel-{nonce}"
    notes = sorted({ln.split(" note ", 1)[1].strip() for ln in out.splitlines() if ln.startswith(m + " note ")})
    segs: dict[int, list[str]] = {}
    exits: dict[int, int] = {}
    cur = 0
    for ln in out.splitlines():
        if ln.startswith(m + " "):
            parts = ln[len(m) + 1:].split()
            if parts[:1] == ["step"] and len(parts) >= 3 and parts[1].isdigit():
                cur = int(parts[1])
                if parts[2] == "exit" and len(parts) >= 4 and parts[3].lstrip("-").isdigit():
                    exits[cur] = int(parts[3])
            continue
        segs.setdefault(cur, []).append(ln)
    if code == 0:
        return {"result": "passed", "code": 0, "notes": notes, "tail": "", "step": None, "cmd": ""}
    failed = next((n for n, c in exits.items() if c != 0 and not (steps[n - 1] if n - 1 < len(steps) else {}).get("allow_fail")), None)
    lines = segs.get(failed if failed is not None else cur, []) or out.splitlines()
    tail = "\n".join(ln for ln in lines if not ln.startswith(m))
    tail = "\n".join(tail.splitlines()[-TAIL_LINES:])
    st = steps[failed - 1] if failed and failed - 1 < len(steps) else None
    timed = code == 124 and "timed out after" in out[-200:]
    return {"result": "failed", "code": exits.get(failed, code) if failed else code, "notes": notes, "tail": tail,
            "step": failed, "cmd": (st.get("run") or "").strip()[:600] if st else "",
            "timed_out": timed, "setup": failed is None and not timed}


def execute(root: str, plan: dict, conf: dict, *, progress=None, emit=None, no_local: str = "") -> dict:
    """Every run of the plan, one after the other, inside the budget. {docker, why, runs: [result]}. `no_local`: why
    no run may go on keel's own shell (the flow changed the CI config: its commands run only in a container)."""
    docker, why = docker_ready()
    config_images = {r["image"] for r in plan["runs"] if r.get("image_from") == "config" and r.get("image")}
    nonce = uuid.uuid4().hex[:16]
    t_end = time.monotonic() + conf["budget"] * 60
    results: list[dict] = []
    todo: list[tuple[int, dict]] = []
    for idx, run in enumerate(plan["runs"], 1):
        res = {**_brief(run), "result": "", "why": "", "ms": 0, "where": "", "notes": list(run.get("notes") or [])}
        if run["skip"]:
            res.update(result="skipped", why=run["skip"])
        elif conf["jobs"] and run["job"] not in conf["jobs"]:
            res.update(result="skipped", why="not in ci.jobs")
        else:
            todo.append((idx, run))
        results.append(res)
    copy = None
    try:
        if docker and any(image_for(r) for _i, r in todo):
            copy = temp_copy(root)
            _write_scripts(copy, [(i, r) for i, r in todo if image_for(r)], nonce, True, conf["extensions"])
        for idx, run in todo:
            res = results[idx - 1]
            left = int(t_end - time.monotonic())
            if left < 30:
                res.update(result="could not run", why=f"the total budget ({conf['budget']} min, ci.budget) was used up")
                continue
            timeout = min(conf["timeout"] * 60, left)
            image = image_for(run)
            label = _label(run)
            if progress:
                _safe(progress, f"▶ CI {label}", tool="ci")
            t0 = time.monotonic()
            if docker and image:
                if not image_allowed(image, config_images):
                    res.update(result="could not run", why=f"the image {image[:80]!r} is not from the CI config or keel's official list")
                    continue
                code, out = run_in_docker(run, copy, idx, image, timeout)
                res["where"] = f"container {image}"
                if code == -1:
                    res.update(result="could not run", why=out.strip().splitlines()[0][:300] if out.strip() else "docker failed",
                               ms=int((time.monotonic() - t0) * 1000), tail="\n".join(out.splitlines()[-TAIL_LINES:]))
                    continue
            else:
                local = (None, no_local) if no_local else _local_reason(run, docker)
                if local[0] is None:
                    res.update(result="could not run", why=local[1])
                    continue
                code, out = run_local(run, root, idx, nonce, conf["extensions"], timeout)
                res["where"] = local[1]
            res["ms"] = int((time.monotonic() - t0) * 1000)
            got = outcome(code, out, nonce, run.get("steps") or [])
            res.update({**got, "notes": res["notes"] + got["notes"]})
            if emit:
                _safe(emit, {"name": f"ci: {label}", "on": "ci", "fail": "block", "kind": "check", "source": run["source"],
                             "ok": res["result"] == "passed", "available": True, "ms": res["ms"], "files": 0,
                             "cmd": res.get("cmd") or run["name"], "dir": ".", "code": res.get("code", 0),
                             "output": _clean(out, nonce)[-OUTPUT_MAX:]})
    finally:
        if copy:
            shutil.rmtree(copy, ignore_errors=True)
    return {"docker": docker, "why": why, "runs": results}


def changed_config(root: str, cfg: dict, sources: list[str], start: str | None) -> list[str]:
    """The CI config files this branch changed (against the base, or not committed yet). An agent may edit them during
    a flow: keel runs such commands only in a container, never on its own shell."""
    if not git.is_repo(root):
        return []
    changed = set(verdict_actions.branch_files(root, verdict_actions.base_ref(root, cfg, start)))
    return [s for s in sources if s in changed]


def _local_reason(run: dict, docker: bool) -> tuple[bool | None, str]:
    """(None, why it cannot run) or (True, where it runs): a run on keel's own shell needs its runtime at CI's version;
    a run that names no runtime and no image uses this machine's tools (as a GitHub runner's would be)."""
    rt, ver = run.get("runtime") or "", run.get("version") or ""
    if not rt and not run.get("image"):
        return True, "this machine (the job names no runtime)"
    if not rt:
        return None, f"the container image {run['image'][:80]} is not available here" + (
            "" if docker else " (keel2 --docker runs it in a container)")
    have = local_version(rt)
    if version_fits(ver, have):
        return True, f"this machine ({rt} {have})"
    shown = f"{rt} {ver}".strip()
    return None, f"{shown} not available here" + (f" (this machine has {have})" if have else "") + \
        ("" if docker else " (keel2 --docker runs it in a container)")


def _safe(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except Exception:         # bookkeeping never changes the result
        pass


def _clean(out: str, nonce: str) -> str:
    return "\n".join(ln for ln in out.splitlines() if not ln.startswith(f"@@keel-{nonce}"))


def _brief(run: dict) -> dict:
    return {k: run.get(k) for k in ("key", "source", "job", "name", "matrix", "runtime", "version", "image", "style")} | \
        {"tools": list(run.get("tools") or [])}


def _label(r: dict) -> str:
    """"cs · PHP 7.4" or "tests · PHP 8.1 · laravel=8.*"."""
    rt = {"php": "PHP", "node": "Node", "python": "Python", "java": "Java", "go": "Go"}.get(r.get("runtime") or "", r.get("runtime") or "")
    parts = [r.get("name") or r.get("job") or "?"]
    if rt:
        parts.append(f"{rt} {r.get('version') or ''}".strip())
    vals = [v for v in _matrix_vals(r) if v.split("=", 1)[1] not in (r.get("name") or "")]
    if vals:
        parts.append(", ".join(vals)[:80])
    return " · ".join(parts)


VERSION_KEYS = {"php", "php-version", "php_version", "php-versions", "node", "node-version", "node_version", "python",
                "python-version", "python_version", "java", "java-version", "jdk", "go", "go-version"}


def _matrix_vals(r: dict) -> list[str]:
    """The matrix values a row shows: the runtime's own version key is in the versions already."""
    return [f"{k}={v}" for k, v in (r.get("matrix") or {}).items()
            if not (k.lower() in VERSION_KEYS and str(v) == str(r.get("version") or ""))]


# ------------------------------------------------------------------ report

def _time(ms: int) -> str:
    if not ms:
        return "—"
    s = ms // 1000
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def counts(runs: list[dict]) -> dict:
    out = {"passed": 0, "failed": 0, "could not run": 0, "skipped": 0}
    for r in runs:
        out[r["result"]] = out.get(r["result"], 0) + 1
    return out


def summary(res: dict) -> str:
    c = counts(res["runs"])
    failed = [_label(r) for r in res["runs"] if r["result"] == "failed"]
    head = (f"{c['failed']} of {len(res['runs'])} run(s) failed ({'; '.join(failed)[:200]})" if failed
            else f"{c['passed']} run(s) passed" if c["passed"] else "no run could run here")
    return f"CI on its own versions: {head}; {c['passed']} passed, {c['could not run']} could not run, {c['skipped']} skipped."


def report(res: dict, plan: dict, style: list[str] | None = None) -> str:
    """The Markdown report: where it ran, one row per run, each failure's command and output tail, the style check."""
    how = ("Docker: each run in a throw-away container on CI's image" if res["docker"] else
           f"No Docker here ({res.get('why') or 'not available'}): only runs whose runtime version is on this machine ran")
    out = ["## CI on its own versions", "",
           f"Read: {', '.join(plan['sources']) or 'no CI config'}. {how}.", "",
           "| job | versions | result | time |", "|---|---|---|---|"]
    for r in res["runs"]:
        vers = " · ".join(x for x in [f"{r['runtime']} {r['version']}".strip() if r.get("runtime") else "",
                                       ", ".join(_matrix_vals(r))[:80]] if x) or "—"
        result = r["result"] + (f": {r['why']}" if r.get("why") else "")
        out.append(f"| {_cell(r['name'])} | {_cell(vers)} | {_cell(result[:220])} | {_time(r.get('ms') or 0)} |")
    out += ["", f"**{summary(res)}**", ""]
    for r in res["runs"]:
        if r["result"] != "failed":
            continue
        what = (f"step {r['step']}, exit {r.get('code')}" if r.get("step") else
                "timed out" if r.get("timed_out") else "the setup keel adds failed (installing composer or a tool)")
        out += [f"### {_label(r)} failed ({what})", ""]
        if r.get("cmd"):
            out += ["```sh", r["cmd"], "```", ""]
        out += ["Output (the end):", "", "```", r.get("tail") or "(no output)", "```", ""]
    if style:
        out += ["### Style check", ""] + [f"- {s}" for s in style] + [""]
    notes = [(r, n) for r in res["runs"] if r["result"] != "skipped" for n in r.get("notes") or []]
    if notes or plan.get("problems"):
        out += ["### Notes", ""] + [f"- {_label(r)}: {n}" for r, n in notes][:30] + [f"- {p}" for p in plan.get("problems") or []] + [""]
    return "\n".join(out).rstrip() + "\n"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def brief(text: str, limit: int = 2500) -> str:
    """The report cut for an agent's item (the ship reviewers get it)."""
    return text if len(text) <= limit else text[:limit] + "\n… (cut; the final review shows all of it)"


# ------------------------------------------------------------------ style: keel's own check vs CI's

STYLE_RUNTIME = {"php-cs-fixer": "php", "phpcs": "php", "phpcbf": "php", "pint": "php", "ecs": "php",
                 "eslint": "node", "prettier": "node", "stylelint": "node",
                 "black": "python", "ruff": "python", "flake8": "python", "isort": "python", "pycodestyle": "python"}
STYLE_TOOL = re.compile(r"(?:^|[\s/])(" + "|".join(re.escape(k) for k in STYLE_RUNTIME) + r")(?=$|[\s])")


def style_check(root: str, cfg: dict, key: str, res: dict) -> list[str]:
    """keel's style check vs CI's style job: which counts, and the versions when they differ ("your style check ran
    on PHP 8.4 with php-cs-fixer v3.64.0; CI runs PHP 7.4")."""
    from . import container, tools as tools_mod

    ci_style = [r for r in res["runs"] if r.get("style") and r["result"] in ("passed", "failed")]
    planned = [r for r in res["runs"] if r.get("style") and r.get("runtime") and r["result"] != "skipped"]
    if not planned:
        return []
    out = []
    for r in ci_style:
        out.append(f"CI's style job {_label(r)} {r['result']} on CI's version: that result counts over keel's own style check.")
    try:
        declared = tools_mod.resolve(root, cfg, include_stacks=True)
    except Exception:
        declared = []
    lint = verdicts.latest(key, "lint")
    ran = set(((lint or {}).get("detail") or {}).get("tools") or {})
    seen = set()
    for t in declared:
        m = STYLE_TOOL.search(" " + t.run)
        if not m or (ran and t.name not in ran):
            continue
        prog_name = m.group(1)
        rt = STYLE_RUNTIME[prog_name]
        if (prog_name, rt) in seen:
            continue
        seen.add((prog_name, rt))
        ci = [r for r in planned if r.get("runtime") == rt]
        if not ci:
            continue
        ex = container.exec_of(t.run)
        words = (ex["rest"] if ex else shlex.split(t.run))
        prog = next((w for w in words if re.search(rf"(^|/){re.escape(prog_name)}$", w)), prog_name)
        prefix = shlex.split(ex["prefix"]) if ex else []
        have = _run_version(root, prefix + LOCAL_VERSION.get(rt, []))
        tool_v = _run_version(root, prefix + [prog, "--version"])
        ci_vers = sorted({r.get("version") or "?" for r in ci})
        if not have and not tool_v:
            continue            # the tool is not here: nothing to compare
        if have and all(version_fits(v, have) for v in ci_vers if v != "?"):
            continue
        rt_name = {"php": "PHP", "node": "Node", "python": "Python"}.get(rt, rt)
        pins = sorted({x for r in ci for x in r.get("tools") or [] if x.split(":")[0] == prog_name})
        out.append(f"your style check ran on {rt_name} {have or '(unknown version)'} with {prog_name} "
                   f"{('v' + tool_v.lstrip('v')) if tool_v else '(unknown version)'}; CI runs {rt_name} {', '.join(ci_vers)}"
                   + (f" ({', '.join(pins)})" if pins else "") + ".")
    return out


def _run_version(root: str, argv: list[str]) -> str:
    if not argv:
        return ""
    try:
        p = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=30, env=_local_env())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"v?(\d+\.\d+(?:\.\d+)?)", (p.stdout or "") + (p.stderr or ""))
    return m.group(1) if p.returncode == 0 and m else ""


# ------------------------------------------------------------------ the actions

def _result(ok, note, detail="", update=None):
    return verdict_actions._result(ok, note, detail, update)


async def ci_pipeline(a):
    return await asyncio.to_thread(_ci_pipeline, a)


def _ci_pipeline(a):
    cfg = rules.load_config(a.root)
    conf = settings(cfg, getattr(a, "params", None) or {})
    data = dict(a.data or {})
    if conf["run"] == "off":
        data["ci_pipeline"] = {"status": "off"}
        return _result(True, "CI pipeline: off (ci.run: off).", update={"data": data})
    plan = ci_config.plan(a.root, conf["max_runs"])
    if not plan["sources"]:
        data["ci_pipeline"] = {"status": "none"}
        if conf["run"] == "on":
            verdict_actions.record_verdict(a.key, "ci", None, {"reason": "no CI config (.github/workflows, .gitlab-ci.yml)"}, root=a.root)
            return _result(None, "CI pipeline not available: no CI config (.github/workflows/*.yml, .gitlab-ci.yml).",
                           update={"data": data})
        return _result(True, "CI pipeline: the project has no CI config.", update={"data": data})
    simulated = a.fake and conf["run"] != "on"      # a simulated flow runs CI only when ci.run says on
    if simulated:
        res = {"docker": False, "why": "a simulated run", "runs": [{**_brief(r), "result": "skipped", "notes": [],
                                                                      "why": r["skip"] or "not run in a simulated flow", "ms": 0}
                                                                     for r in plan["runs"]]}
    else:
        moved = changed_config(a.root, cfg, plan["sources"], a.base)
        no_local = (f"this branch changed the CI config ({', '.join(moved)[:120]}): keel runs it only in a container"
                    if moved else "")
        res = execute(a.root, plan, conf, progress=getattr(a, "progress", None), emit=getattr(a, "emit", None),
                      no_local=no_local)
    style = style_check(a.root, cfg, a.key, res) if not simulated else []
    text = report(res, plan, style)
    c = counts(res["runs"])
    line = summary(res)
    ok = False if c["failed"] else (True if c["passed"] else None)
    compact = [{k: v for k, v in r.items() if k != "tail"} | {"tail": (r.get("tail") or "")[-1500:]} for r in res["runs"]]
    verdict_actions.record_verdict(a.key, "ci", ok, {"summary": line, "report": text[:20000], "docker": res["docker"],
                                                     "runs": compact, "style": style, "sources": plan["sources"],
                                                     **({"reason": line} if ok is None else {})}, root=a.root)
    data["ci_pipeline"] = {"status": "failed" if c["failed"] else "passed" if c["passed"] else "not run", "summary": line,
                           "docker": res["docker"], "counts": c, "style": style}
    data["ci_pipeline_report"] = text
    return _result(ok, line, text, {"data": data})


async def ci_report(a):
    return await asyncio.to_thread(_ci_report, a)


def _ci_report(a):
    v = verdicts.latest(a.key, "ci")
    if not v:
        return _result(True, "No CI pipeline report yet: the ship step `ci` (or keel_ci_run) makes one.")
    d = v.get("detail") or {}
    head, tree = verdicts.stamp(a.root) if git.is_repo(a.root) else (None, None)
    stale = "" if not (head or tree) or verdicts.fresh(v, head, tree) else \
        f" (stale: it ran on {str(v.get('commit') or 'other files')[:7]}, not on HEAD)"
    note = (d.get("summary") or d.get("reason") or "CI pipeline report") + stale
    return _result(True, note[:300], f"{v.get('at')}{stale}\n\n{d.get('report') or d.get('reason') or ''}")


ACTIONS = {"ci_pipeline": ci_pipeline, "ci_report": ci_report}
