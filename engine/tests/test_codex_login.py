import json
import os
import stat

from keel_engine.models import login_keys
from keel_engine.models.cli import codex_login_env, safe_env


def test_codex_auth_goes_to_a_private_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEL_DATA", str(tmp_path))
    auth = json.dumps({"tokens": {"access_token": "abc"}})
    env = codex_login_env({"codex_auth": auth})
    home = tmp_path / "codex-home"
    assert env == {"CODEX_HOME": str(home.resolve())}
    f = home / "auth.json"
    assert f.read_text() == auth
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert safe_env(env)["CODEX_HOME"] == str(home.resolve())


def test_no_codex_auth_leaves_the_users_own_login():
    assert codex_login_env({}) == {}
    assert codex_login_env(None) == {}


def test_provider_test_key_is_the_codex_login_in_subscription_mode():
    assert login_keys("codex", "subscription", "{}") == {"codex_auth": "{}"}
    assert login_keys("codex", "api", "sk-x") == {}
