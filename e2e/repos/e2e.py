#!/usr/bin/env python3
"""Step 5 end to end (docs/plugins/16-steps5-7-contract.md): plugins in their own repos, and an image that takes them
from plugins.lock. Local only: no GitHub, no push, no real key. Only throw-away names: the container keel-s5 on
127.0.0.1:8093 with the volume keel-s5-data, the images keel-v2:s5-*.

    python3 e2e/repos/e2e.py [--keep] [--no-docker] [--baseline fca8ec9]

It needs git (with subtree), bash, uv, Java 21, Node 20 (the plugins' CI builds), Docker, curl.

  1 repos      docs/plugins/tools/split-plugin.sh makes the repos of tasks, wiki and jira (jira needs tasks) with their
               history, and the template repo; keel.ref is this checkout's HEAD (commit first: CI builds HEAD)
  2 CI         each repo's .github/workflows/ci.yml and release.yml run here, step by step, as GitHub would run them:
               `uses: actions/checkout` clones this checkout (keel-v2) or the plugin repo at the ref, the setup actions
               only check the tool is there, `run:` steps run with bash -eo pipefail, the ${{ }} values in env/with,
               GITHUB_OUTPUT. The one difference: KEEL_PLUGIN_REPOS points to the local repos (keel-studio's place).
               A stand-in gh keeps each release in a folder. The signing key is a test key (keel-plugin keygen).
               jira's CI takes tasks from tasks' repo at its tag v1.0.0. The release refuses a wrong tag and a
               missing key. The template's CI and release (with its own key) run too.
  3 lock       the releases served by a local HTTP server; scripts/fetch-locked-plugins.sh against them
  4 image      docker build PLUGINS=lock with a test lock and the test key (--build-context plugins-lock=…), a
               throw-away keel-s5: /api/plugin-host lists tasks, wiki and jira from the lock, their web parts load
  5 unchanged  the plugins stage of a normal build (PLUGINS=source) holds the same files as the Dockerfile of
               --baseline (before step 5)
Without --keep the container, the volume, the s5 images and the temp folder are removed at the end.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/keel-plugin"))
from keel_plugin import miniyaml  # noqa: E402  (stdlib only: no cryptography needed here)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import keelauth  # noqa: E402  (e2e/keelauth.py: keel's sign-in, v0.18.1)
import lib  # noqa: E402  (e2e/lib.py: a free port and unique names)
E = lib.Env.make("repos")  # keel-e2e-repos-<random> and a free port: never the fixed keel-s5 / 8093
NAME, VOLUME, PORT = E.name, E.volume, E.port
IMAGE_LOCK, IMAGE_SRC_NEW, IMAGE_SRC_OLD = "keel-v2:s5-lock", "keel-v2:s5-src-new", "keel-v2:s5-src-old"
PLUGINS = ["tasks", "wiki", "jira"]
KEELBOT = "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>"
CHECKS: list[str] = []
KP = ["uv", "run", "--quiet", "--project", str(ROOT / "tools/keel-plugin"), "keel-plugin"]
T0 = time.time()

FAKE_GH = r"""#!/usr/bin/env bash
# a stand-in for gh in the release workflows: a release is a folder $S5_RELEASES/<repo>/<tag>/
set -euo pipefail
echo "gh $*" >> "$S5_RELEASES/gh.log"
[ -n "${GH_TOKEN:-}" ] || { echo "gh: GH_TOKEN is not set" >&2; exit 4; }
cmd="$1 $2"; tag="$3"; shift 3
files=() repo=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo="${2#*/}"; shift ;;
    --title | --notes) shift ;;
    --*) ;;
    *) files+=("$1") ;;
  esac
  shift
done
dir="$S5_RELEASES/$repo/$tag"
case "$cmd" in
  "release view") [ -d "$dir" ] ;;
  "release create") [ ! -d "$dir" ] || { echo "release $tag exists" >&2; exit 1; }; mkdir -p "$dir"; cp "${files[@]}" "$dir/" ;;
  "release upload") [ -d "$dir" ]; cp "${files[@]}" "$dir/" ;;
  *) echo "gh: not in this stand-in: $cmd" >&2; exit 3 ;;
esac
"""


def log(text: str) -> None:
    print(f"[{time.time() - T0:6.0f}s] {text}", flush=True)


def check(ok: bool, what: str) -> None:
    if not ok:
        raise AssertionError(f"FAILED: {what}")
    CHECKS.append(what)
    log(f"ok   {what}")


def sh(*args, cwd=None, env=None, check_rc=True, timeout=None) -> str:
    r = subprocess.run([str(a) for a in args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"{' '.join(str(a) for a in args[:6])} … failed ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    return r.stdout + r.stderr


def git(repo: Path, *args) -> str:
    return sh("git", "-C", repo, *args).strip()


# ---------------------------------------------------------------- a small GitHub Actions runner


class Run:
    """One workflow run: its steps' outputs, the log, and where it stopped."""

    def __init__(self, name: str):
        self.name, self.outputs, self.failed, self.log_text, self.errors = name, {}, None, "", []

    @property
    def ok(self) -> bool:
        return self.failed is None


class Runner:
    """Runs a workflow file of a plugin repo here, as GitHub's runner would (the parts these workflows use)."""

    def __init__(self, tmp: Path, keel_repo: Path, repos: Path, releases: Path):
        self.tmp, self.keel_repo, self.repos, self.releases = tmp, keel_repo, repos, releases
        self.bin = tmp / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.n = 0

    def expr(self, text: str, ctx: dict) -> str:
        def one(m: re.Match) -> str:
            e = m.group(1).strip()
            cmp = re.fullmatch(r"(.+?)\s*(!=|==)\s*''", e)
            if cmp:
                v = self.value(cmp.group(1).strip(), ctx)
                return str((v != "") if cmp.group(2) == "!=" else (v == "")).lower()
            return self.value(e, ctx)
        return re.sub(r"\$\{\{(.*?)\}\}", one, str(text))

    @staticmethod
    def value(e: str, ctx: dict) -> str:
        parts = e.split(".")
        if parts[0] == "steps" and len(parts) == 4 and parts[2] == "outputs":
            return ctx["outputs"].get(parts[1], {}).get(parts[3], "")
        if parts[0] == "github" and len(parts) == 2:
            return str(ctx["github"][parts[1]])
        if parts[0] == "secrets" and len(parts) == 2:
            return ctx["secrets"].get(parts[1], "")
        raise ValueError(f"an expression this runner does not know: {e}")

    def run(self, repo: Path, workflow: str, *, ref: str, event: str = "push", secrets: dict | None = None) -> Run:
        """Runs .github/workflows/<workflow> of the plugin repo at ref (a branch or a tag) in a fresh workspace."""
        data = miniyaml.loads((repo / ".github/workflows" / workflow).read_text())
        jobs = data["jobs"]
        assert len(jobs) == 1, "one job per workflow here"
        job_name, job = next(iter(jobs.items()))
        self.n += 1
        ws = self.tmp / "ws" / f"{self.n:02d}-{repo.name}-{Path(workflow).stem}"
        ws.mkdir(parents=True)
        run = Run(f"{repo.name} {workflow} @ {ref}")
        is_tag = git(repo, "tag", "--list", ref) == ref
        ctx = {"outputs": run.outputs, "secrets": secrets or {},
               "github": {"ref_name": ref, "repository": f"keel-studio/{repo.name}", "token": "fake-token",
                          "event_name": event, "sha": git(repo, "rev-parse", ref),
                          "ref": f"refs/{'tags' if is_tag else 'heads'}/{ref}"}}
        base_env = {**os.environ, "CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": str(ws),
                    "GITHUB_REF_NAME": ref, "GITHUB_REPOSITORY": ctx["github"]["repository"],
                    "RUNNER_TEMP": str(ws / "_temp"), "GIT_CONFIG_GLOBAL": str(ws / "_temp" / "gitconfig"),
                    "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "S5_RELEASES": str(self.releases),
                    # the one difference from GitHub: keel-studio's plugin repos are local folders here
                    "KEEL_PLUGIN_REPOS": str(self.repos)}
        (ws / "_temp").mkdir()
        (ws / "_temp" / "gitconfig").touch()
        shell = (data.get("defaults") or {}).get("run", {}).get("shell", "bash")
        assert shell == "bash"
        log_file = self.tmp / "logs" / f"{ws.name}.log"
        log_file.parent.mkdir(exist_ok=True)
        log(f"run  {run.name} (job {job_name})")
        with open(log_file, "w") as out:
            for i, step in enumerate(job["steps"], 1):
                title = step.get("name") or step.get("uses") or step.get("run", "").splitlines()[0][:70]
                assert "if" not in step, "this runner does not evaluate if:"
                env = {**base_env, **{k: self.expr(v, ctx) for k, v in (data.get("env") or {}).items()},
                       **{k: self.expr(v, ctx) for k, v in (job.get("env") or {}).items()},
                       **{k: self.expr(v, ctx) for k, v in (step.get("env") or {}).items()}}
                out.write(f"\n##### step {i}: {title}\n")
                out.flush()
                t = time.time()
                try:
                    if "uses" in step:
                        self.action(step, ctx, ws, repo, ref, out)
                        rc = 0
                    else:
                        rc = self.script(step, ws, env, out, run, i)
                except Exception as exc:  # noqa: BLE001 - an action that fails is a failed step
                    out.write(f"{exc}\n")
                    rc = 1
                log(f"     {'ok  ' if rc == 0 else 'FAIL'} {title} ({time.time() - t:.0f}s)")
                if rc != 0:
                    run.failed = title
                    break
        run.log_text = log_file.read_text()
        run.errors = re.findall(r"^::error::(.*)$", run.log_text, flags=re.M)
        run.workspace = ws
        if not run.ok:
            log(f"     the run stopped at '{run.failed}': {log_file}\n" + "\n".join(run.log_text.splitlines()[-25:]))
        return run

    def action(self, step: dict, ctx: dict, ws: Path, repo: Path, ref: str, out) -> None:
        uses, w = step["uses"], {k: self.expr(v, ctx) for k, v in (step.get("with") or {}).items()}
        name = uses.split("@")[0]
        if name == "actions/checkout":
            source, at = repo, ref
            if w.get("repository"):
                if w["repository"] != "MiladNalbandi/keel-v2":
                    raise ValueError(f"checkout of {w['repository']}: this runner knows keel-v2 and the plugin repo")
                source, at = self.keel_repo, w.get("ref") or "HEAD"
            dest = ws / w.get("path", ".")
            out.write(sh("git", "clone", "-q", "--no-checkout", source, dest))
            out.write(sh("git", "-C", dest, "-c", "advice.detachedHead=false", "checkout", "-q", at))
            out.write(f"checked out {source} at {at} into {dest}\n")
        elif name in ("actions/setup-node", "actions/setup-java", "astral-sh/setup-uv"):
            tool = {"actions/setup-node": ["node", "--version"], "actions/setup-java": ["java", "-version"],
                    "astral-sh/setup-uv": ["uv", "--version"]}[name]
            out.write(f"{name}: using this computer's {tool[0]}: {sh(*tool).strip().splitlines()[0]}\n")
        elif name == "actions/upload-artifact":
            src = ws / w["path"]
            dest = self.tmp / "artifacts" / ws.name / w["name"]
            shutil.copytree(src, dest)
            out.write(f"artifact {w['name']}: {sorted(p.name for p in dest.iterdir())}\n")
        else:
            raise ValueError(f"an action this runner does not know: {uses}")

    def script(self, step: dict, ws: Path, env: dict, out, run: Run, i: int) -> int:
        text = step["run"]
        assert "${{" not in text, "expressions belong in env:"
        script = ws / "_temp" / f"step{i}.sh"
        script.write_text(text)
        gh_out = ws / "_temp" / f"output{i}"
        gh_out.touch()
        summary = ws / "_temp" / f"summary{i}"
        env = {**env, "GITHUB_OUTPUT": str(gh_out), "GITHUB_STEP_SUMMARY": str(summary)}
        cwd = ws / step["working-directory"] if step.get("working-directory") else ws
        rc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=cwd, env=env,
                            stdout=out, stderr=subprocess.STDOUT).returncode
        if step.get("id"):
            run.outputs[step["id"]] = dict(line.split("=", 1) for line in gh_out.read_text().splitlines() if "=" in line)
        return rc


# ---------------------------------------------------------------- the local "GitHub": HTTP for the releases


class Server:
    def __init__(self, folder: Path):
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(folder))
        handler.log_message = lambda *a, **k: None
        # 0.0.0.0: the docker build reaches it as host.docker.internal (as e2e/marketplace does)
        self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------- docker (only keel-s5 and keel-v2:s5-*)


def docker_clean(images: bool) -> None:
    sh("docker", "rm", "-f", NAME, f"{NAME}-check", check_rc=False)
    keelauth.forget(NAME)
    sh("docker", "volume", "rm", VOLUME, check_rc=False)
    if images:
        sh("docker", "image", "rm", IMAGE_LOCK, IMAGE_SRC_NEW, IMAGE_SRC_OLD, check_rc=False)


def http_get(path: str, timeout: float = 10) -> tuple[int, bytes]:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=keelauth.headers(NAME))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError):
        return 0, b""


def build_image(args: list[str], what: str, log_dir: Path) -> None:
    f = log_dir / f"docker-{what}.log"
    t = time.time()
    with open(f, "w") as out:
        rc = subprocess.run(["docker", "build", "--progress=plain", *args], cwd=ROOT, stdout=out,
                            stderr=subprocess.STDOUT).returncode
    log(f"     docker build {what}: {'ok' if rc == 0 else 'FAILED'} ({time.time() - t:.0f}s, {f})")
    if rc != 0:
        raise AssertionError(f"docker build {what} failed:\n" + "\n".join(f.read_text().splitlines()[-40:]))


def stage_files(image: str) -> str:
    """'<sha256>  <path>' of every file in the plugins stage's /out."""
    return sh("docker", "run", "--rm", "--name", f"{NAME}-check", "--entrypoint", "sh", image, "-c",
              "cd /out && find . -type f | LC_ALL=C sort | xargs sha256sum")


# ---------------------------------------------------------------- the steps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", action="store_true", help="keep the temp folder, keel-s5 and the s5 images")
    ap.add_argument("--no-docker", action="store_true", help="only the repos, their CI and the lock (no image)")
    ap.add_argument("--baseline", default="fca8ec9", help="the commit whose Dockerfile a normal build must match")
    args = ap.parse_args()
    E.keep = args.keep

    head = git(ROOT, "rev-parse", "HEAD")
    if sh("git", "-C", ROOT, "status", "--porcelain", "--untracked-files=no").strip():
        log("note: this checkout has changes that are not committed; the plugin repos' CI builds HEAD without them")
    tmp = Path(tempfile.mkdtemp(prefix="s5_repos_"))
    repos, releases = tmp / "repos", tmp / "releases"
    repos.mkdir()
    releases.mkdir()
    log(f"temp folder {tmp}; keel-v2 HEAD {head[:12]}")
    server = None
    ok = False
    try:
        # ---- keys: a test publisher key (it stands for the org secret KEEL_PUBLISHER_KEY) and the template's own
        keys = tmp / "keys"
        sh(*KP, "keygen", "keel-test", "--dir", keys)
        sh(*KP, "keygen", "you-test", "--dir", keys)
        keel_secret = (keys / "keel-test.key").read_text().strip()
        you_secret = (keys / "you-test.key").read_text().strip()
        keel_pub = keys / "keel-test.pub"

        # ---- 1 the repos
        split = ROOT / "docs/plugins/tools/split-plugin.sh"
        for name in PLUGINS:
            out = sh("bash", split, name, repos / f"keel-plugin-{name}", "--push")
            r = repos / f"keel-plugin-{name}"
            check("nothing was pushed." in out and git(r, "remote") == "" and "push --force -u origin main" in out,
                  f"split {name}: a local repo, nothing pushed (--push only prints the commands)")
            subjects = git(r, "log", "--format=%s").splitlines()
            check(len(subjects) >= 2 and subjects[0].endswith(f"its own repo, built against keel-v2 {head[:12]}"),
                  f"split {name}: the plugin's history ({len(subjects) - 1} commits) and one commit with the repo files")
            check(git(r, "log", "-1", "--format=%B").strip().endswith(KEELBOT), f"split {name}: the commit ends with KeelBot")
            check((r / "keel.ref").read_text().splitlines()[-1] == head, f"split {name}: keel.ref is HEAD")
        out = sh("bash", split, "--template", repos / "keel-plugin-template")
        check((repos / "keel-plugin-template/keel.ref").read_text().splitlines()[-1] == head,
              "split --template: the template repo, keel.ref is HEAD")
        # a publisher's repo made from the template, with its public key committed
        example = repos / "keel-plugin-example"
        sh("git", "clone", "-q", repos / "keel-plugin-template", example)
        shutil.copy(keys / "you-test.pub", example / ".github/publisher.pub")
        git(example, "add", ".github/publisher.pub")
        git(example, "-c", "user.name=e2e", "-c", "user.email=e2e@localhost", "commit", "-q", "-m", "my public key")

        # ---- 2 CI and releases
        runner = Runner(tmp, ROOT, repos, releases)
        built: dict[str, Path] = {}
        order = ["tasks", "wiki", "jira"]           # jira after tasks' release: its CI takes tasks from the repo
        for name in order:
            r = repos / f"keel-plugin-{name}"
            ci = runner.run(r, "ci.yml", ref="main", event="push")
            check(ci.ok, f"{name}: ci.yml passes (keel's tests and build at keel.ref, pack, lint)")
            art = tmp / "artifacts" / ci.workspace.name / "kplug" / f"{name}-1.0.0.kplug"
            check(art.is_file(), f"{name}: ci.yml's artifact is {art.name}")
            text = ci.log_text
            check("keel-plugin lint" in text and "ok: no problems found" in text, f"{name}: the package passes lint")
            if name == "jira":
                check("tasks 1.0.0: its repo keel-plugin-tasks at v1.0.0" in text,
                      "jira: its CI takes tasks (it needs tasks) from tasks' repo at the release tag v1.0.0")
            if name == "wiki":
                check("== engine tests: scripts/test-plugins.sh wiki" in text and "== api: ./gradlew wikiTest wikiPluginJar" in text
                      and "== web tests: vitest run" in text and "== web: npm run build:plugin -- wiki" in text,
                      "wiki: engine tests, api tests + jar, web tests + build all ran")
            if name == "jira":
                check("engine: no engine/tests" not in text and "== engine tests" not in text,
                      "jira (no engine part, no engine tests): no engine step")
            # a tag that is not the manifest's version, and a missing key: refused before any build
            if name == "tasks":
                git(r, "tag", "v9.9.9")
                bad = runner.run(r, "release.yml", ref="v9.9.9", secrets={"KEEL_PUBLISHER_KEY": keel_secret})
                check(not bad.ok and any("is not this plugin's version" in e for e in bad.errors)
                      and not (releases / "keel-plugin-tasks/v9.9.9").exists(),
                      "release.yml refuses the tag v9.9.9 (keel-plugin.yml says 1.0.0): nothing released")
                git(r, "tag", "v1.0.0")
                nokey = runner.run(r, "release.yml", ref="v1.0.0", secrets={})
                check(not nokey.ok and any("KEEL_PUBLISHER_KEY is not set" in e for e in nokey.errors),
                      "release.yml without the secret KEEL_PUBLISHER_KEY stops at its first step")
            else:
                git(r, "tag", "v1.0.0")
            rel = runner.run(r, "release.yml", ref="v1.0.0", secrets={"KEEL_PUBLISHER_KEY": keel_secret})
            check(rel.ok, f"{name}: release.yml on the tag v1.0.0 builds, signs and releases")
            folder = releases / f"keel-plugin-{name}" / "v1.0.0"
            kplug = folder / f"{name}-1.0.0.kplug"
            check(sorted(p.name for p in folder.iterdir()) == [kplug.name, kplug.name + ".minisig"],
                  f"{name}: the release has {kplug.name} and its .minisig")
            v = sh(*KP, "verify", kplug, "--pub", keel_pub)
            check("ok:" in v, f"{name}: the release's signature verifies with the (test) keel publisher key")
            built[name] = kplug

        ci = runner.run(example, "ci.yml", ref="main")
        check(ci.ok and "web: web/index.js is packed as it is" in ci.log_text and "== api" not in ci.log_text,
              "the template (example: engine, a ready web part, content; no api): ci.yml passes")
        git(example, "tag", "v0.1.0")
        rel = runner.run(example, "release.yml", ref="v0.1.0", secrets={"PUBLISHER_KEY": you_secret})
        ex = releases / "keel-plugin-example/v0.1.0/example-0.1.0.kplug"
        check(rel.ok and ex.is_file() and "ok:" in sh(*KP, "verify", ex, "--pub", keys / "you-test.pub"),
              "the template's release.yml signs with the publisher's own key (PUBLISHER_KEY, .github/publisher.pub)")

        # ---- 3 the lock, served over http
        server = Server(releases)
        lock_lines = []
        for name in PLUGINS:
            sha = sh("shasum", "-a", "256", built[name]).split()[0]
            lock_lines.append(f"{name} 1.0.0 http://{{host}}:{server.port}/keel-plugin-{name}/v1.0.0/{name}-1.0.0.kplug sha256:{sha}")
        local_lock = tmp / "local.lock"
        local_lock.write_text("# the e2e's lock\n" + "\n".join(lines.format(host="127.0.0.1") for lines in lock_lines) + "\n")
        env = {**os.environ, "PLUGINS_LOCK_ALLOW_HTTP": "1"}
        out = sh("bash", ROOT / "scripts/fetch-locked-plugins.sh", local_lock, tmp / "fetched", "--pub", keel_pub, env=env)
        check("3 plugins into" in out and all((tmp / "fetched" / n / "1.0.0/files.sha256").is_file() for n in PLUGINS),
              "fetch-locked-plugins.sh: the three locked releases, checked and unpacked as <name>/<version>/")
        if args.no_docker:
            ok = True
            return 0

        # ---- 4 the image with PLUGINS=lock, and a throw-away keel from it
        docker_clean(images=False)
        ctx = tmp / "lockctx"
        ctx.mkdir()
        (ctx / "plugins.lock").write_text("# the e2e's lock (host.docker.internal: this computer)\n"
                                          + "\n".join(lines.format(host="host.docker.internal") for lines in lock_lines) + "\n")
        (ctx / "content/trust").mkdir(parents=True)
        shutil.copy(keel_pub, ctx / "content/trust/keel.pub")
        extra = ["--add-host=host.docker.internal:host-gateway"] if platform.system() == "Linux" else []
        build_image(["--build-arg", "PLUGINS=lock", "--build-arg", "PLUGINS_LOCK_ALLOW_HTTP=1",
                     "--build-arg", "INSTALL_CLIS=0", "--build-context", f"plugins-lock={ctx}", *extra,
                     "-t", IMAGE_LOCK, "."], "lock", tmp / "logs")
        check(True, "docker build --build-arg PLUGINS=lock (the test lock and test key as the plugins-lock context)")
        # the same lock without keel.pub, and the repo's own (empty) lock: the build stops and says why
        nokey = tmp / "lockctx-nokey"
        nokey.mkdir()
        shutil.copy(ctx / "plugins.lock", nokey / "plugins.lock")
        for what, context, why in (("no keel.pub", f"plugins-lock={nokey}", "content/trust/keel.pub is missing"),
                                   ("the repo's empty lock", None, "locks no plugin")):
            f = tmp / "logs" / f"docker-refused-{what.replace(' ', '-').replace(chr(39), '')}.log"
            cmd = ["docker", "build", "--progress=plain", "--target", "plugins", "--build-arg", "PLUGINS=lock",
                   "--build-arg", "PLUGINS_LOCK_ALLOW_HTTP=1", *(["--build-context", context] if context else []), *extra, "."]
            with open(f, "w") as out:
                rc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT).returncode
            check(rc != 0 and why in f.read_text(), f"docker build PLUGINS=lock with {what}: refused ('{why}')")
        listing = sh("docker", "run", "--rm", "--name", f"{NAME}-check", "--entrypoint", "sh", IMAGE_LOCK, "-c",
                     "cd /opt/keel-v2/plugins && ls -d */*")
        check(sorted(listing.split()) == ["jira/1.0.0", "tasks/1.0.0", "wiki/1.0.0"],
              "the image's /opt/keel-v2/plugins holds exactly the locked plugins: jira, tasks, wiki 1.0.0")
        ws = tmp / "workspace"
        ws.mkdir()
        sh("docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:8080", "-v", f"{VOLUME}:/data",
           "-v", f"{ws}:/workspace", "-e", "KEEL_FAKE=1", IMAGE_LOCK)
        deadline = time.time() + 300
        while http_get("/api/health")[0] != 200:
            if time.time() > deadline:
                raise AssertionError("keel-s5 did not get healthy in 5 minutes:\n" + sh("docker", "logs", "--tail", "60", NAME, check_rc=False))
            time.sleep(3)
        check(True, f"keel-s5 ({IMAGE_LOCK}) is healthy on 127.0.0.1:{PORT}")
        host = json.loads(http_get("/api/plugin-host")[1])
        got = {p["name"]: (p["version"], p["source"]) for p in host["plugins"]}
        log(f"     /api/plugin-host: {json.dumps(host)[:600]}")
        check(got == {"tasks": ("1.1.0", "image"), "wiki": ("1.0.0", "image"), "jira": ("1.0.0", "image")}
              and not host["problems"],
              "/api/plugin-host lists tasks, wiki and jira 1.0.0 from the lock (image plugins), no problems")
        for name in PLUGINS:
            code, body = http_get(f"/plugins/{name}/1.0.0/web/index.js")
            check(code == 200 and len(body) > 100, f"{name}'s web part loads: /plugins/{name}/1.0.0/web/index.js")
        code, _ = http_get("/api/projects")
        check(code == 200, "keel answers: /api/projects")
        docker_clean(images=False)

        # ---- 5 a normal build (PLUGINS=source) puts the same plugins in the plugins stage as before step 5
        old = tmp / "Dockerfile.baseline"
        old.write_text(sh("git", "-C", ROOT, "show", f"{args.baseline}:Dockerfile"))
        build_image(["--target", "plugins", "-t", IMAGE_SRC_NEW, "."], "source-now", tmp / "logs")
        build_image(["-f", str(old), "--target", "plugins", "-t", IMAGE_SRC_OLD, "."], "source-baseline", tmp / "logs")
        now, before = stage_files(IMAGE_SRC_NEW), stage_files(IMAGE_SRC_OLD)
        names = sorted({line.split()[1].split("/")[1] for line in now.splitlines() if line.strip()})
        log(f"     the plugins stage (PLUGINS=source): {', '.join(names)}; {len(now.splitlines())} files")
        check(now == before and len(names) == 11,
              f"PLUGINS=source: the plugins stage has the same {len(names)} plugins and the same files (sha256) as "
              f"the Dockerfile of {args.baseline}")
        ok = True
        return 0
    finally:
        if server:
            server.close()
        if not args.keep:
            if not args.no_docker:
                docker_clean(images=True)
            if ok:
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                log(f"kept the temp folder for a look: {tmp}")
        log(f"{len(CHECKS)} checks passed" + ("" if ok else " before the failure"))


if __name__ == "__main__":
    sys.exit(main())
