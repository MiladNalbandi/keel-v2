"""keel 0.18.22 a stack's setup: block (runtime/stack_setup.py): the pack's validation, the union of setup.apt over the
registered projects, the system plan as a pure function (nothing is ever apt-installed here), each_worktree in a new
flow worktree, the failure gate, init's proposal, the setup check and native mode's message."""

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import decide, start, wait
from keel_engine import rules
from keel_engine.runtime import actions, init_gates, ladder, stack_edit, stack_setup, stacks
from keel_engine.tools import worktrees

PHP_STACK = """\
name: php-symfony
lane: api
detect:
  files: [composer.json]
layers:
  - {id: unit, what: pure PHP, where: tests/Unit}
commands:
  api_test_module: vendor/bin/phpunit
  api_test_ac: "vendor/bin/phpunit --filter '{AC}'"
dependency_files: [composer.json]
"""

MARKER_STACK = """\
name: marker
lane: api
detect:
  files: [pyproject.toml]
layers:
  - {id: unit, what: the code alone, where: tests}
setup:
  each_worktree:
    - %s
"""


@pytest.fixture(autouse=True)
def host_mode(monkeypatch):
    """Every test says where keel runs; nothing here looks at (or installs on) this machine."""
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "host")
    stacks.forget()
    yield
    stacks.forget()


@pytest.fixture(autouse=True)
def approved(monkeypatch):
    """These tests are about WHAT setup does. Since 0.18.33 a project's own stack runs its commands only after a person
    approved them, so here every approval is given; tests/test_stack_setup_approval.py is about the approval itself."""
    monkeypatch.setattr(stack_setup, "is_approved", lambda key: True)


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _project(tmp_path, name, stack_text, files=("composer.json",)):
    root = tmp_path / name
    (root / ".keel" / "stacks").mkdir(parents=True)
    for f in files:
        (root / f).write_text("{}\n")
    (root / ".keel" / "stacks" / "stack.yml").write_text(stack_text)
    # keel's own symfony pack would match composer.json too: these tests look at the project's stack alone
    (root / ".keel" / "config.yml").write_text("stacks:\n  ignore: [symfony]\n")
    return root


# ------------------------------------------------------------------ the pack's validation

def test_setup_block_is_validated():
    assert stacks.validate_setup(None) == []
    assert stacks.validate_setup({"apt": ["php8.3-cli", "unzip"], "run": ["curl -sS x | php"],
                                  "each_worktree": ["composer install -n --no-progress"]}) == []
    assert stacks.validate_setup("php") == ["setup is not a mapping: give apt, run or each_worktree, each a list"]
    errs = stacks.validate_setup({"apt": ["php8.3-cli; rm -rf /"], "each-worktree": ["x"], "run": "make"})
    assert any("setup.apt has 'php8.3-cli; rm -rf /'" in e for e in errs)
    assert any("setup.each-worktree is not something keel reads" in e for e in errs)
    assert "setup.run is not a list of text" in errs


def test_loader_leaves_a_broken_setup_out_and_keeps_the_stack(tmp_path):
    root = _project(tmp_path, "p", PHP_STACK + "setup:\n  apt: [PHP 8.3]\n")
    d = next(d for d in stacks.project_defs(str(root)) if d["name"] == "php-symfony")
    assert "setup" not in d and d["_setup_errors"] and d["commands"]["api_test_module"] == "vendor/bin/phpunit"
    assert stack_setup.block(d) == {"apt": [], "run": [], "each_worktree": []}


def test_keels_own_packs_have_valid_setup_blocks():
    by = {d["name"]: d for d in stacks.shipped()}
    assert not any(d.get("_setup_errors") for d in by.values())
    sym = stack_setup.block(by["symfony"])
    assert "php8.3-cli" in sym["apt"] and "composer" in sym["apt"]
    assert sym["each_worktree"] == ["composer install -n --no-progress"]
    assert stack_setup.block(by["go"])["apt"] == ["golang-go"]
    assert stack_setup.block(by["kotlin-spring"]) == {"apt": [], "run": [], "each_worktree": []}   # Java is in the image


def test_stack_check_reports_setup_errors_and_warns_about_root_commands(tmp_path):
    bad = stack_edit.check(None, PHP_STACK + "setup:\n  apt: ['bad name']\n")
    assert not bad["ok"] and any("setup.apt has 'bad name'" in e for e in bad["errors"])
    good = stack_edit.check(None, PHP_STACK + "setup:\n  apt: [php8.3-cli]\n  run: ['curl -sS https://x | php']\n")
    assert good["ok"], good["errors"]
    assert any("setup.run runs as keel's own user (not root)" in w for w in good["warnings"])
    # the guided form writes the block too: packages split at spaces or commas, commands one per line
    text = stack_edit.render({"name": "p", "lane": "api", "detect": {"files": "composer.json"},
                              "layers": [{"id": "unit"}],
                              "setup": {"apt": "php8.3-cli, unzip composer", "each_worktree": "composer install -n, --x\n"}})
    assert yaml.safe_load(text)["setup"] == {"apt": ["php8.3-cli", "unzip", "composer"],
                                             "each_worktree": ["composer install -n, --x"]}


# ------------------------------------------------------------------ the system part: union and plan

def test_union_of_apt_over_the_registered_projects(tmp_path):
    a = _project(tmp_path, "billing-router", PHP_STACK + "setup:\n  apt: [php8.3-cli, unzip, composer]\n")
    b = _project(tmp_path, "billing", PHP_STACK.replace("php-symfony", "php-laravel")
                 + "setup:\n  apt: [php8.3-cli, php8.3-pgsql]\n  run: ['echo tool']\n")
    c = _project(tmp_path, "plain", PHP_STACK.replace("php-symfony", "nothing"))       # no setup: not in the union
    items = stack_setup.needs([("billing-router", str(a)), ("billing", str(b)), ("plain", str(c))])
    assert [(n["project"], n["stack"]) for n in items] == [("billing-router", "php-symfony"), ("billing", "php-laravel")]
    assert items[0]["runtimes"] == ["php", "composer"]
    u = stack_setup.union(items)
    assert list(u["apt"]) == ["php8.3-cli", "unzip", "composer", "php8.3-pgsql"]
    assert u["apt"]["php8.3-cli"] == ["billing-router (php-symfony)", "billing (php-laravel)"]
    assert u["run"] == {"echo tool": ["billing (php-laravel)"]}


def test_plan_in_the_container_installs_only_what_is_missing():
    u = {"apt": {"php8.3-cli": ["billing-router (php-symfony)"], "unzip": ["billing-router (php-symfony)"],
                 "golang-go": ["api (go)"]},
         "run": {"echo tool": ["billing (php-laravel)"]}}
    p = stack_setup.plan(u, present={"unzip"}, ran=set(), where="container")
    assert p["steps"] == [{"do": "apt", "packages": ["php8.3-cli", "golang-go"],
                           "for": ["billing-router (php-symfony)", "api (go)"]},
                          {"do": "run", "command": "echo tool", "for": ["billing (php-laravel)"]}]
    assert p["message"] == "installing php8.3-cli, golang-go for billing-router, api…"
    # after a restart from the cache: everything is there, the run command ran in this container
    done = stack_setup.plan(u, present={"php8.3-cli", "unzip", "golang-go"}, ran={stack_setup.run_key("echo tool")},
                            where="container")
    assert done["steps"] == [] and done["message"] == ""


def test_plan_outside_the_container_installs_nothing_and_says_what_is_missing():
    items = [{"project": "billing-router", "root": "/x", "stack": "php-symfony", "apt": ["php8.3-cli", "composer"],
              "run": [], "runtimes": ["php", "composer"]}]
    u = stack_setup.union(items)
    p = stack_setup.plan(u, present=set(), ran=set(), where="native", have={"composer"}, items=items)
    assert p["steps"] == [] and p["missing"] == ["php"]
    assert p["message"].startswith("billing-router: php not found (stack php-symfony). keel does not install system "
                                   "packages outside its container")
    assert "sudo apt-get install php8.3-cli composer" in p["message"]
    assert stack_setup.plan(u, set(), set(), "native", have={"php", "composer"}, items=items)["message"] == ""


def test_system_run_calls_the_helper_for_apt_only_and_says_so(tmp_path, monkeypatch):
    marker = tmp_path / "run-marker.txt"
    root = _project(tmp_path, "billing-router", PHP_STACK + "setup:\n  apt: [php8.3-cli, composer]\n"
                    f"  run: ['id -un > {marker}']\n")
    calls = tmp_path / "calls.txt"
    helper = tmp_path / "fake-helper"
    helper.write_text(f'#!/bin/sh\necho "$@" >> {calls}\necho "Setting up $2"\n')
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "container")
    monkeypatch.setattr(stack_setup, "HELPER_CMD", [str(helper)])
    monkeypatch.setattr(stack_setup, "RAN_DIR", str(tmp_path / "ran"))
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("billing-router", str(root))])
    monkeypatch.setattr(stack_setup, "dpkg_present", lambda pkgs: set())
    sysu = stack_setup.SystemSetup()
    sysu.run_once()
    # the root helper gets the packages and nothing else; setup.run ran as this process's own user, no sudo
    assert calls.read_text().splitlines() == ["apt php8.3-cli composer"]
    assert marker.read_text().strip() == subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    st = sysu.status()
    assert st["state"] == "ready" and st["line"] == "installed php8.3-cli, composer"
    assert "$ keel-stack-setup apt php8.3-cli composer" in st["log"]
    assert (Path(os.environ["KEEL_DATA"]) / "setup" / "packages.json").is_file()
    # recorded: the next run in this container does not run it again
    marker.unlink()
    monkeypatch.setattr(stack_setup, "dpkg_present", lambda pkgs: {"php8.3-cli", "composer"})
    sysu.run_once()
    assert not marker.exists() and calls.read_text().splitlines() == ["apt php8.3-cli composer"]
    # a failing helper: the line says what could not be installed, for whom
    monkeypatch.setattr(stack_setup, "dpkg_present", lambda pkgs: set())
    helper.write_text("#!/bin/sh\necho 'E: Unable to locate package php8.3-cli'\nexit 100\n")
    sysu.run_once()
    st = sysu.status()
    assert st["state"] == "failed" and st["line"].startswith("could not install php8.3-cli, composer for billing-router "
                                                             "(exit 100): E: Unable to locate package")


def test_a_failing_setup_run_says_so(tmp_path, monkeypatch):
    root = _project(tmp_path, "billing", PHP_STACK + "setup:\n  run: ['echo nope; exit 4']\n")
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "container")
    monkeypatch.setattr(stack_setup, "HELPER_CMD", ["false"])          # never called: no apt here
    monkeypatch.setattr(stack_setup, "RAN_DIR", str(tmp_path / "ran"))
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("billing", str(root))])
    sysu = stack_setup.SystemSetup()
    sysu.run_once()
    st = sysu.status()
    assert st["state"] == "failed" and st["line"] == "could not run echo nope; exit 4 for billing (exit 4): nope"
    assert not (tmp_path / "ran").exists()


# ------------------------------------------------------------------ the root helper and sudo

HELPER_FILE = Path(__file__).resolve().parents[2] / "docker" / "keel-stack-setup"


def _helper(*args):
    return subprocess.run(["bash", str(HELPER_FILE), *args], capture_output=True, text=True)


@pytest.mark.parametrize("args", [[], ["run", "id"], ["run"], ["sh", "-c", "id"], ["APT", "php"], ["install", "php"],
                                  ["--", "apt", "php"]])
def test_the_helper_refuses_anything_but_apt(args):
    r = _helper(*args)
    assert r.returncode == 2 and "usage: keel-stack-setup apt <package>..." in r.stderr


@pytest.mark.parametrize("pkg", ["php8.3-cli;id", "-o", "--allow-unauthenticated", "Php", "php=1.0", "x y", "$(id)",
                                 "../x", "", "sudo-", "a.+", "-oFoo", "a*", "a?", "a..b", "a+.b", "x" * 101])
def test_the_helper_refuses_what_is_not_a_package_name(pkg):
    r = _helper("apt", "php8.3-cli", pkg)
    assert r.returncode == 2 and f"not a package name: {pkg}" in r.stderr     # before apt (or mkdir) runs


def test_the_helper_needs_a_package():
    assert _helper("apt").returncode == 2


def test_sudo_allows_keels_user_only_the_helpers_apt():
    dockerfile = (HELPER_FILE.parents[1] / "Dockerfile").read_text()
    lines = [ln for ln in dockerfile.splitlines() if "sudoers" in ln and "echo" in ln]
    assert len(lines) == 2
    assert "echo 'keel ALL=(root) NOPASSWD: /usr/local/bin/keel-stack-setup apt *' > /etc/sudoers.d/keel-stack-setup" \
        in lines[0]
    # 0.21.3 and one more fixed verb as root: the agents' user joins the Docker socket's group (keel2 --docker)
    assert "echo 'keel ALL=(root) NOPASSWD: /usr/local/bin/keel-stack-setup agent-docker' >> /etc/sudoers.d/keel-stack-setup" \
        in lines[1]
    assert "visudo -cf /etc/sudoers.d/keel-stack-setup" in dockerfile
    # 0.21.3 the agents' own user: keel's user runs only keel-as-agent, only as agent (never root)
    rules = [ln for ln in dockerfile.splitlines() if "ALL=(" in ln]
    assert [r for r in rules if "(agent)" in r] == ["        'keel ALL=(agent) NOPASSWD:SETENV: /usr/local/bin/keel-as-agent' > /etc/sudoers.d/keel-agent \\"]
    assert all("(root)" in r or "(agent)" in r for r in rules)
    assert "visudo -cf /etc/sudoers.d/keel-agent" in dockerfile
    text = HELPER_FILE.read_text()
    assert "bash -c" not in text and "\n  run)" not in text and "eval" not in text


def test_the_helper_keeps_its_apt_cache_away_from_keels_user():
    text = HELPER_FILE.read_text()
    assert "CACHE=/var/cache/keel-apt" in text and "/data" not in text.replace("the data volume", "")
    assert "-o root -g root" in text and "chown" not in text.split("usage()", 1)[1]
    assert "apt-get update" in text and "-- \"$@\"" in text         # lists always fresh; names after "--"
    assert "[ -L \"$CACHE\" ]" in text


def test_the_helper_finds_a_package_early_in_a_long_list(tmp_path):
    # 0.18.33 piped the list into `grep -q` under pipefail: grep stopped at the first match, the writer got SIGPIPE
    # (141) and every package that exists was "no such package" (php8.3-cli on Ubuntu 24.04)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "apt.log"
    fakes = {
        "install": "exit 0",
        "apt-get": f'echo "apt-get $*" >> {log}',
        "apt-cache": 'echo php8.3-cli; seq 1 200000 | sed "s/^/pkg-/"',
    }
    for name, body in fakes.items():
        (bin_ / name).write_text(f"#!/usr/bin/env bash\n{body}\n")
        (bin_ / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", str(HELPER_FILE), "apt", "php8.3-cli"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "no such package" not in r.stderr
    assert "install -y" in log.read_text() and "php8.3-cli" in log.read_text()


def test_the_background_request_runs_once_and_looks_again_when_asked(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(stack_setup.SystemSetup, "run_once", lambda self: runs.append(1) or {})
    sysu = stack_setup.SystemSetup()
    sysu.request(str(tmp_path), "p")
    assert sysu.wait(10)
    assert runs == [1]
    assert stack_setup.known_roots()[0][1] == os.path.realpath(tmp_path)       # remembered for the next start


# ------------------------------------------------------------------ each_worktree in a flow's worktree

def _worktree(repo, name="flow-setup-1", branch="feat/setup-1"):
    made = worktrees.add(str(repo), name, branch)
    return Path(made["path"])


def _marker_stack(wt: Path, command: str):
    (wt / ".keel" / "stacks").mkdir(parents=True, exist_ok=True)
    (wt / ".keel" / "stacks" / "marker.yml").write_text(MARKER_STACK % command)
    stacks.forget()


def test_each_worktree_runs_once_in_a_new_flow_worktree(client, repo):
    wt = _worktree(repo)
    _marker_stack(wt, "echo set-up > setup-marker.txt")
    jobs = stack_setup.worktree_jobs(str(wt))
    assert [(j["stack"], j["dir"], j["why"]) for j in jobs] == [("marker", "", "a new worktree")]
    assert stack_setup.worktree_jobs(str(repo)) == []          # the main checkout: nothing implied missing, no job

    tid = start(client, wt)
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "spec_gate", s
    assert (wt / "setup-marker.txt").read_text().strip() == "set-up"
    # shown as the flow's first step, with its own job and the command's output
    started = [e for e in client.bus.of(tid, "step.started")]
    assert started[0]["step"] == "__setup" and started[0]["data"]["name"] == "set up the stack"
    lines = [e["data"]["text"] for e in client.bus.of(tid, "code.step") if e["step"] == "__setup"]
    assert any(t.startswith("$ echo set-up > setup-marker.txt") for t in lines), lines
    # recorded in the worktree's own git dir: a second flow there does not run it again
    rec = stack_setup.read_record(str(wt))
    assert rec["marker:."]["ok"] is True
    assert stack_setup.record_file(str(wt)).parent == Path(_git(wt, "rev-parse", "--absolute-git-dir").strip())
    assert stack_setup.worktree_jobs(str(wt)) == []


def test_a_flow_with_nothing_to_set_up_shows_no_setup_step(client, repo):
    tid = start(client, repo)
    wait(client, tid)
    assert not [e for e in client.bus.of(tid) if e.get("step") == "__setup"]


def test_a_folder_without_its_installed_dependencies_is_set_up_too(tmp_path):
    root = _project(tmp_path, "app", PHP_STACK + "setup:\n  each_worktree: ['mkdir vendor']\n")
    jobs = stack_setup.worktree_jobs(str(root))
    assert [j["why"] for j in jobs] == ["the project folder has composer.json but no vendor/"]
    assert stack_setup.run_jobs(str(root), jobs)["ok"] and (root / "vendor").is_dir()
    assert stack_setup.worktree_jobs(str(root)) == []


def test_each_worktree_runs_in_the_lanes_folder(tmp_path):
    root = tmp_path / "shiptrack"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "composer.json").write_text("{}\n")
    (root / ".keel" / "stacks").mkdir(parents=True)
    (root / ".keel" / "stacks" / "php.yml").write_text(PHP_STACK + "setup:\n  each_worktree: ['mkdir vendor']\n")
    (root / ".keel" / "config.yml").write_text("backend:\n  dir: backend\nstacks:\n  ignore: [symfony]\n")
    jobs = stack_setup.worktree_jobs(str(root))
    assert [(j["dir"], j["why"]) for j in jobs] == [("backend", "backend has composer.json but no vendor/")]
    stack_setup.run_jobs(str(root), jobs)
    assert (root / "backend" / "vendor").is_dir() and not (root / "vendor").exists()


# ------------------------------------------------------------------ the failure gate

def test_a_failing_setup_stops_at_a_gate_retry_then_goes_on(client, repo):
    wt = _worktree(repo, "flow-setup-2", "feat/setup-2")
    _marker_stack(wt, "test -f ok.flag && echo fine > setup-marker.txt")
    tid = start(client, wt)
    s = wait(client, tid)
    assert s["status"] == "waiting", s
    w = s["waiting"]
    assert w["step"] == "__setup" and w["choices"] == ["retry", "skip", "stop"]
    assert w["title"].startswith("Setting up the stack failed: test -f ok.flag && echo fine > setup-marker.txt failed")
    assert [c["label"] for c in w["choice_info"]] == ["I fixed it, try again", "Skip the setup", "Stop the flow"]
    assert not (wt / "setup-marker.txt").exists()
    (wt / "ok.flag").write_text("")                         # the person fixes it
    s = decide(client, tid, payload={"choice": "retry"})
    assert s["waiting"]["step"] == "spec_gate", s
    assert (wt / "setup-marker.txt").read_text().strip() == "fine"


def test_skip_goes_on_and_is_not_asked_again(client, repo):
    wt = _worktree(repo, "flow-setup-3", "feat/setup-3")
    _marker_stack(wt, "exit 3")
    tid = start(client, wt)
    assert wait(client, tid)["waiting"]["step"] == "__setup"
    s = decide(client, tid, why="no network here", payload={"choice": "skip"})
    assert s["waiting"]["step"] == "spec_gate", s
    assert stack_setup.worktree_jobs(str(wt)) == []         # skipped for these commands: the next flow is not asked


def test_stop_ends_the_flow_with_the_reason(client, repo):
    wt = _worktree(repo, "flow-setup-4", "feat/setup-4")
    _marker_stack(wt, "composer-that-is-not-here install")
    tid = start(client, wt)
    s = wait(client, tid)
    assert s["waiting"]["step"] == "__setup"
    assert "composer-that-is-not-here not found; stack marker has no setup.apt" in s["waiting"]["title"]
    s = decide(client, tid, payload={"choice": "stop"})
    assert s["status"] == "stopped" and "stopped at the stack setup" in (s.get("note") or s.get("error") or "")


def test_the_gate_is_never_answered_by_the_run_mode(client, repo):
    wt = _worktree(repo, "flow-setup-5", "feat/setup-5")
    _marker_stack(wt, "exit 1")
    tid = start(client, wt, settings={"gates_mode": "every-ac", "cap_tokens": 0, "on_cap": "pause", "run_mode": "auto"})
    s = wait(client, tid)
    assert s["status"] == "waiting" and s["waiting"]["step"] == "__setup"


def test_in_the_container_a_flow_waits_for_the_packages_and_fails_clearly(tmp_path, monkeypatch):
    root = _project(tmp_path, "billing-router", PHP_STACK + "setup:\n  apt: [php8.3-cli]\n")
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "container")
    monkeypatch.setattr(stack_setup, "dpkg_present", lambda pkgs: set())
    monkeypatch.setattr(stack_setup, "ran_markers", lambda: set())
    asked = []

    class Fake:
        def request(self, root=None, name=None, reason=""):
            asked.append(root)

        def wait(self, timeout, tick=None, every=5.0):
            return True

        def status(self):
            return {"state": "failed", "line": "could not install php8.3-cli for billing-router (exit 100)", "log": []}

    monkeypatch.setattr(stack_setup, "SYSTEM", Fake())
    r = stack_setup.prepare(str(root))
    assert not r["ok"] and r["note"] == "could not install php8.3-cli for billing-router (exit 100)"
    assert asked == [str(root)]
    monkeypatch.setattr(stack_setup, "dpkg_present", lambda pkgs: {"php8.3-cli"})
    assert stack_setup.prepare(str(root))["ok"]


# ------------------------------------------------------------------ init's proposal

def test_init_proposes_the_setup_block_and_writes_it_after_the_plan(tmp_path):
    root = _project(tmp_path, "shiptrack", PHP_STACK)
    _git(root, "init", "-q")
    found = stack_setup.proposals(str(root))
    assert [(p["stack"], p["language"]) for p in found] == [("php-symfony", "php")]
    assert found[0]["setup"] == {"apt": ["php8.3-cli", "php8.3-xml", "php8.3-mbstring", "php8.3-sqlite3", "php8.3-intl",
                                         "php8.3-curl", "unzip", "composer"],
                                 "each_worktree": ["composer install -n --no-progress"]}
    text = init_gates.plan(str(root), init_gates.defaults(str(root)))
    assert "Add a setup: block to the stack php-symfony (.keel/stacks/stack.yml): PHP is not in keel's image" in text
    assert "- composer install -n --no-progress" in text
    assert "setup" not in yaml.safe_load((root / ".keel/stacks/stack.yml").read_text())   # nothing written before approval

    a = actions.ActionInput(root=str(root), phase="setup", title="init", ac=None, acs=[], fake=True, flow="init")
    r = actions.write_config(a)
    assert "Added a setup: block to .keel/stacks/stack.yml" in r.note
    doc = yaml.safe_load((root / ".keel/stacks/stack.yml").read_text())
    assert doc["setup"] == found[0]["setup"] and doc["commands"]["api_test_module"] == "vendor/bin/phpunit"
    assert "# What this stack needs in keel's container" in (root / ".keel/stacks/stack.yml").read_text()
    assert stack_setup.proposals(str(root)) == []           # written once
    assert "setup: block" not in actions.write_config(a).note


def test_init_proposes_nothing_for_a_language_the_image_has(tmp_path):
    kotlin = PHP_STACK.replace("php-symfony", "my-api").replace("composer.json", "build.gradle.kts") \
        .replace("vendor/bin/phpunit", "./gradlew test")
    root = _project(tmp_path, "k", kotlin, files=("build.gradle.kts",))
    assert stack_setup.proposals(str(root)) == []


# ------------------------------------------------------------------ the setup check and native mode

def test_setup_check_names_the_stack_whose_runtime_is_missing(tmp_path, monkeypatch):
    root = _project(tmp_path, "billing-router", PHP_STACK + "setup:\n  apt: [php8.3-cli]\n")
    monkeypatch.setattr(stack_setup.shutil, "which", lambda name: None if name == "php" else f"/usr/bin/{name}")
    monkeypatch.setattr(ladder.shutil, "which", lambda name: None if name == "php" else f"/usr/bin/{name}")
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "container")
    ok, rungs = ladder.run(str(root), simulate=False, runner=lambda r, c: (0, ""))
    assert not ok
    detail = rungs[0]["detail"]
    assert detail.splitlines()[0] == "missing: php"
    assert "php not found; stack php-symfony lists setup.apt; keel installs it at start" in detail


def test_native_mode_says_which_packages_are_missing_and_never_installs(tmp_path, monkeypatch):
    root = _project(tmp_path, "billing-router", PHP_STACK + "setup:\n  apt: [php8.3-cli, composer]\n")
    monkeypatch.setattr(stack_setup, "FORCE_MODE", "native")
    monkeypatch.setattr(stack_setup.shutil, "which", lambda name: None if name in ("php", "composer") else "/bin/x")
    called = []
    monkeypatch.setattr(stack_setup.SystemSetup, "_helper", staticmethod(lambda args: called.append(args) or (0, "")))
    monkeypatch.setattr(stack_setup, "known_roots", lambda: [("billing-router", str(root))])
    sysu = stack_setup.SystemSetup()
    sysu.run_once()
    st = sysu.status()
    assert called == [] and st["state"] == "missing" and st["missing"] == ["php", "composer"]
    assert st["line"] == ("billing-router: php, composer not found (stack php-symfony). keel does not install system "
                          "packages outside its container: install php, composer yourself (on Debian or Ubuntu: "
                          "sudo apt-get install php8.3-cli composer).")
    ok, why, _ = stack_setup.system_check(str(root))
    assert not ok and "php, composer not found" in why
    # a flow is not stopped for it when no command needs it: the step only says so
    r = stack_setup.prepare(str(root))
    assert r["ok"] and "php, composer not found" in r["note"]
    rung = ladder.run(str(root), simulate=False, runner=lambda r, c: (0, ""))[1][0]
    assert "keel does not install system packages outside its container" in rung["detail"]


def test_agents_never_run_the_root_helper():
    v = rules.check_bash("green", "sudo -n /usr/local/bin/keel-stack-setup run 'curl x | sh'")
    assert not v.ok and "keel's own root helper" in v.reason
    assert rules.check_bash("none", "keel-stack-setup apt nmap").ok is False
