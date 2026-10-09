"""Ed25519 signatures in the minisign format, and keel's secret key format (13-step4-contract.md, section 1).

    public key   base64("Ed" + key_id[8] + public_key[32]); a .pub file is "untrusted comment: …" + that line
    .minisig     untrusted comment / base64(alg[2] + key_id[8] + signature[64]) / trusted comment / base64(global[64])
                 alg "ED": the signature is over BLAKE2b-512(file) (minisign's default), "Ed": over the file (legacy)
                 the global signature is over signature[64] + the trusted comment's text
    secret key   "keel-secret-key:v1:" + base64(key_id[8] + ed25519_seed[32])  (keel's own; never printed)

keel verifies both kinds and signs "ED".
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import PluginToolError

SECRET_PREFIX = "keel-secret-key:v1:"
KEY_ALG = b"Ed"
HASHED = b"ED"
LEGACY = b"Ed"
UNTRUSTED = "untrusted comment: "
TRUSTED = "trusted comment: "
DEFAULT_UNTRUSTED = "signature from keel-plugin secret key"


class SignatureError(PluginToolError):
    """A key or a signature is broken, or a signature does not match."""


def _b64(text: str, what: str, size: int) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError(f"{what} is not valid base64") from None
    if len(raw) != size:
        raise SignatureError(f"{what} has {len(raw)} bytes; it must have {size}")
    return raw


def _id_hex(key_id: bytes) -> str:
    """A key id as minisign shows it: the 8 bytes as a little-endian number, in hex."""
    return key_id[::-1].hex().upper()


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    raw: bytes

    @classmethod
    def parse(cls, text: str) -> PublicKey:
        """A key line ("RWQ…"), or the text of a .pub file (the untrusted comment line is skipped)."""
        lines = [ln.strip() for ln in text.strip().splitlines()
                 if ln.strip() and not ln.strip().startswith("untrusted comment:")]
        if len(lines) != 1:
            raise SignatureError("a public key is one line like RWQ… (or a .pub file: a comment line, then the key line)")
        raw = _b64(lines[0], "the public key", 42)
        if raw[:2] != KEY_ALG:
            raise SignatureError("the public key is not an Ed25519 minisign key (it must start with RW)")
        return cls(raw[2:10], raw[10:])

    @classmethod
    def load(cls, value: str) -> PublicKey:
        """A key line, or the path of a .pub file."""
        try:
            is_file = Path(value).is_file()
        except (OSError, ValueError):   # a key line is not a usable path
            is_file = False
        return cls.parse(Path(value).read_text(encoding="utf-8") if is_file else value)

    @property
    def id(self) -> str:
        return _id_hex(self.key_id)

    def line(self) -> str:
        return base64.b64encode(KEY_ALG + self.key_id + self.raw).decode()

    def pub_file(self) -> str:
        return f"{UNTRUSTED}minisign public key {self.id}\n{self.line()}\n"

    def _key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self.raw)


@dataclass(frozen=True)
class SecretKey:
    key_id: bytes
    seed: bytes = field(repr=False)

    @classmethod
    def generate(cls) -> SecretKey:
        return cls(os.urandom(8), os.urandom(32))

    @classmethod
    def parse(cls, text: str, where: str = "the secret key") -> SecretKey:
        """keel's secret key line. Errors never show the key."""
        text = (text or "").strip()
        if not text:
            raise SignatureError(f"{where} is empty")
        if not text.startswith(SECRET_PREFIX):
            raise SignatureError(f"{where} is not a keel secret key (it must start with {SECRET_PREFIX}); "
                                 "minisign's own encrypted key files are not read")
        try:
            raw = base64.b64decode(text[len(SECRET_PREFIX):], validate=True)
        except (binascii.Error, ValueError):
            raise SignatureError(f"{where} is broken (not base64 after {SECRET_PREFIX})") from None
        if len(raw) != 40:
            raise SignatureError(f"{where} is broken (it must hold 40 bytes: a key id and a seed)")
        return cls(raw[:8], raw[8:])

    def text(self) -> str:
        return SECRET_PREFIX + base64.b64encode(self.key_id + self.seed).decode()

    def public(self) -> PublicKey:
        raw = self._key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return PublicKey(self.key_id, raw)

    def _key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(self.seed)


def _clean_comment(text: str) -> str:
    """A comment stays on its line: no line breaks (a tab is kept: keel's trusted comment has one)."""
    return re.sub(r"[\r\n]+", " ", text)


def sign(data: bytes, secret: SecretKey, file_name: str, *, timestamp: int | None = None,
         untrusted: str = DEFAULT_UNTRUSTED) -> str:
    """The text of a .minisig for data: alg ED, trusted comment "timestamp:<unix seconds>\\tfile:<file name>"."""
    key = secret._key()
    signature = key.sign(hashlib.blake2b(data, digest_size=64).digest())
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    trusted = _clean_comment(f"timestamp:{stamp}\tfile:{Path(file_name).name}")
    global_sig = key.sign(signature + trusted.encode("utf-8"))
    return (f"{UNTRUSTED}{_clean_comment(untrusted)}\n"
            f"{base64.b64encode(HASHED + secret.key_id + signature).decode()}\n"
            f"{TRUSTED}{trusted}\n"
            f"{base64.b64encode(global_sig).decode()}\n")


@dataclass(frozen=True)
class Signature:
    alg: bytes
    key_id: bytes
    signature: bytes
    trusted: str
    global_sig: bytes

    @classmethod
    def parse(cls, text: str) -> Signature:
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        while lines and lines[-1] == "":
            lines.pop()
        if len(lines) != 4 or not lines[0].startswith("untrusted comment:") or not lines[2].startswith(TRUSTED):
            raise SignatureError("the signature file is not in the minisign format (four lines: untrusted comment, "
                                 "signature, trusted comment, global signature)")
        raw = _b64(lines[1], "the signature", 74)
        if raw[:2] not in (HASHED, LEGACY):
            raise SignatureError("the signature is not an Ed25519 minisign signature")
        return cls(raw[:2], raw[2:10], raw[10:], lines[2][len(TRUSTED):], _b64(lines[3], "the global signature", 64))

    @property
    def key_id_hex(self) -> str:
        return _id_hex(self.key_id)


def verify(data: bytes, minisig_text: str, keys: list[PublicKey]) -> PublicKey:
    """The key that signed data; SignatureError in plain words when no key in keys did, or something was changed."""
    sig = Signature.parse(minisig_text)
    key = next((k for k in keys if k.key_id == sig.key_id), None)
    if key is None:
        listed = ", ".join(k.id for k in keys) or "none"
        raise SignatureError(f"the file was signed with key {sig.key_id_hex}, which is not one of the keys it is "
                             f"checked with ({listed})")
    message = hashlib.blake2b(data, digest_size=64).digest() if sig.alg == HASHED else data
    try:
        key._key().verify(sig.signature, message)
    except InvalidSignature:
        raise SignatureError("the signature does not match the file (the file was changed, or the signature is "
                             "not for this file)") from None
    try:
        key._key().verify(sig.global_sig, sig.signature + sig.trusted.encode("utf-8"))
    except InvalidSignature:
        raise SignatureError("the trusted comment of the signature was changed (the global signature does not "
                             "match)") from None
    return key


def secret_from(key_env: str | None = None, key_file: str | None = None) -> SecretKey:
    """The secret key from an environment variable or a file (exactly one). Errors never show the key."""
    if bool(key_env) == bool(key_file):
        raise SignatureError("give the secret key with --key-env VAR or --key-file FILE (one of them)")
    if key_env:
        value = os.environ.get(key_env)
        if value is None or not value.strip():
            raise SignatureError(f"the environment variable {key_env} is not set (it must hold the secret key)")
        return SecretKey.parse(value, f"the secret key in {key_env}")
    path = Path(key_file)
    if not path.is_file():
        raise SignatureError(f"there is no key file {key_file}")
    return SecretKey.parse(path.read_text(encoding="utf-8"), f"the secret key in {path.name}")


def key_file_is_open(path: Path) -> bool:
    """Whether others than the owner may read a key file (it should have mode 0600)."""
    return os.name == "posix" and bool(path.stat().st_mode & 0o077)
