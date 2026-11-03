"""keel 0.22.0 a test run that fails because Docker or the database the tests need cannot be reached is not a test
failure (#67): the flow asks the person (recheck, skip, stop) instead of sending it back to the agent round after round,
and the preflight says so before the build."""

import asyncio
import stat
import subprocess
from pathlib import Path

from keel_engine.runtime import actions, feature_actions, harness_actions, repeats
from keel_engine.runtime.actions import ActionInput
from keel_engine.tools import testcmd

TESTCONTAINERS = ("ERROR org.testcontainers.dockerclient.DockerClientProviderStrategy - Could not find a valid Docker "
                  "environment. Please see logs and check configuration")
PSQL = "org.postgresql.util.PSQLException: Connection to localhost:5432 refused. Check that the hostname and port are correct"


def _project(tmp_path: Path, says: str, code: int = 1) -> Path:
    root = tmp_path / "shop"
    (root / ".keel").mkdir(parents=True)
    (root / ".keel" / "config.yml").write_text("commands:\n  api_test_ac: './check.sh {AC}'\n  api_test_module: './check.sh all'\n")
    script = root / "check.sh"
    (root / "says.txt").write_text(says + "\n")
    script.write_text(f'#!/bin/sh\ncat says.txt\nexit {code}\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _green(root: Path, ac_id: str = "AC-3"):
    a = ActionInput(root=str(root), phase="green", title="t", ac={"id": ac_id, "layer": "API"},
                    acs=[{"id": ac_id, "layer": "API", "status": "red"}], fake=False, flow="feature")
    return asyncio.run(actions.verify_green(a))


def test_env_down_names_docker_and_databases_only_on_a_failed_run():
    assert testcmd.env_down(1, TESTCONTAINERS).startswith("Docker is not reachable")
    assert "PostgreSQL" in testcmd.env_down(1, PSQL)
    assert "PostgreSQL" in testcmd.env_down(1, 'psycopg2.OperationalError: connection to server at "localhost" '
                                               '(127.0.0.1), port 5432 failed: Connection refused')
    assert "MySQL" in testcmd.env_down(1, "com.mysql.cj.jdbc.exceptions.CommunicationsException: Communications link failure")
    assert testcmd.env_down(1, "Error: connect ECONNREFUSED 127.0.0.1:5432")
    assert testcmd.env_down(1, "Error 111 connecting to localhost:6379. Connection refused.")
    # a pass that prints such text, a plain assertion, a refused web call and a command that cannot start are not it
    assert testcmd.env_down(0, TESTCONTAINERS) is None
    assert testcmd.env_down(1, "AssertionError: expected 3 but was 4") is None
    assert testcmd.env_down(1, "Connection refused: api.example.com:443") is None
    assert testcmd.env_down(127, "sh: 1: ./mvnw: not found") is None


def test_green_asks_the_person_instead_of_sending_it_back(tmp_path):
    res = _green(_project(tmp_path, TESTCONTAINERS))
    assert not res.ok and res.ask and res.ask["type"] == "environment"
    assert res.ask["choices"] == ["recheck", "skip", "stop"]
    assert res.ask["title"].startswith("The test environment can't start (Docker is not reachable")
    assert "this is not a test failure" in res.ask["title"] and "keel2 start --docker" in res.ask["detail"]
    info = {c["name"]: c for c in res.ask["choice_info"]}
    assert info["recheck"]["label"] == "Check again" and info["recheck"]["default"]


def test_a_real_test_failure_still_goes_back_to_the_agent(tmp_path):
    res = _green(_project(tmp_path, "FAILED tests/test_orders.py::test_ac_3 - AssertionError: 3 != 4"))
    assert not res.ok and not res.ask and res.note.endswith("does not pass yet.")


def test_the_harness_check_asks_too(tmp_path):
    root = _project(tmp_path, PSQL)
    a = ActionInput(root=str(root), phase="ac", title="t", ac=None, acs=[], fake=False, flow="harness", step="check")
    res = harness_actions.check(a)
    assert not res.ok and res.ask["type"] == "environment" and res.ask["choices"] == ["recheck", "stop"]
    assert "PostgreSQL" in res.ask["title"]


def test_a_refused_database_is_not_a_network_blip_for_repeats():
    assert repeats.transient("curl: (7) Failed to connect to example.com port 443: Connection refused")
    assert not repeats.transient(PSQL)


def test_the_preflight_warns_before_the_build(tmp_path, monkeypatch):
    root = _project(tmp_path, "ok", 0)
    a = ActionInput(root=str(root), phase="preflight", title="t", ac=None, acs=[], fake=False, flow="feature")
    monkeypatch.setattr(feature_actions, "_docker_up", lambda _root: False)
    assert feature_actions.docker_warning(a, {}) is None                  # no Testcontainers: nothing to say
    (root / "pom.xml").write_text("<project><dependency><groupId>org.testcontainers</groupId></dependency></project>")
    said = feature_actions.docker_warning(a, {})
    assert said and "Testcontainers (pom.xml)" in said and "keel2 start --docker" in said
    assert said in feature_actions.preflight_warnings(a, {})
    monkeypatch.setattr(feature_actions, "_docker_up", lambda _root: True)
    assert feature_actions.docker_warning(a, {}) is None
