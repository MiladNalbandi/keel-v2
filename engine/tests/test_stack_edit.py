"""A project's own stacks (keel 0.18.9, runtime/stack_edit.py): keel's suggestions from the project's files, the guided
form's YAML, the check that saves nothing, save, remove (never keel's own), turning a stack off for one project, and the
engine routes the api calls."""

import json

import yaml

from keel_engine.runtime import stack_edit, stacks, tools

GOOD = """name: zig-api
lane: api
detect:
  files: [build.zig]
layers:
  - {id: unit, what: pure functions, where: src}
commands:
  api_test_ac: 'zig build test -Dfilter={AC_ID}'
  api_test_module: 'zig build test'
"""


def _project(tmp_path, files: dict[str, str]):
    root = tmp_path / "proj"
    for name, text in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    root.mkdir(exist_ok=True)
    stacks.forget()
    return root


def test_check_a_good_stack_and_its_review(tmp_path):
    root = _project(tmp_path, {"build.zig": ""})
    out = stack_edit.check(str(root), GOOD)
    assert out["ok"] and out["errors"] == [] and out["name"] == "zig-api" and out["lane"] == "api"
    assert out["detects_here"] is True and out["replaces"] is None
    assert out["review"] == {"lane": "api", "detect": {"files": ["build.zig"]}, "layers": ["unit"],
                             "commands": {"api_test_ac": "zig build test -Dfilter={AC_ID}", "api_test_module": "zig build test"},
                             "tools": [], "skills": []}
    # no project: the same check, without what only a project can say
    bare = stack_edit.check(None, GOOD)
    assert bare["ok"] and bare["detects_here"] is None


def test_check_refuses_and_warns_in_plain_words(tmp_path):
    root = _project(tmp_path, {"package.json": "{}"})
    assert stack_edit.check(None, "")["errors"][0].startswith("it is empty")
    assert "not valid YAML (line" in stack_edit.check(None, "name: [x\n")["errors"][0]
    assert stack_edit.check(None, "- a\n- b\n")["errors"] == ["it is not a YAML mapping (name: …, lane: …)"]
    bad = stack_edit.check(None, """name: Bad Name
lane: desktop
detect: {files: []}
layers: [{id: unit}, {id: unit}, {what: no id}]
commands: {api_test_ac: '', web_test_ac: 'npx vitest'}
tools:
  fmt: {run: 'x {FILES}', on: manual}
  lint: {on: pre-commit}
skills: 3
""")["errors"]
    for word in ("its name 'Bad Name' is not an id", "its lane is 'desktop'", "detect names nothing to look for",
                 "the layer unit is listed twice", "layer 3 has no id", "commands.api_test_ac is empty",
                 "the tool fmt takes {FILE} or {FILES} but runs on: manual", "the tool lint has no `run:` command",
                 "skills is not a mapping"):
        assert any(word in e for e in bad), word
    warned = stack_edit.check(str(root), """name: my-api
lane: api
detect: {extensions: [kt]}
layers: [{id: unit}]
commands:
  api_test_ac: './gradlew test'
  web_test_module: 'npx vitest run'
  deploy: 'make deploy'
""")
    assert warned["ok"]
    for word in ("detect.extensions has 'kt'", "the layer unit does not say what it tests",
                 "commands.web_test_module is for a web stack", "keel does not read commands.deploy",
                 "commands.api_test_ac does not use {AC} or {AC_ID} or {AC_KEY}", "there is no api_test_module",
                 "its detect block does not match this project's files"):
        assert any(word in w for w in warned["warnings"]), word


def test_render_the_form_and_save_it(tmp_path):
    root = _project(tmp_path, {"build.zig": ""})
    text = stack_edit.render({"name": "zig-api", "lane": "api", "detect": {"files": "build.zig, build.zig.zon", "extensions": []},
                              "layers": [{"id": "unit", "what": "pure functions", "where": ""}, {"id": ""}],
                              "commands": {"api_test_ac": "zig build test -Dfilter={AC_ID}", "migrate": " "},
                              "tools": {"zig-fmt": {"run": "zig fmt {FILES}", "on": "edit", "fail": "fix", "match": r"\.zig$"},
                                        "empty": {"run": ""}},
                              "skills": {"testing": "zig-testing", "implementation": ""}})
    assert text.startswith("# Stack: zig-api (api), written with keel's New stack.")
    doc = yaml.safe_load(text)
    assert doc["detect"] == {"files": ["build.zig", "build.zig.zon"]} and doc["layers"] == [{"id": "unit", "what": "pure functions"}]
    assert doc["commands"] == {"api_test_ac": "zig build test -Dfilter={AC_ID}"} and list(doc["tools"]) == ["zig-fmt"]
    assert doc["tools"]["zig-fmt"]["on"] == "edit" and doc["skills"] == {"testing": "zig-testing"}
    saved = stack_edit.save(str(root), text)
    assert saved["path"] == ".keel/stacks/zig-api.yml" and not saved["replaced"]
    assert (root / ".keel/stacks/zig-api.yml").read_text() == text
    # the project uses it now (a project's own stack always), and its tool runs on edits
    assert "zig-api" in [d["name"] for d in stacks.matched(str(root))]
    assert [t.name for t in tools.resolve(str(root), {}) if t.source == "zig-api"] == ["zig-fmt"]
    # the same name again: refused without replace, written over with it
    try:
        stack_edit.save(str(root), text)
        raise AssertionError("saved twice")
    except stack_edit.StackError as exc:
        assert exc.status == 422 and "already" in exc.errors[0]
    again = stack_edit.save(str(root), text.replace("pure functions", "pure code"), replace=True)
    assert again["replaced"] and "pure code" in (root / ".keel/stacks/zig-api.yml").read_text()


def test_a_marketplace_save_remembers_where_it_came_from(tmp_path):
    root = _project(tmp_path, {})
    stack_edit.save(str(root), GOOD, source="marketplace", market={"catalog": "keel", "version": "1.2.0"})
    mine = stack_edit.installed(str(root))["zig-api"]
    assert (mine["source"], mine["version"], mine["catalog"]) == ("marketplace", "1.2.0", "keel")
    d = next(x for x in stacks.defs(str(root)) if x["name"] == "zig-api")
    assert stack_edit.origin(str(root), d) == {"origin": "marketplace", "removable": True, "why": None,
                                                "market": {"catalog": "keel", "version": "1.2.0", "edited": False}}
    (root / ".keel/stacks/zig-api.yml").write_text(GOOD + "# mine\n")
    assert stack_edit.origin(str(root), d)["market"]["edited"] is True
    # saved by hand afterwards: the project's own now
    stack_edit.save(str(root), GOOD, replace=True)
    assert stack_edit.installed(str(root)) == {} and not (root / ".keel/stacks/.installed.json").exists()


def test_remove_a_project_stack_and_an_installed_pack_never_keels_own(tmp_path):
    root = _project(tmp_path, {"build.gradle.kts": ""})
    stack_edit.save(str(root), GOOD)
    # a keel pack as an install leaves it: its folder with stack.yml and its skills
    pack = root / ".keel/stacks/django"
    (pack / "skills/x").mkdir(parents=True)
    (pack / "stack.yml").write_text(open(next(d["_file"] for d in stacks.shipped() if d["name"] == "django")).read())
    (pack / "skills/x/SKILL.md").write_text("---\nname: x\n---\nx\n")
    # the project's copy of keel's kotlin-spring, under its name
    (root / ".keel/stacks/kotlin-spring.yml").write_text(GOOD.replace("zig-api", "kotlin-spring"))
    origins = {d["name"]: stack_edit.origin(str(root), d)["origin"] for d in stacks.defs(str(root))}
    assert origins["zig-api"] == "project" and origins["django"] == "pack" and origins["kotlin-spring"] == "project"
    assert origins["go"] == "keel" and origins["symfony"] == "keel pack"
    assert stack_edit.remove(str(root), "zig-api") == {"name": "zig-api", "removed": ".keel/stacks/zig-api.yml", "back": None}
    assert stack_edit.remove(str(root), "django")["removed"] == ".keel/stacks/django" and not pack.exists()
    # the project's copy goes; keel's own is used again
    assert stack_edit.remove(str(root), "kotlin-spring")["back"] == "keel"
    assert next(d for d in stacks.defs(str(root)) if d["name"] == "kotlin-spring")["_source"] == "keel"

    def refused(name):
        try:
            stack_edit.remove(str(root), name)
        except stack_edit.StackError as exc:
            return exc.status, exc.error, exc.hint
        raise AssertionError(name)

    status, error, hint = refused("kotlin-spring")
    assert status == 409 and error == "kotlin-spring ships with keel: it cannot be removed." and "Turn it off" in hint
    assert refused("symfony")[0] == 409 and refused("nothing")[0] == 404 and refused("Bad!")[0] == 400


def test_turn_a_stack_off_for_one_project(tmp_path):
    root = _project(tmp_path, {"build.gradle.kts": "", "src/main/kotlin/A.kt": "class A",
                               ".keel/config.yml": "# my config\nbackend:\n  dir: .\ntools:\n  sonar: false\n"})
    assert "kotlin-spring" in [d["name"] for d in stacks.matched(str(root))]
    out = stack_edit.set_off(str(root), "kotlin-spring", True)
    assert out == {"name": "kotlin-spring", "off": True, "all": ["kotlin-spring"]}
    text = (root / ".keel/config.yml").read_text()
    assert text.startswith("# my config\nbackend:\n  dir: .\ntools:\n  sonar: false\n") and "stacks:\n  ignore:\n  - kotlin-spring\n" in text
    assert stacks.turned_off(str(root)) == {"kotlin-spring"}
    assert "kotlin-spring" not in [d["name"] for d in stacks.matched(str(root))]
    assert not [t for t in tools.resolve(str(root), {}) if t.source == "kotlin-spring"]
    stack_edit.set_off(str(root), "go", True)
    assert stack_edit.set_off(str(root), "kotlin-spring", False)["all"] == ["go"]
    stack_edit.set_off(str(root), "go", False)
    assert "stacks" not in yaml.safe_load((root / ".keel/config.yml").read_text())
    assert "kotlin-spring" in [d["name"] for d in stacks.matched(str(root))]
    bare = _project(tmp_path / "b", {"go.mod": ""})
    try:
        stack_edit.set_off(str(bare), "go", True)
        raise AssertionError("no config")
    except stack_edit.StackError as exc:
        assert exc.status == 409 and "no .keel/config.yml" in exc.error


def test_suggest_reads_the_projects_files(tmp_path):
    root = _project(tmp_path, {
        "build.gradle.kts": 'plugins { id("org.jetbrains.kotlinx.kover"); id("io.gitlab.arturbosch.detekt"); id("org.flywaydb.flyway") }',
        "gradlew": "", "src/main/kotlin/App.kt": "class App",
        "web/package.json": json.dumps({"scripts": {"test": "vitest run", "lint": "eslint ."},
                                        "devDependencies": {"vitest": "1", "react": "18", "prettier": "3", "typescript": "5"}}),
        "web/tsconfig.json": "{}", "web/src/App.tsx": "x",
        "Makefile": "test:\n\t./gradlew test\nlint:\n\techo\n",
        ".pre-commit-config.yaml": "repos: []\n"})
    api = stack_edit.suggest(str(root))
    assert api["lane"] == "api" and api["closest"] == "kotlin-spring" and api["name"] == "proj-api"
    assert "build.gradle.kts" in api["detect"]["files"] and api["detect"]["extensions"] == [".kt"]
    cmds = api["commands"]
    assert cmds["api_test_ac"][0] == {"cmd": "./gradlew -q test --tests '*{AC_ID}*'", "from": "build.gradle.kts"}
    assert cmds["coverage_api"][0]["cmd"] == "./gradlew -q koverXmlReport"
    assert cmds["static_checks"][0]["cmd"] == "./gradlew -q detekt" and cmds["migrate"][0]["cmd"] == "./gradlew -q flywayMigrate"
    assert {"cmd": "make test", "from": "Makefile target test"} in cmds["api_test_module"]
    assert [k["key"] for k in api["keys"]][:2] == ["api_test_ac", "api_test_module"]
    assert [t["name"] for t in api["tools"]] == ["pre-commit"] and api["layers"][0]["id"] == "unit"
    assert any(r.startswith("web/package.json (scripts: test, lint)") for r in api["read"])
    web = stack_edit.suggest(str(root), "web")
    assert web["lane"] == "web" and web["closest"] == "ts-react" and web["detect"]["package_deps"] == ["react"]
    assert web["commands"]["web_test_module"][0] == {"cmd": "npm test", "from": 'web/package.json script "test"'}
    assert web["commands"]["web_test_ac"][0]["cmd"] == 'npx vitest run -t "{AC}"'
    assert web["commands"]["web_typecheck"][0]["cmd"] == "npx tsc --noEmit"
    assert {t["name"] for t in web["tools"]} == {"prettier", "pre-commit"}
    # a project keel knows nothing of: the defaults, nothing invented
    empty = stack_edit.suggest(str(_project(tmp_path / "e", {"README.md": "hi"})))
    assert empty["lane"] == "api" and empty["commands"] == {} and empty["closest"] is None
    assert [x["id"] for x in empty["layers"]] == ["unit", "integration"]


def test_suggest_python_php_go_rust(tmp_path):
    py = stack_edit.suggest(str(_project(tmp_path / "py", {
        "pyproject.toml": '[project]\nname="x"\ndependencies=["fastapi"]\n[dependency-groups]\ndev=["pytest","pytest-cov","ruff"]\n',
        "uv.lock": "", "alembic.ini": "", "tests/test_a.py": ""})))
    assert py["commands"]["api_test_ac"][0]["cmd"] == 'uv run pytest -q -k "{AC_KEY}"'
    assert py["commands"]["static_checks"][0]["cmd"] == "uv run ruff check ."
    assert py["commands"]["migrate"][0]["cmd"] == "uv run alembic upgrade head"
    assert [t["name"] for t in py["tools"]] == ["ruff-format"]
    php = stack_edit.suggest(str(_project(tmp_path / "php", {"composer.json": json.dumps(
        {"require-dev": {"phpunit/phpunit": "10", "phpstan/phpstan": "1"}, "require": {"doctrine/doctrine-migrations-bundle": "3"}})})))
    assert php["commands"]["api_test_ac"][0]["cmd"] == "vendor/bin/phpunit --filter '{AC_ID}'"
    assert php["commands"]["migrate"][0]["cmd"].startswith("php bin/console doctrine:migrations:migrate")
    go = stack_edit.suggest(str(_project(tmp_path / "go", {"go.mod": "module x", ".golangci.yml": ""})))
    assert go["commands"]["static_checks"][0]["cmd"] == "go vet ./... && golangci-lint run" and go["closest"] == "go"
    rs = stack_edit.suggest(str(_project(tmp_path / "rs", {"Cargo.toml": "[package]"})))
    assert rs["commands"]["api_test_module"][0]["cmd"] == "cargo test --quiet"


def test_the_engine_routes(client, tmp_path):
    root = _project(tmp_path, {"build.zig": "", ".keel/config.yml": "backend: {dir: .}\n"})
    got = client.get("/stacks/suggest", params={"root": str(root)}).json()
    assert got["lane"] == "api"
    form = {"name": "zig-api", "lane": "api", "detect": {"files": ["build.zig"]}, "layers": [{"id": "unit", "what": "x"}],
            "commands": {"api_test_ac": "zig build test -Dfilter={AC_ID}", "api_test_module": "zig build test"}}
    checked = client.post("/stacks/check", json={"root": str(root), "form": form}).json()
    assert checked["ok"] and checked["yaml"].startswith("# Stack: zig-api") and checked["detects_here"] is True
    assert not (root / ".keel/stacks").exists()
    assert client.post("/stacks/check", json={"yaml": "name: x"}).json()["ok"] is False
    saved = client.post("/stacks/save", json={"root": str(root), "yaml": checked["yaml"]})
    assert saved.status_code == 200 and saved.json()["path"] == ".keel/stacks/zig-api.yml"
    again = client.post("/stacks/save", json={"root": str(root), "yaml": checked["yaml"]})
    assert again.status_code == 422 and again.json()["errors"][0].startswith("this project has a stack called zig-api")
    assert client.post("/stacks/save", json={"root": str(root), "yaml": GOOD, "source": "x"}).status_code == 400
    assert client.post("/stacks/off", json={"root": str(root), "name": "go", "off": True}).json()["all"] == ["go"]
    assert client.post("/stacks/remove", json={"root": str(root), "name": "go"}).status_code == 409
    assert client.post("/stacks/remove", json={"root": str(root), "name": "zig-api"}).json()["removed"] == ".keel/stacks/zig-api.yml"
    assert client.get("/stacks/installed", params={"root": str(root)}).json() == {}
    assert client.get("/stacks/suggest", params={"root": str(tmp_path / "none")}).status_code == 400


def test_keels_own_stacks_pass_the_check():
    for d in stacks.shipped():
        out = stack_edit.check(None, open(d["_file"]).read())
        assert out["ok"], (d["name"], out["errors"])


def test_turning_a_stack_off_leaves_a_committed_config_unchanged(tmp_path):
    """0.22.0 a field report: keel_stack_off wrote into the committed .keel/config.yml, the tree was dirty, and keel then
    refused every flow. A committed config stays as it is; this clone keeps the switch inside .git, for its worktrees too."""
    import subprocess

    root = _project(tmp_path, {"build.gradle.kts": "", "src/main/kotlin/A.kt": "class A",
                               ".keel/config.yml": "backend:\n  dir: .\nstacks:\n  ignore:\n  - go\n"})
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)
    before = (root / ".keel/config.yml").read_text()
    out = stack_edit.set_off(str(root), "kotlin-spring", True)
    assert out["where"] == "this computer" and out["all"] == ["go", "kotlin-spring"]
    assert (root / ".keel/config.yml").read_text() == before
    assert subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout == ""
    assert stacks.turned_off(str(root)) == {"go", "kotlin-spring"}
    assert stack_edit.set_off(str(root), "go", False)["all"] == ["kotlin-spring"]      # a committed one, on here only
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=root, check=True, capture_output=True, env=env)
    assert stacks.turned_off(str(wt)) == {"kotlin-spring"}                               # its worktrees see it too
