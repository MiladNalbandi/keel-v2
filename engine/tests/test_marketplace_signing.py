"""Signatures in the minisign format (keel_engine/marketplace/signing.py, docs/plugins/13-step4-contract.md §1): the
contract's test vector, and every way a signature can be wrong."""

import base64

import pytest

from keel_engine.marketplace import signing
from keel_engine.marketplace.signing import PublicKey, SecretKey, SignatureError

SECRET = "keel-secret-key:v1:AQIDBAUGBwgBAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fIA=="  # the contract's test key; keel:allow-secret
PUBLIC = "RWQBAgMEBQYHCHm1Vi6P5lT5QHixEuipi6eQH4U65pW+1+DjkQutBJZk"
MESSAGE = b"keel test vector\n"
ED = ("untrusted comment: signature from keel test key\n"
      "RUQBAgMEBQYHCPMsy8ifCtByWeNYRTEvnCTCIhujuPevW5G+lHgNDw8QgmEgvAp4zen2Aysfolw/3msiNED7JFa6kbT1lG7TSwU=\n"
      "trusted comment: timestamp:1760000000\tfile:test.txt\n"
      "KvuInzyRMTOZtuQBooVaFuyCbaAOwzqjLOmHymXKBN0myaUecmFw6gtPh1HKKeNI37p/PNztORc9ihkjzQjCBQ==\n")
LEGACY = ("untrusted comment: signature from keel test key\n"
          "RWQBAgMEBQYHCAMQu/HNJ1rnfV7dwPTWKv8tRNvZdEjh8SFqAKpijxpMIGBWkJ95aCohGjnPF61JPY8V6TM0ZCIkivEz1SZOUgA=\n"
          "trusted comment: timestamp:1760000000\tfile:test.txt\n"
          "ym42po2TeCBYbkcsPHxkwODxoFVPPAiAgScygyn2Bjf5jWJHgqybPJ1wd31HR1tADSfUFmaPit5AUrorO7H3Ag==\n")


def lines(text: str) -> list[str]:
    return text.splitlines()


def flip(b64: str, at: int) -> str:
    """The same base64 with one byte changed."""
    raw = bytearray(base64.b64decode(b64))
    raw[at] ^= 0x01
    return base64.b64encode(bytes(raw)).decode()


# ------------------------------------------------------------------ the test vector

def test_the_secret_key_gives_the_public_key():
    key = SecretKey.parse(SECRET)
    assert key.public().line() == PUBLIC
    assert key.public() == PublicKey.parse(PUBLIC)
    assert key.text() == SECRET
    assert key.public().id == "0807060504030201"          # minisign prints the key id as a little-endian number


def test_signing_the_message_gives_exactly_the_ed_signature():
    assert MESSAGE == b"keel test vector\n" and len(MESSAGE) == 17
    got = signing.sign(MESSAGE, SECRET, "test.txt", timestamp=1760000000, comment="signature from keel test key")
    assert got == ED


@pytest.mark.parametrize("sig", [ED, LEGACY], ids=["ED", "Ed"])
def test_both_signatures_of_the_vector_verify(sig, tmp_path):
    assert signing.verify(MESSAGE, sig, [PUBLIC]) == "0807060504030201"
    f = tmp_path / "test.txt"
    f.write_bytes(MESSAGE)
    assert signing.verify(f, sig, [PublicKey.parse(PUBLIC)]) == "0807060504030201"   # a file is read from its path


def test_a_pub_file_and_crlf_line_ends_read_too():
    assert PublicKey.parse(f"untrusted comment: minisign public key 0807060504030201\n{PUBLIC}\n").line() == PUBLIC
    assert signing.verify(MESSAGE, ED.replace("\n", "\r\n"), [PUBLIC])
    assert signing.parse_minisig(ED).trusted_comment == "timestamp:1760000000\tfile:test.txt"


# ------------------------------------------------------------------ what must fail

@pytest.mark.parametrize("sig", [ED, LEGACY], ids=["ED", "Ed"])
def test_a_changed_byte_in_the_file_fails(sig):
    with pytest.raises(SignatureError, match="does not match the file"):
        signing.verify(b"keel test vector!", sig, [PUBLIC])


@pytest.mark.parametrize("sig", [ED, LEGACY], ids=["ED", "Ed"])
def test_a_changed_byte_in_the_signature_fails(sig):
    parts = lines(sig)
    parts[1] = flip(parts[1], 20)
    with pytest.raises(SignatureError, match="does not match the file"):
        signing.verify(MESSAGE, "\n".join(parts), [PUBLIC])


def test_a_signature_by_another_key_id_fails():
    other = signing.keygen().public()
    with pytest.raises(SignatureError, match="key 0807060504030201, which is not one of the keys"):
        signing.verify(MESSAGE, ED, [other])
    parts = lines(ED)
    parts[1] = flip(parts[1], 2)                       # the key id inside the signature
    with pytest.raises(SignatureError, match="not one of the keys"):
        signing.verify(MESSAGE, "\n".join(parts), [PUBLIC])


def test_another_key_with_the_same_key_id_fails():
    impostor = PublicKey(SecretKey.parse(SECRET).key_id, signing.keygen().public().key)
    with pytest.raises(SignatureError, match="does not match"):
        signing.verify(MESSAGE, ED, [impostor])


def test_a_changed_trusted_comment_or_global_signature_fails():
    changed = ED.replace("file:test.txt", "file:other.txt")
    with pytest.raises(SignatureError, match="trusted comment was changed"):
        signing.verify(MESSAGE, changed, [PUBLIC])
    parts = lines(ED)
    parts[3] = flip(parts[3], 5)
    with pytest.raises(SignatureError, match="global signature does not match"):
        signing.verify(MESSAGE, "\n".join(parts), [PUBLIC])


@pytest.mark.parametrize("text, why", [
    ("", "not in the minisign format"),
    ("untrusted comment: x\nnot base64!\ntrusted comment: t\nAAAA\n", "not valid base64"),
    ("untrusted comment: x\nAAAA\ntrusted comment: t\nAAAA\n", "has 3 bytes, a minisign one has 74"),
    (ED.replace("trusted comment:", "comment:"), "not in the minisign format"),
])
def test_a_broken_signature_file_says_why(text, why):
    with pytest.raises(SignatureError, match=why):
        signing.verify(MESSAGE, text, [PUBLIC])


def test_an_unknown_algorithm_is_refused():
    raw = bytearray(base64.b64decode(lines(ED)[1]))
    raw[0:2] = b"XX"
    parts = lines(ED)
    parts[1] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(SignatureError, match="unknown algorithm"):
        signing.verify(MESSAGE, "\n".join(parts), [PUBLIC])


@pytest.mark.parametrize("text", ["", "abc", base64.b64encode(b"Xx" + bytes(40)).decode(), f"{PUBLIC}\n{PUBLIC}"])
def test_a_broken_public_key_is_refused(text):
    with pytest.raises(SignatureError):
        PublicKey.parse(text)


@pytest.mark.parametrize("text", ["", "keel-secret-key:v2:AAAA", "keel-secret-key:v1:AAAA"])
def test_a_broken_secret_key_is_refused(text):
    with pytest.raises(SignatureError):
        SecretKey.parse(text)


def test_a_new_key_signs_and_verifies_and_never_shows_its_seed(tmp_path):
    key = signing.keygen()
    f = tmp_path / "db-1.0.0.kplug"
    f.write_bytes(b"\x1f\x8b" + bytes(1000))
    sig = signing.sign(f, key, "db-1.0.0.kplug\tx\n", timestamp=5)
    assert lines(sig)[2] == "trusted comment: timestamp:5\tfile:db-1.0.0.kplug x"     # no tab or line break gets in
    assert signing.verify(f, sig, [signing.keygen().public(), key.public().line()]) == key.public().id
    assert key.text() not in repr(key) and key.seed.hex() not in repr(key)
