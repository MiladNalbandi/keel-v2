"""Signatures in the minisign format, Ed25519 (docs/plugins/13-step4-contract.md §1).

    public key     one line: base64("Ed" + key_id[8] + public_key[32]); a .pub file has an untrusted comment above it
    <file>.minisig untrusted comment: <text>
                   base64(alg[2] + key_id[8] + signature[64])     alg "ED": over BLAKE2b-512(file); "Ed": over the file
                   trusted comment: <text>
                   base64(global_signature[64])                   over signature[64] + the trusted comment's text
    keel's secret  keel-secret-key:v1: + base64(key_id[8] + ed25519_seed[32]) (keel's own tools; never in a repo or a log)

keel verifies both algorithms and the global signature, and signs "ED" with the trusted comment
`timestamp:<unix seconds>\\tfile:<file name>`. A publisher may use the minisign tool instead.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SECRET_PREFIX = "keel-secret-key:v1:"
UNTRUSTED = "untrusted comment: "
TRUSTED = "trusted comment: "
ALG_HASHED, ALG_LEGACY = b"ED", b"Ed"
CHUNK = 1 << 20


class SignatureError(ValueError):
    """A key or a signature that is broken, or a signature that does not match: the message says which, in plain words."""


def _b64(text: str, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(f"{what} is not valid base64") from exc
    if len(raw) != size:
        raise SignatureError(f"{what} has {len(raw)} bytes, a minisign one has {size}")
    return raw


def key_id_text(key_id: bytes) -> str:
    """A key id as minisign prints it: 16 hex digits of the little-endian number."""
    return key_id[::-1].hex().upper()


@dataclass(frozen=True)
class PublicKey:
    key_id: bytes      # 8 bytes
    key: bytes         # 32 bytes, the Ed25519 public key

    @classmethod
    def parse(cls, text: str) -> "PublicKey":
        """A key line, or a whole .pub file (its untrusted comment line is skipped)."""
        lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
        if lines and lines[0].startswith(UNTRUSTED.strip()):
            lines = lines[1:]
        if len(lines) != 1:
            raise SignatureError("a public key is one line of base64 (the second line of a minisign .pub file)")
        raw = _b64(lines[0], 42, "the public key")
        if raw[:2] != ALG_LEGACY:
            raise SignatureError("the public key is not an Ed25519 minisign key (it does not start with 'Ed')")
        return cls(raw[2:10], raw[10:])

    @property
    def id(self) -> str:
        return key_id_text(self.key_id)

    def line(self) -> str:
        return base64.b64encode(ALG_LEGACY + self.key_id + self.key).decode()


@dataclass(frozen=True)
class SecretKey:
    key_id: bytes      # 8 bytes
    seed: bytes        # 32 bytes, the Ed25519 private key seed

    @classmethod
    def parse(cls, text: str) -> "SecretKey":
        text = str(text or "").strip()
        if not text.startswith(SECRET_PREFIX):
            raise SignatureError(f"a keel secret key starts with '{SECRET_PREFIX}'")
        raw = _b64(text[len(SECRET_PREFIX):], 40, "the secret key")
        return cls(raw[:8], raw[8:])

    def text(self) -> str:
        return SECRET_PREFIX + base64.b64encode(self.key_id + self.seed).decode()

    def _private(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(self.seed)

    def public(self) -> PublicKey:
        raw = self._private().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return PublicKey(self.key_id, raw)

    def __repr__(self) -> str:      # never print the seed by accident
        return f"SecretKey(id={key_id_text(self.key_id)})"


def keygen() -> SecretKey:
    """A new key pair (tests, and keel's own tools): a random key id and seed."""
    return SecretKey(secrets.token_bytes(8), secrets.token_bytes(32))


@dataclass(frozen=True)
class Minisig:
    alg: bytes
    key_id: bytes
    signature: bytes
    trusted_comment: str
    global_signature: bytes
    untrusted_comment: str = ""


def parse_minisig(text: str) -> Minisig:
    """The four lines of a .minisig file."""
    lines = [x.rstrip("\r") for x in str(text or "").split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) != 4 or not lines[0].startswith(UNTRUSTED) or not lines[2].startswith(TRUSTED):
        raise SignatureError("the signature file is not in the minisign format (four lines: untrusted comment, "
                             "signature, trusted comment, global signature)")
    raw = _b64(lines[1], 74, "the signature")
    if raw[:2] not in (ALG_HASHED, ALG_LEGACY):
        raise SignatureError(f"the signature uses an unknown algorithm {raw[:2]!r} (keel knows ED and Ed)")
    return Minisig(raw[:2], raw[2:10], raw[10:], lines[2][len(TRUSTED):], _b64(lines[3], 64, "the global signature"),
                   lines[0][len(UNTRUSTED):])


def _blake2b(data: bytes | Path) -> bytes:
    h = hashlib.blake2b(digest_size=64)
    if isinstance(data, Path):
        with open(data, "rb") as fh:
            while chunk := fh.read(CHUNK):
                h.update(chunk)
    else:
        h.update(data)
    return h.digest()


def _keys(keys: Iterable[PublicKey | str]) -> list[PublicKey]:
    out = []
    for k in keys:
        out.append(k if isinstance(k, PublicKey) else PublicKey.parse(k))
    return out


def verify(data: bytes | Path, minisig_text: str, keys: Iterable[PublicKey | str]) -> str:
    """Check a file (its bytes, or its path) against its .minisig with one of these keys. Returns the key id that
    signed it; raises SignatureError in plain words when anything does not match."""
    sig = parse_minisig(minisig_text)
    key = next((k for k in _keys(keys) if k.key_id == sig.key_id), None)
    if key is None:
        raise SignatureError(f"the signature was made with key {key_id_text(sig.key_id)}, which is not one of the "
                             "keys keel trusts here")
    pub = Ed25519PublicKey.from_public_bytes(key.key)
    if sig.alg == ALG_HASHED:
        message = _blake2b(data)
    else:
        message = data.read_bytes() if isinstance(data, Path) else data
    try:
        pub.verify(sig.signature, message)
    except InvalidSignature:
        raise SignatureError("the signature does not match the file and the key") from None
    try:
        pub.verify(sig.global_signature, sig.signature + sig.trusted_comment.encode("utf-8"))
    except InvalidSignature:
        raise SignatureError("the signature's trusted comment was changed (its global signature does not match)") from None
    return key.id


def trusted_comment(file_name: str, timestamp: int | None = None) -> str:
    name = " ".join(str(file_name).split())       # no tab or line break inside the comment
    return f"timestamp:{int(time.time()) if timestamp is None else int(timestamp)}\tfile:{name}"


def sign(data: bytes | Path, secret: SecretKey | str, file_name: str, *, timestamp: int | None = None,
         comment: str = "signature from keel secret key") -> str:
    """A .minisig text ("ED": over BLAKE2b-512 of the file), with the trusted comment timestamp:<n>\\tfile:<name>."""
    key = secret if isinstance(secret, SecretKey) else SecretKey.parse(secret)
    private = key._private()
    signature = private.sign(_blake2b(data))
    trusted = trusted_comment(file_name, timestamp)
    global_signature = private.sign(signature + trusted.encode("utf-8"))
    untrusted = " ".join(str(comment).split())
    return (f"{UNTRUSTED}{untrusted}\n{base64.b64encode(ALG_HASHED + key.key_id + signature).decode()}\n"
            f"{TRUSTED}{trusted}\n{base64.b64encode(global_signature).decode()}\n")
