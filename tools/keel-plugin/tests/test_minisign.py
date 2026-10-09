"""Signatures: the contract's test vector (13-step4-contract.md section 1), keygen -> sign -> verify, and what a
changed file, a changed comment or a wrong key give."""

from __future__ import annotations

import base64
import os
import stat

import pytest

from keel_plugin import minisign
from keel_plugin.minisign import PublicKey, SecretKey, SignatureError

# the contract's test key: never use it for anything else
SECRET = "keel-secret-key:v1:AQIDBAUGBwgBAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fIA=="  # keel:allow-secret (test vector)
PUBLIC = "RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk"
MESSAGE = b"keel test vector\n"
TRUSTED = "timestamp:1760000000\tfile:test.txt"
SIG_ED = (
    "untrusted comment: signature from keel test key\n"
    "RUQBAgMEBQYHCPMsy8ifCtByWeNYRTEvnCTCIhujuPevW5G+lHgNDw8QgmEgvAp4zen2Aysfolw/3msiNED7JFa6kbT1lG7TSwU=\n"
    "trusted comment: timestamp:1760000000\tfile:test.txt\n"
    "KvuInzyRMTOZtuQBooVaFuyCbaAOwzqjLOmHymXKBN0myaUecmFw6gtPh1HKKeNI37p/PNztORc9ihkjzQjCBQ==\n"
)
SIG_ED_LEGACY = (
    "untrusted comment: signature from keel test key\n"
    "RWQBAgMEBQYHCAMQu/HNJ1rnfV7dwPTWKv8tRNvZdEjh8SFqAKpijxpMIGBWkJ95aCohGjnPF61JPY8V6TM0ZCIkivEz1SZOUgA=\n"
    "trusted comment: timestamp:1760000000\tfile:test.txt\n"
    "ym42po2TeCBYbkcsPHxkwODxoFVPPAiAgScygyn2Bjf5jWJHgqybPJ1wd31HR1tADSfUFmaPit5AUrorO7H3Ag==\n"
)


# ---------------------------------------------------------------- the test vector

def test_vector_message_is_17_bytes_and_the_trusted_comment_has_a_tab():
    assert len(MESSAGE) == 17
    assert "\t" in TRUSTED


def test_vector_secret_key_gives_the_public_key():
    secret = SecretKey.parse(SECRET)
    assert secret.public().line() == PUBLIC
    assert secret.key_id == bytes(range(1, 9))


def test_vector_hashed_signature_ED_verifies():
    key = minisign.verify(MESSAGE, SIG_ED, [PublicKey.parse(PUBLIC)])
    assert key.line() == PUBLIC


def test_vector_legacy_signature_Ed_verifies():
    key = minisign.verify(MESSAGE, SIG_ED_LEGACY, [PublicKey.parse(PUBLIC)])
    assert key.line() == PUBLIC


def test_vector_signing_gives_exactly_the_ED_signature():
    text = minisign.sign(MESSAGE, SecretKey.parse(SECRET), "test.txt", timestamp=1760000000,
                         untrusted="signature from keel test key")
    assert text == SIG_ED


def test_vector_signing_by_the_cli_gives_the_same_signature_line(tmp_path, run, monkeypatch):
    f = tmp_path / "test.txt"
    f.write_bytes(MESSAGE)
    monkeypatch.setenv("PB_TEST_KEY", SECRET)
    code, out, err = run("sign", f, "--key-env", "PB_TEST_KEY", "--comment", "signature from keel test key")
    assert code == 0, err
    lines = (tmp_path / "test.txt.minisig").read_text().splitlines()
    assert lines[0] == "untrusted comment: signature from keel test key"
    assert lines[1] == SIG_ED.splitlines()[1]   # Ed25519 is deterministic; the signature does not hold the time
    assert lines[2].startswith("trusted comment: timestamp:") and lines[2].endswith("\tfile:test.txt")
    assert SECRET not in out + err
    code, out, _ = run("verify", f, "--pub", PUBLIC)
    assert code == 0 and "ok: test.txt is signed by key 0807060504030201" in out


def test_the_public_key_from_a_pub_file():
    key = PublicKey.parse(f"untrusted comment: minisign public key 0807060504030201\n{PUBLIC}\n")
    assert key.line() == PUBLIC and key.id == "0807060504030201"
    assert PublicKey.parse(key.pub_file()) == key


# ---------------------------------------------------------------- changes are found

@pytest.mark.parametrize("sig", [SIG_ED, SIG_ED_LEGACY])
def test_a_changed_byte_in_the_file_fails(sig):
    with pytest.raises(SignatureError, match="does not match the file"):
        minisign.verify(b"keel test vector!", sig, [PublicKey.parse(PUBLIC)])


def test_a_changed_trusted_comment_fails():
    bad = SIG_ED.replace("timestamp:1760000000", "timestamp:1760000001")
    with pytest.raises(SignatureError, match="trusted comment"):
        minisign.verify(MESSAGE, bad, [PublicKey.parse(PUBLIC)])


def test_a_wrong_global_signature_fails():
    lines = SIG_ED.splitlines()
    lines[3] = SIG_ED_LEGACY.splitlines()[3]
    with pytest.raises(SignatureError, match="trusted comment"):
        minisign.verify(MESSAGE, "\n".join(lines) + "\n", [PublicKey.parse(PUBLIC)])


def test_another_key_fails_and_names_the_key_ids():
    other = SecretKey.generate().public()
    with pytest.raises(SignatureError, match="signed with key 0807060504030201, which is not one of the keys"):
        minisign.verify(MESSAGE, SIG_ED, [other])


def test_a_key_with_the_same_id_but_other_bytes_fails():
    other = SecretKey(bytes(range(1, 9)), os.urandom(32)).public()
    with pytest.raises(SignatureError, match="does not match"):
        minisign.verify(MESSAGE, SIG_ED, [other])


@pytest.mark.parametrize("text", ["", "one line\n", SIG_ED.replace("trusted comment:", "comment:"),
                                  SIG_ED.replace("RUQB", "!!!!")])
def test_a_broken_signature_file_is_named(text):
    with pytest.raises(SignatureError):
        minisign.verify(MESSAGE, text, [PublicKey.parse(PUBLIC)])


@pytest.mark.parametrize("bad", ["", "RWQ", "keel-secret-key:v1:AAAA", "keel-secret-key:v1:%%%",
                                 "untrusted comment: minisign encrypted secret key\nRWRTY0Iy"])
def test_a_broken_secret_key_is_refused_without_showing_it(bad):
    with pytest.raises(SignatureError) as exc:
        SecretKey.parse(bad)
    if bad.strip():
        assert bad.strip() not in str(exc.value)


def test_secret_key_repr_hides_the_seed():
    secret = SecretKey.parse(SECRET)
    assert secret.seed.hex() not in repr(secret)
    assert SECRET.split(":")[-1] not in repr(secret)


# ---------------------------------------------------------------- keygen -> sign -> verify

def test_keygen_sign_verify(tmp_path, run):
    code, out, err = run("keygen", "pub", "--dir", tmp_path / "keys")
    assert code == 0, err
    key_file, pub_file = tmp_path / "keys/pub.key", tmp_path / "keys/pub.pub"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    secret_text = key_file.read_text().strip()
    assert secret_text.startswith(minisign.SECRET_PREFIX)
    assert secret_text not in out + err                    # never printed
    public = PublicKey.parse(pub_file.read_text())
    assert public.line() in out                            # the public key is
    assert pub_file.read_text().startswith("untrusted comment: ")

    f = tmp_path / "db-1.0.0.kplug"
    f.write_bytes(os.urandom(5000))
    code, out, err = run("sign", f, "--key-file", key_file)
    assert code == 0, err
    assert secret_text not in out + err
    sig = (tmp_path / "db-1.0.0.kplug.minisig").read_text()
    assert base64.b64decode(sig.splitlines()[1])[:2] == b"ED"   # hashed with BLAKE2b
    assert f"\tfile:{f.name}" in sig.splitlines()[2]
    code, out, _ = run("verify", f, "--pub", pub_file)
    assert code == 0 and f"signed by key {public.id}" in out
    code, out, _ = run("verify", f, "--pub", public.line())
    assert code == 0

    # tampering: one changed byte
    data = bytearray(f.read_bytes())
    data[100] ^= 1
    f.write_bytes(bytes(data))
    code, out, _ = run("verify", f, "--pub", pub_file)
    assert code == 1 and "not ok" in out and "does not match the file" in out


def test_verify_with_several_keys_finds_the_right_one(tmp_path, run):
    run("keygen", "a", "--dir", tmp_path)
    run("keygen", "b", "--dir", tmp_path)
    f = tmp_path / "x.txt"
    f.write_text("x")
    run("sign", f, "--key-file", tmp_path / "b.key")
    code, out, _ = run("verify", f, "--pub", tmp_path / "a.pub", "--pub", tmp_path / "b.pub")
    assert code == 0
    code, out, _ = run("verify", f, "--pub", tmp_path / "a.pub")
    assert code == 1 and "which is not one of the keys" in out


def test_keygen_does_not_write_over_a_key(tmp_path, run):
    assert run("keygen", "k", "--dir", tmp_path)[0] == 0
    before = (tmp_path / "k.key").read_text()
    code, _, err = run("keygen", "k", "--dir", tmp_path)
    assert code == 1 and "is there already" in err
    assert (tmp_path / "k.key").read_text() == before
    assert run("keygen", "k", "--dir", tmp_path, "--force")[0] == 0
    assert (tmp_path / "k.key").read_text() != before
    assert stat.S_IMODE((tmp_path / "k.key").stat().st_mode) == 0o600


def test_sign_needs_a_key(tmp_path, run, monkeypatch):
    f = tmp_path / "x.txt"
    f.write_text("x")
    monkeypatch.delenv("PB_NO_SUCH_KEY", raising=False)
    code, _, err = run("sign", f, "--key-env", "PB_NO_SUCH_KEY")
    assert code == 1 and "PB_NO_SUCH_KEY is not set" in err
    code, _, err = run("sign", f)
    assert code == 1 and "--key-env VAR or --key-file FILE" in err
    monkeypatch.setenv("PB_BAD_KEY", "keel-secret-key:v1:c2VjcmV0")
    code, _, err = run("sign", f, "--key-env", "PB_BAD_KEY")
    assert code == 1 and "broken" in err and "c2VjcmV0" not in err
    assert not (tmp_path / "x.txt.minisig").exists()


def test_an_open_key_file_gets_a_warning(tmp_path, run):
    run("keygen", "k", "--dir", tmp_path)
    os.chmod(tmp_path / "k.key", 0o644)
    f = tmp_path / "x.txt"
    f.write_text("x")
    code, _, err = run("sign", f, "--key-file", tmp_path / "k.key")
    assert code == 0 and "chmod 600" in err
