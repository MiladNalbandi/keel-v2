"""Which path a request is for: the same reading as api/.../common/ApiPath.kt.

`raw` is the path as it came on the wire (ASGI `raw_path`, no query). The Kotlin controllers match a cleaned path (no
`;params`, percent-decoded, no empty or `.` segments, `..` resolved); [clean] gives that one, [ambiguous] marks the forms
nobody writes on purpose (refused for the api).
"""

from __future__ import annotations

import re
from urllib.parse import unquote

_ENCODED_SEPARATOR = re.compile(r"%(2f|5c|2e|00)", re.IGNORECASE)


def clean(raw: str) -> str:
    no_params = "/".join(seg.split(";", 1)[0] for seg in raw.split("/"))
    decoded = _decode(no_params)
    out: list[str] = []
    for seg in decoded.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    trailing = "/" if decoded.endswith("/") and out else ""
    return "/" + "/".join(out) + trailing


def _decode(text: str) -> str:
    # Spring's UriUtils.decode fails on a broken escape ("%zz", a lone "%"); Kotlin then keeps the text as it is.
    if re.search(r"%(?![0-9a-fA-F]{2})", text):
        return text
    try:
        return unquote(text, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return text


def is_api(path: str) -> bool:
    """The api's own paths: the ones that need a credential."""
    return path.startswith("/api/") or path.startswith("/internal/")


def ambiguous(raw: str) -> bool:
    """A raw path with `;`, a backslash, `//`, an encoded `/` `\\` `.` or NUL, or a `.`/`..` segment."""
    return (
        ";" in raw
        or "\\" in raw
        or "//" in raw
        or bool(_ENCODED_SEPARATOR.search(raw))
        or any(seg in (".", "..") for seg in raw.split("/"))
    )


def first_part(path: str) -> str:
    return path.lstrip("/").split("/", 1)[0]
