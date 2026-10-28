"""0.18.65 the pipeline step: the project's own CI jobs, run by keel on CI's versions (runtime/ci_config.py,
runtime/ci_pipeline.py).

A flow passed keel's style check (newest runtime, older style tool) and its tests, and CI failed: CI's style job runs
on an older runtime where the tool flags code the local run accepted. keel reads the CI config, expands its matrix and
runs each job itself, in a throw-away container on CI's image (a fake `docker` on PATH here records every call), or on
this machine when the version is here. Examples: a shop app on PHP and Python."""

import asyncio
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from keel_engine.runtime import actions, ci_config, ci_pipeline, ship, verdicts
from keel_engine.runtime.actions import ActionInput

IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(root, *args):
    return subprocess.run(["git", *IDENT, *args], cwd=root, capture_output=True, text=True)


def project(tmp_path, files: dict[str, str], name="shop") -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text).lstrip())
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: start")
    return root


def ai(root, **kw) -> ActionInput:
    base = dict(root=str(root), phase="ship", title="t", ac=None, acs=[], fake=False, flow="feature", project="shop")
    base.update(kw)
    return ActionInput(**base)


def by_key(plan) -> dict:
    return {r["key"]: r for r in plan["runs"]}


# ------------------------------------------------------------------ the config

CI_YML = """
name: CI
on: [push, pull_request]
env:
  APP_ENV: testing
jobs:
  cs:
    name: Code style
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: shivammathur/setup-php@v2
        with:
          php-version: '7.4'
          tools: php-cs-fixer:3.4
      - run: php-cs-fixer fix --dry-run --diff
  tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        php: [7.4, 8.1, 8.2]
        laravel: ['8.*', '10.*']
        exclude:
          - php: 7.4
            laravel: '10.*'
        include:
          - php: 8.2
            stability: lowest
          - php: '8.3'
            laravel: '11.*'
    env:
      DB_CONNECTION: sqlite
    steps:
      - uses: actions/checkout@v4
      - uses: shivammathur/setup-php@v2
        with:
          php-version: ${{ matrix.php }}
          extensions: mbstring, pdo_sqlite, :xdebug
          coverage: pcov
      - name: Install
        run: composer require "laravel/framework:${{ matrix.laravel }}" --no-update && composer update
      - run: vendor/bin/phpunit
        working-directory: api
  py:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: [3.10, '3.12']
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m pytest
        env:
          GH: ${{ secrets.GITHUB_TOKEN }}
  coverage-upload:
    runs-on: ubuntu-latest
    steps:
      - run: ./upload.sh
        env:
          CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
  deploy:
    runs-on: ubuntu-latest
    needs: tests
    steps:
      - run: ./deploy.sh
  e2e:
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8
    steps:
      - run: npm run e2e
"""


def test_a_github_matrix_is_expanded_with_include_and_exclude(tmp_path):
    root = project(tmp_path, {".github/workflows/ci.yml": CI_YML})
    plan = ci_config.plan(str(root))
    assert plan["sources"] == [".github/workflows/ci.yml"] and not plan["problems"]
    tests = [r for r in plan["runs"] if r["job"] == "tests"]
    combos = [(r["matrix"]["php"], r["matrix"].get("laravel"), r["matrix"].get("stability")) for r in tests]
    # 3 x 2 minus (7.4, 10.*); stability joins both 8.2 runs; 8.3 fits none: a run of its own
    assert combos == [("7.4", "8.*", None), ("8.1", "8.*", None), ("8.1", "10.*", None), ("8.2", "8.*", "lowest"),
                      ("8.2", "10.*", "lowest"), ("8.3", "11.*", None)]
    r = by_key(plan)[".github/workflows/ci.yml#tests[php=8.1,laravel=10.*]"]
    assert (r["runtime"], r["version"], r["skip"]) == ("php", "8.1", "")
    assert r["extensions"] == ["mbstring", "pdo_sqlite", "pcov"], "a :disabled one is left out; coverage adds pcov"
    assert r["env"] == {"APP_ENV": "testing", "DB_CONNECTION": "sqlite"}
    assert r["steps"][0]["run"] == 'composer require "laravel/framework:10.*" --no-update && composer update'
    assert r["steps"][1] == {"name": "vendor/bin/phpunit", "run": "vendor/bin/phpunit", "dir": "api", "env": {},
                             "allow_fail": False, "shell": ""}
    assert ci_pipeline.image_for(r) == "php:8.1-cli"


def test_the_style_job_versions_stay_text_and_unsafe_jobs_are_skipped_with_a_reason(tmp_path):
    root = project(tmp_path, {".github/workflows/ci.yml": CI_YML})
    runs = by_key(ci_config.plan(str(root)))
    cs = runs[".github/workflows/ci.yml#cs"]
    assert (cs["style"], cs["version"], cs["tools"], cs["skip"]) == (True, "7.4", ["php-cs-fixer:3.4"], "")
    py = runs[".github/workflows/ci.yml#py[os=ubuntu-latest,python=3.10]"]
    assert py["version"] == "3.10", "YAML would read 3.10 as the float 3.1"
    assert py["skip"] == "" and any("GITHUB_TOKEN is not given" in n for n in py["notes"])
    assert "GH" not in py["steps"][0]["env"], "the token never reaches the run"
    assert runs[".github/workflows/ci.yml#py[os=windows-latest,python=3.12]"]["skip"].startswith("runs on windows-latest")
    assert runs[".github/workflows/ci.yml#coverage-upload"]["skip"].startswith("needs secrets (CODECOV_TOKEN)")
    assert runs[".github/workflows/ci.yml#deploy"]["skip"] == "a deploy / release / publish job"
    assert runs[".github/workflows/ci.yml#e2e"]["skip"].startswith("needs service containers (mysql)")


def test_a_release_workflow_and_a_tag_only_workflow_are_skipped(tmp_path):
    root = project(tmp_path, {
        ".github/workflows/release.yml": "on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make\n",
        ".github/workflows/nightly.yml": "on:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  slow:\n    runs-on: ubuntu-latest\n"
                                         "    steps:\n      - run: make slow\n",
        ".github/workflows/bad.yml": "jobs: [\n"})
    plan = ci_config.plan(str(root))
    runs = by_key(plan)
    assert runs[".github/workflows/release.yml#build"]["skip"] == "a deploy / release / publish workflow"
    assert runs[".github/workflows/nightly.yml#slow"]["skip"] == "runs on schedule, not on a push or pull request"
    assert plan["problems"] and plan["problems"][0].startswith(".github/workflows/bad.yml: not readable YAML")


def test_matrix_rules_from_github_docs():
    combos, why = ci_config.expand_matrix({
        "fruit": ["apple", "pear"], "animal": ["cat", "dog"],
        "include": [{"color": "green"}, {"color": "pink", "animal": "cat"}, {"fruit": "apple", "shape": "circle"},
                    {"fruit": "banana"}, {"fruit": "banana", "animal": "cat"}]})
    assert why == ""
    assert combos == [
        {"fruit": "apple", "animal": "cat", "color": "pink", "shape": "circle"},
        {"fruit": "apple", "animal": "dog", "color": "green", "shape": "circle"},
        {"fruit": "pear", "animal": "cat", "color": "pink"},
        {"fruit": "pear", "animal": "dog", "color": "green"},
        {"fruit": "banana"},
        {"fruit": "banana", "animal": "cat"}]
    assert ci_config.expand_matrix("${{ fromJSON(needs.a.outputs.m) }}")[0] == []
    assert ci_config.expand_matrix({"php": "${{ fromJSON(x) }}"})[1].startswith("matrix.php comes from an expression")


GITLAB = """
stages: [lint, test, deploy]
variables:
  APP_ENV: testing
.php:
  before_script:
    - composer install
  variables:
    COMPOSER_NO_INTERACTION: "1"
style:
  stage: lint
  image: php:7.4-cli
  script:
    - vendor/bin/php-cs-fixer fix --dry-run
test:
  extends: .php
  stage: test
  image: php:${PHP_VERSION}-cli
  parallel:
    matrix:
      - PHP_VERSION: ['8.1', '8.2']
        DB: sqlite
      - PHP_VERSION: '7.4'
  script:
    - vendor/bin/phpunit
  after_script:
    - echo done
deploy_prod:
  stage: deploy
  script: ./deploy.sh
smoke:
  stage: test
  when: manual
  script: ./smoke.sh
vault:
  stage: test
  secrets:
    DB_PASSWORD:
      vault: production/db/password@ops
  script: ./x.sh
"""


def test_gitlab_parallel_matrix_extends_and_skips(tmp_path):
    root = project(tmp_path, {".gitlab-ci.yml": GITLAB})
    plan = ci_config.plan(str(root))
    runs = by_key(plan)
    tests = [r for r in plan["runs"] if r["job"] == "test"]
    assert [(r["image"], r["version"], r["matrix"].get("DB")) for r in tests] == [
        ("php:8.1-cli", "8.1", "sqlite"), ("php:8.2-cli", "8.2", "sqlite"), ("php:7.4-cli", "7.4", None)]
    t = tests[0]
    assert t["image_from"] == "config" and t["skip"] == ""
    assert t["steps"][0]["run"] == "composer install\nvendor/bin/phpunit", "the template's before_script comes first"
    assert t["steps"][1]["allow_fail"] is True
    assert t["env"]["COMPOSER_NO_INTERACTION"] == "1" and t["env"]["APP_ENV"] == "testing" and t["env"]["PHP_VERSION"] == "8.1"
    assert runs[".gitlab-ci.yml#style"]["style"] is True
    assert runs[".gitlab-ci.yml#deploy_prod"]["skip"] == "a deploy / release / publish job"
    assert runs[".gitlab-ci.yml#smoke"]["skip"] == "runs only by hand (when: manual)"
    assert runs[".gitlab-ci.yml#vault"]["skip"].startswith("needs secrets")
    assert ".php" not in {r["job"] for r in plan["runs"]}, "a hidden template is no job"


def test_images_only_from_the_config_or_the_official_list():
    assert ci_pipeline.image_allowed("php:7.4-cli", set())
    assert ci_pipeline.image_allowed("node:20", set()) and ci_pipeline.image_allowed("eclipse-temurin:17", set())
    assert not ci_pipeline.image_allowed("evil/miner:latest", set())
    assert ci_pipeline.image_allowed("registry.example.com/shop/ci:2", {"registry.example.com/shop/ci:2"})
    assert not ci_pipeline.image_allowed("--privileged", {"--privileged"}), "never something that reads as an option"
    assert not ci_pipeline.image_allowed("php:7.4 -v /:/host", {"php:7.4 -v /:/host"})


# ------------------------------------------------------------------ running, with a fake docker

FAKE_DOCKER = r'''#!{python}
"""A docker client that records its argv and plays the containers on this machine (each in its own folder)."""
import json, os, shutil, subprocess, sys
home = "{home}"           # the docker client gets only docker's own variables
args = sys.argv[1:]
with open(os.path.join(home, "calls.jsonl"), "a") as f:
    f.write(json.dumps(args) + "\n")
cmd = args[0] if args else ""
if cmd == "version":
    print("27.0.0"); sys.exit(0)
if cmd == "create":
    name = args[args.index("--name") + 1]
    image = args[-2]
    missing = os.path.join(home, "missing")
    if os.path.exists(missing) and image in open(missing).read().split():
        print(f"Unable to find image '{image}' locally\npull access denied for {image}", file=sys.stderr); sys.exit(1)
    os.makedirs(os.path.join(home, name))
    json.dump({"image": image, "script": args[-1]}, open(os.path.join(home, name, "meta.json"), "w"))
    print("c0ffee"); sys.exit(0)
if cmd == "cp":
    src, dest = args[1], args[2]
    name = dest.split(":", 1)[0]
    shutil.copytree(src[:-2] if src.endswith("/.") else src, os.path.join(home, name, "work"))
    sys.exit(0)
if cmd == "start":
    name = args[-1]
    meta = json.load(open(os.path.join(home, name, "meta.json")))
    work = os.path.join(home, name, "work")
    env = {**os.environ, "KEEL_CI_WORK": work, "FAKE_IMAGE": meta["image"]}
    p = subprocess.run(["sh", meta["script"].replace("/work", work, 1)], env=env, cwd=work)
    sys.exit(p.returncode)
if cmd == "rm":
    shutil.rmtree(os.path.join(home, args[-1]), ignore_errors=True); sys.exit(0)
sys.exit(0)
'''


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    home = tmp_path / "fake-docker"
    bin_ = tmp_path / "fake-bin"
    home.mkdir()
    bin_.mkdir()
    (bin_ / "docker").write_text(FAKE_DOCKER.replace("{python}", sys.executable).replace("{home}", str(home)))
    (bin_ / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{__import__('os').environ['PATH']}")
    monkeypatch.delenv("KEEL_CI_DOCKER", raising=False)

    def calls():
        f = home / "calls.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    calls.home = home
    return calls


STYLE_CI = """
on: pull_request
jobs:
  cs:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        php: ['7.4', '8.2']
    steps:
      - uses: shivammathur/setup-php@v2
        with:
          php-version: ${{ matrix.php }}
      - name: style
        env:
          SHOP_MODE: ci
        run: |
          echo "checking on $FAKE_IMAGE"
          case "$FAKE_IMAGE" in *7.4*) echo "1) src/Cart.php (trailing_comma_in_multiline)"; exit 8;; esac
          test "$SHOP_MODE" = ci
          git diff --exit-code
          test -z "$(git status --porcelain)"
          echo "style ok" > .style-done
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: ./deploy.sh
"""


def test_each_run_goes_in_its_own_container_with_only_a_copy_of_the_project(tmp_path, fake_docker):
    root = project(tmp_path, {".github/workflows/ci.yml": STYLE_CI, "src/Cart.php": "<?php\n"})
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root)))
    assert r.ok is False and "1 of 3 run(s) failed (cs · PHP 7.4)" in r.note
    text = r.update["data"]["ci_pipeline_report"]
    assert "| cs | php 7.4 | failed |" in text
    assert "| cs | php 8.2 | passed |" in text
    assert "| deploy | — | skipped: a deploy / release / publish job | — |" in text
    assert "### cs · PHP 7.4 failed (step 1, exit 8)" in text and "1) src/Cart.php (trailing_comma_in_multiline)" in text
    assert "@@keel-" not in text, "the marker lines stay out of the report"
    calls = fake_docker()
    creates = [c for c in calls if c[0] == "create"]
    assert [c[-2] for c in creates] == ["php:7.4-cli", "php:8.2-cli"]
    for c in calls:
        flat = " ".join(c)
        assert "--privileged" not in c and "docker.sock" not in flat and "-v" not in c and "--volume" not in c \
            and "--mount" not in c and "host" not in c, c
    assert all(c[c.index("--network") + 1] == "bridge" for c in creates)
    cps = [c for c in calls if c[0] == "cp"]
    assert len(cps) == 2 and len({c[1] for c in cps}) == 1, "one temp copy goes into each container"
    src = Path(cps[0][1][:-2])
    assert not src.is_relative_to(root) and not src.exists(), "the copy is outside the project and gone afterwards"
    assert len([c for c in calls if c[0] == "rm"]) == 2, "every container is removed"
    assert not (root / ".style-done").exists() and not (root / ".keel-ci").exists(), "the project folder is untouched"
    assert git(root, "status", "--porcelain").stdout == ""
    v = verdicts.latest("shop", "ci")
    assert v["ok"] is False and v["detail"]["docker"] is True and "cs · PHP 7.4" in v["detail"]["summary"]


def test_a_missing_image_could_not_run_and_the_step_passes(tmp_path, fake_docker, monkeypatch):
    (fake_docker.home / "missing").write_text("php:7.4-cli\n")
    root = project(tmp_path, {".github/workflows/ci.yml": STYLE_CI})
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root, params={"jobs": ["cs"]})))
    text = r.update["data"]["ci_pipeline_report"]
    assert "could not run: could not make the container" in text and "| cs | php 8.2" in text
    assert "skipped: a deploy / release / publish job" in text
    assert r.ok is True, "nothing failed: a run that could not run is said, not failed"


def test_without_docker_only_the_local_version_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_CI_DOCKER", "0")
    here = ".".join(ci_pipeline.local_version("python").split(".")[:2])
    if not here:
        pytest.skip("no python3 on keel's shell")
    wf = {"on": ["push"], "jobs": {"test": {"runs-on": "ubuntu-latest",
                                            "strategy": {"matrix": {"python": [here, "2.7"]}},
                                            "steps": [{"uses": "actions/setup-python@v5", "with": {"python-version": "${{ matrix.python }}"}},
                                                      {"run": "python3 -c 'import sys; print(sys.version)' > made.txt && test -s made.txt"}]}}}
    root = project(tmp_path, {".github/workflows/ci.yml": yaml.safe_dump(wf)})
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root)))
    text = r.update["data"]["ci_pipeline_report"]
    assert f"| test | python {here} | passed |" in text
    assert ("| test | python 2.7 | could not run: python 2.7 not available here (this machine has " in text) and "(keel2 --docker runs it in a container)" in text
    assert "No Docker here (KEEL_CI_DOCKER=0)" in text
    assert r.ok is True and not (root / "made.txt").exists(), "the run used a temp copy"


def test_a_ci_config_the_branch_changed_never_runs_on_keels_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_CI_DOCKER", "0")
    wf = {"on": ["push"], "jobs": {"make": {"runs-on": "ubuntu-latest", "steps": [{"run": "touch ran.txt"}]}}}
    root = project(tmp_path, {".github/workflows/ci.yml": yaml.safe_dump(wf)})
    git(root, "checkout", "-q", "-b", "feat/cart")
    wf["jobs"]["make"]["steps"][0]["run"] = "cat ~/.ssh/id_rsa"           # an agent's edit during the flow
    (root / ".github/workflows/ci.yml").write_text(yaml.safe_dump(wf))
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root)))
    text = r.update["data"]["ci_pipeline_report"]
    assert ("| make | — | could not run: this branch changed the CI config (.github/workflows/ci.yml): keel runs it "
            "only in a container |") in text
    git(root, "checkout", "-q", "--", ".")
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root)))
    assert "| make | — | passed |" in r.update["data"]["ci_pipeline_report"], "the base's own CI config runs here"


def test_off_and_no_config(tmp_path):
    root = project(tmp_path, {".keel/config.yml": "ci:\n  run: off\n", ".github/workflows/ci.yml": STYLE_CI})
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root)))
    assert r.ok and r.note == "CI pipeline: off (ci.run: off)."
    bare = project(tmp_path, {"README.md": "shop\n"}, name="bare")
    r = asyncio.run(actions.run_action("ci_pipeline", ai(bare)))
    assert r.ok and "no CI config" in r.note and verdicts.latest("shop", "ci") is None
    (bare / ".keel").mkdir()
    (bare / ".keel" / "config.yml").write_text("ci:\n  run: on\n")
    r = asyncio.run(actions.run_action("ci_pipeline", ai(bare)))
    assert r.ok and r.note.startswith("CI pipeline not available")


def test_a_simulated_flow_runs_ci_only_when_asked(tmp_path, fake_docker):
    root = project(tmp_path, {".github/workflows/ci.yml": STYLE_CI})
    r = asyncio.run(actions.run_action("ci_pipeline", ai(root, fake=True)))
    assert "not run in a simulated flow" in r.update["data"]["ci_pipeline_report"] and not fake_docker()


def test_the_script_sets_up_composer_tools_and_reports_extensions():
    run = {"name": "cs", "source": ".github/workflows/ci.yml", "runtime": "php", "version": "7.4", "image_from": "",
           "env": {"APP_ENV": "testing", "bad name": "x"}, "tools": ["composer:v2", "php-cs-fixer:3.4", "mystery"],
           "extensions": ["intl", "redis-5.3.7"], "steps": [{"run": "composer install", "dir": "../..", "env": {}}]}
    text, files = ci_pipeline.script(run, "n0nce", 1, True, "report")
    assert "export APP_ENV=testing" in text and "bad name" not in text
    assert "--2 --install-dir=/usr/local/bin --filename=composer" in text
    assert "composer global require --quiet 'friendsofphp/php-cs-fixer:3.4.*'" in text
    assert "tool mystery from setup-php is not installed here" in text
    assert 'php -m | grep -qix intl || echo "$M note extension intl not available"' in text
    assert "grep -qix redis " in text
    assert '(cd "$W"/. && env $SH "$W/.keel-ci/run-1-step-1.sh")' in text, "a folder outside the copy becomes the copy's root"
    assert files == {"run-1-step-1.sh": "composer install\n"}
    text, _ = ci_pipeline.script(run, "n0nce", 1, True, "install")
    assert "pecl install redis" in text and "docker-php-ext-install intl" in text
    text, _ = ci_pipeline.script({**run, "image_from": "config"}, "n0nce", 1, True, "report")
    assert "composer-setup" not in text, "the CI config's own image is used as it is"


# ------------------------------------------------------------------ the report

def _res(**kw):
    base = {"key": "k", "source": ".github/workflows/ci.yml", "job": "cs", "name": "cs", "matrix": {}, "runtime": "php",
            "version": "7.4", "image": "", "style": True, "tools": [], "result": "passed", "why": "", "ms": 72_000,
            "notes": [], "tail": "", "step": None, "cmd": "", "code": 0}
    base.update(kw)
    return base


def test_the_report_has_a_row_per_run_the_failure_tail_and_a_summary():
    res = {"docker": True, "why": "", "runs": [
        _res(result="failed", step=2, code=8, cmd="vendor/bin/php-cs-fixer fix --dry-run", tail="1) src/Cart.php"),
        _res(job="tests", name="tests", style=False, version="8.1", matrix={"php": "8.1", "laravel": "10.*"}, ms=5_000),
        _res(job="tests", name="tests", style=False, version="5.6", result="could not run", why="php 5.6 not available here",
             ms=0),
        _res(job="deploy", name="deploy", style=False, runtime="", version="", result="skipped",
             why="a deploy / release / publish job", ms=0, notes=["never shown"])]}
    text = ci_pipeline.report(res, {"sources": [".github/workflows/ci.yml"], "problems": []},
                              ["your style check ran on PHP 8.4.1 with php-cs-fixer v3.64.0; CI runs PHP 7.4."])
    assert text.startswith("## CI on its own versions\n\nRead: .github/workflows/ci.yml. Docker: each run")
    assert "| cs | php 7.4 | failed | 1m 12s |" in text
    assert "| tests | php 8.1 · laravel=10.* | passed | 5s |" in text
    assert "| tests | php 5.6 | could not run: php 5.6 not available here | — |" in text
    assert ("**CI on its own versions: 1 of 4 run(s) failed (cs · PHP 7.4); 1 passed, 1 could not run, 1 skipped.**"
            in text)
    assert "### cs · PHP 7.4 failed (step 2, exit 8)\n\n```sh\nvendor/bin/php-cs-fixer fix --dry-run\n```" in text
    assert "### Style check\n\n- your style check ran on PHP 8.4.1" in text
    assert "never shown" not in text


def test_the_style_check_names_the_version_mismatch(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "php").write_text("#!/bin/sh\necho 8.4.1\n")
    (bin_ / "php-cs-fixer").write_text("#!/bin/sh\necho 'PHP CS Fixer 3.64.0 Persian Successor by Fabien Potencier'\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{__import__('os').environ['PATH']}")
    root = project(tmp_path, {".keel/config.yml": yaml.safe_dump({"tools": {"cs": {
        "run": "php-cs-fixer fix --dry-run {FILES}", "on": "edit", "fail": "block", "match": r"\.php$"}}})})
    res = {"runs": [_res(result="failed", tools=["php-cs-fixer:3.4"]), _res(job="tests", name="tests", style=False)]}
    from keel_engine import rules
    lines = ci_pipeline.style_check(str(root), rules.load_config(str(root)), "shop", res)
    assert lines == ["CI's style job cs · PHP 7.4 failed on CI's version: that result counts over keel's own style check.",
                     "your style check ran on PHP 8.4.1 with php-cs-fixer v3.64.0; CI runs PHP 7.4 (php-cs-fixer:3.4)."]


def test_the_final_review_and_the_ship_reviewers_get_the_report(tmp_path):
    root = project(tmp_path, {"README.md": "shop\n"})
    text = "## CI on its own versions\n\n| job | versions | result | time |\n|---|---|---|---|\n| cs | php 7.4 | failed | 3s |\n"
    state = {"data": {"ci_pipeline_report": text}}
    out = ship.final_report(str(root), "shop", state, "Cart totals")
    assert "| cs | php 7.4 | failed | 3s |" in out and "| ci |" not in out, "no verdict: no row in the table"
    from keel_engine.runtime.verdict_actions import record_verdict
    record_verdict("shop", "ci", False, {"summary": "CI on its own versions: 1 of 1 run(s) failed (cs · PHP 7.4)",
                                         "report": text}, root=str(root))
    out = ship.final_report(str(root), "shop", {"data": {}}, "Cart totals")
    assert "- CI on its own versions: 1 of 1 run(s) failed (cs · PHP 7.4)" in out
    assert "| ci | FAIL | CI on its own versions" in out and "| cs | php 7.4 | failed | 3s |" in out
    items = ship._lenses(ai(root, data={"ci_pipeline_report": text}, state={})).update["data"]["review_lenses"]
    assert items and all(i["ci"].startswith("## CI on its own versions") for i in items)


def test_the_ship_workflow_runs_ci_after_the_static_checks():
    from keel_engine.workflows.templates import get_template
    steps = [s.id for s in get_template("ship").steps]
    assert steps.index("lint") + 1 == steps.index("ci") < steps.index("lenses") < steps.index("final_review")
    ci = next(s for s in get_template("ship").steps if s.id == "ci")
    assert ci.actions() == ["ci_pipeline"] and ci.soft and ci.skippable == "optional"


def test_ship_runs_the_ci_jobs_and_the_final_review_shows_them(client, repo, monkeypatch, fake_docker):
    from conftest import decide, start, wait
    from test_flows_cover_ship import script, ship_plan
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(STYLE_CI)
    cfg = yaml.safe_load((repo / ".keel" / "config.yml").read_text()) if (repo / ".keel" / "config.yml").exists() else {}
    (repo / ".keel").mkdir(exist_ok=True)
    (repo / ".keel" / "config.yml").write_text(yaml.safe_dump({**(cfg or {}), "ci": {"run": "on"}}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ci: the shop's workflow")
    script(monkeypatch, ship_plan())
    tid = start(client, repo, workflow="ship")
    s = wait(client, tid)
    assert "  - ci" in s["waiting"]["detail"], "the skip menu offers it"
    s = decide(client, tid, "approve", payload={"skip": {"release": "r", "cover": "c", "deps": "d", "reviewers": "r",
                                                         "spec_walk": "s", "lint": "l"}})
    w = s["waiting"]
    assert w["step"] == "final_review", s
    assert "- CI on its own versions: 1 of 3 run(s) failed (cs · PHP 7.4)" in w["detail"]
    assert "| ci | FAIL |" in w["detail"] and "### cs · PHP 7.4 failed (step 1, exit 8)" in w["detail"]
    ran = [e["data"]["tool"] for e in client.bus.of(tid, "tool.ran")]
    assert ran == ["ci: cs · PHP 7.4", "ci: cs · PHP 8.2"]


def test_mcp_ci_run_targets_a_flow_of_the_project_only(client, tmp_path):
    root = project(tmp_path, {"README.md": "shop\n"})
    r = client.post("/projects/shop/actions/ci_pipeline", json={"root": str(root), "args": {"thread_id": "nope"}})
    assert r.status_code == 404
    r = client.post("/projects/shop/actions/ci_report", json={"root": str(root), "args": {}})
    assert r.status_code == 200 and r.json()["note"].startswith("No CI pipeline report yet")
