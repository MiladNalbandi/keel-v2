"""keel 0.18.75 keel's commits signed with an SSH key keel2 mounts (KEEL_SIGNING_KEY), and the key out of agents' reach."""

from keel_engine import rules
from keel_engine.runtime.actions import commit_args


def test_keels_commits_are_signed_when_a_key_is_mounted(tmp_path, monkeypatch):
    key = tmp_path / "signing-key"
    key.write_text("not a real key\n")
    monkeypatch.delenv("KEEL_SIGNING_KEY", raising=False)
    who, _ = commit_args(str(tmp_path), None, {})
    assert "commit.gpgsign=true" not in who
    monkeypatch.setenv("KEEL_SIGNING_KEY", str(key))
    who, _ = commit_args(str(tmp_path), None, {})
    assert who[-6:] == ["-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "-c", "commit.gpgsign=true"]


def test_the_mounted_key_is_one_of_keels_own_files():
    assert rules.keel_key_file("/run/keel/signing-key")
    assert rules.KEEL_KEY_WORDS.search("cat /run/keel/signing-key")
