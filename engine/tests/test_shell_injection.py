"""0.18.64 security audit F14: text keel puts into a shell command is never shell code.

(a) the criterion id of run_tests(ac=...) in {AC}; (b) a test name from a runner's output in {TEST}; (d) a fix
command run through a container prefix (wrap_one) that a single `&` or a newline split, so its second half ran outside
the container. (c) is in test_git_engine_hardening.py. Each attack runs for real in a temp folder: the file it would
create must not appear."""

import subprocess
from pathlib import Path

import pytest

from keel_engine.runtime import container, suite_failure
from keel_engine.tools import testcmd
from keel_engine.tools.agent_tools import ToolBox

ATTACKS = ["AC-1; touch PWNED", "AC-1$(touch PWNED)", "AC-1`touch PWNED`", "AC-1' ; touch PWNED; '", 'AC-1" ; touch PWNED; "',
           "AC-1 && touch PWNED", "AC-1\ntouch PWNED", "AC-1 | touch PWNED", "--help"]


def project(tmp_path, cmd: str, key: str = "api_test_ac") -> Path:
    r = tmp_path / "proj"
    (r / ".keel").mkdir(parents=True)
    (r / ".keel" / "config.yml").write_text(f"backend:\n  dir: .\ncommands:\n  {key}: {cmd!r}\n")
    return r


# ------------------------------------------------------------------ (a) run_tests(ac=...)

@pytest.mark.parametrize("template", ["echo {AC}", "echo '*{AC}*'", 'echo "{AC}"', "echo --filter={AC_ID} -k {AC_KEY}"])
@pytest.mark.parametrize("ac", ATTACKS)
def test_run_tests_refuses_an_id_that_is_not_a_criterion(tmp_path, template, ac):
    root = project(tmp_path, template)
    refused = []
    box = ToolBox(str(root), "red", on_refuse=lambda *a: refused.append(a))
    out = box.run_tests(ac)
    assert out.startswith("REFUSED") and refused
    assert not (root / "PWNED").exists()


@pytest.mark.parametrize("template", ["echo {AC}", "echo '*{AC}*'", 'echo "{AC}"', "echo --filter={AC_ID} -k {AC_KEY}"])
@pytest.mark.parametrize("ac", ATTACKS)
def test_a_filled_command_never_runs_what_the_id_says(tmp_path, template, ac):
    cmd = testcmd.fill(template, ac)
    subprocess.run(cmd, shell=True, cwd=tmp_path, capture_output=True, timeout=10)
    assert not (tmp_path / "PWNED").exists(), cmd


@pytest.mark.parametrize("ac", ["AC-6", "CHG-1.2", "BUG-3", "ac-12"])
def test_keels_own_ids_run(tmp_path, ac):
    root = project(tmp_path, "echo ran-{AC}")
    out = ToolBox(str(root), "red").run_tests(ac)
    assert "exit 0" in out and f"ran-{ac}" in out


def test_filled_commands_look_as_before_for_keels_ids():
    assert testcmd.fill("./gradlew -q test --tests '*{AC}*'", "AC-6") == \
        "./gradlew -q test --tests '*AC-6*' --tests '*AC6*'"
    assert testcmd.fill("vendor/bin/phpunit --filter '{AC_ID}'", "CHG-1.2") == "vendor/bin/phpunit --filter 'CHG12'"
    assert testcmd.fill('npx vitest run -t "{AC}"', "AC-6") == 'npx vitest run -t "AC-6"'
    assert testcmd.fill("pytest -k {AC_KEY}", "AC-6") == "pytest -k ac_6"
    assert testcmd.fill("echo {AC}", "AC-n") == "echo AC-n"           # explain's placeholder


# ------------------------------------------------------------------ (b) test names into {TEST}

# keel cuts a name at the first space or parenthesis, so a real attack has neither (${IFS} is a space to the shell)
@pytest.mark.parametrize("name", ["FooTest::bar;touch${IFS}PWNED", "FooTest::bar`touch${IFS}PWNED`",
                                  "tests/x.py::a|touch${IFS}PWNED", "FooTest::b';touch${IFS}PWNED;'",
                                  'FooTest::b";touch${IFS}PWNED;"', "FooTest::b&&touch${IFS}PWNED",
                                  "tests/x.py::test_a; touch PWNED", "✕ adds `touch PWNED`", "--version"])
@pytest.mark.parametrize("template", ["echo {TEST}", "echo '{TEST}'", 'echo "{TEST}"'])
def test_a_failing_test_name_never_runs_as_shell_code(tmp_path, name, template):
    root = project(tmp_path, template, key="api_test_one")
    got = suite_failure.alone(str(root), [name], env=None)
    assert not (root / "PWNED").exists()
    assert all(x["command"] != template for x in got)


def test_the_test_name_attack_is_real_with_a_plain_replace(tmp_path):
    # the control: what keel did before 0.18.64 (the short name put in as it is)
    cmd = "echo {TEST}".replace("{TEST}", suite_failure._short("FooTest::bar;touch${IFS}PWNED"))
    subprocess.run(cmd, shell=True, cwd=tmp_path, capture_output=True, timeout=10)
    assert (tmp_path / "PWNED").exists()


@pytest.mark.parametrize("name, short", [("tests/x.py::test_a[1]", "test_a[1]"), ("App\\Tests\\FooTest::testBar", "testBar"),
                                         ("FooTest > bar() FAILED", "bar")])
def test_a_plain_test_name_still_runs_alone(tmp_path, name, short):
    root = project(tmp_path, "echo {TEST}", key="api_test_one")
    cmd, _rel = suite_failure.one_test_command(str(root), "API", name)
    assert cmd == f"echo {testcmd.shlex.quote(short)}"
    (root / "test_a1").write_text("")      # a glob would match this file; the quoted name does not
    out = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True).stdout.strip()
    assert out == short


# ------------------------------------------------------------------ (d) wrap_one

@pytest.mark.parametrize("cmd", ["a & b", "a\nb", "a;b", "a && b", "a | b", "echo $(x)", "echo `x`", "echo $HOME",
                                 "echo 'a b'", "ls *.txt", "a > f"])
def test_any_shell_syntax_runs_whole_inside_the_container(cmd):
    assert container.wrap_one(cmd, "docker compose exec -T app") == \
        f"docker compose exec -T app sh -c {testcmd.shlex.quote(cmd)}"


@pytest.mark.parametrize("cmd", ["composer update -W", "php -v", "npm ci", "./gradlew -q test", "pip install -r requirements.txt"])
def test_a_command_of_plain_words_keeps_the_plain_form(cmd):
    assert container.wrap_one(cmd, "P") == f"P {cmd}"


@pytest.mark.parametrize("cmd", ["true & touch OUTSIDE", "true\ntouch OUTSIDE"])
def test_the_second_half_never_runs_outside_the_container(tmp_path, cmd):
    # the "container" prefix here is `true`: everything it is given runs nowhere; before the fix the part after the &
    # or the newline ran on keel's side
    line = container.wrap_one(cmd, "true")
    subprocess.run(line, shell=True, cwd=tmp_path, capture_output=True, timeout=10)
    assert not (tmp_path / "OUTSIDE").exists()
