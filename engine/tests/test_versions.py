"""One version for all of keel: the engine, the api, the web app and the keel2 script
(docs/plugins/07-step1-contract.md §9). A plugin's `requires.keel` is checked against it."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _match(pattern: str, path: Path) -> str | None:
    m = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def versions(root: Path) -> dict[str, str | None]:
    """Where each version lives -> the version found there (None when it is not found)."""
    pyproject = tomllib.loads((root / "engine/pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    return {
        "engine/keel_engine/config.py (VERSION)": _match(r'^VERSION\s*=\s*"([^"]*)"', root / "engine/keel_engine/config.py"),
        "engine/pyproject.toml ([project] version)": pyproject.get("project", {}).get("version"),
        "api/build.gradle.kts (version = ...)": _match(r'^version\s*=\s*"([^"]*)"', root / "api/build.gradle.kts"),
        "keel2 (KEEL2_VERSION=...)": _match(r'^KEEL2_VERSION="?([^"\s]*)"?', root / "keel2"),
        "web/package.json (\"version\")": package.get("version"),
    }


def mismatch(found: dict[str, str | None]) -> str | None:
    """A plain message when the versions differ (or one is missing), else None."""
    if len(set(found.values())) == 1 and None not in found.values():
        return None
    width = max(len(where) for where in found)
    rows = "\n".join(f"  {where.ljust(width)}  {v if v is not None else '(not found)'}" for where, v in found.items())
    return "keel's version must be the same in all these places. Set them all to the new version:\n" + rows


def test_every_part_of_keel_has_the_same_version():
    problem = mismatch(versions(ROOT))
    assert problem is None, problem


def test_a_different_version_is_listed_with_every_place(tmp_path):
    for name, text in {
        "engine/keel_engine/config.py": 'VERSION = "1.2.0"   # keel release\n',
        "engine/pyproject.toml": '[project]\nname = "x"\nversion = "1.2.0"\n',
        "api/build.gradle.kts": 'group = "keel"\nversion = "1.2.0"\n',
        "keel2": '#!/bin/sh\nKEEL2_VERSION="1.2.0"\n',
        "web/package.json": '{"name": "w", "version": "0.1.0"}',
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    found = versions(tmp_path)
    assert set(found.values()) == {"1.2.0", "0.1.0"}
    msg = mismatch(found)
    assert msg.startswith("keel's version must be the same in all these places")
    assert all(where in msg for where in found)
    assert re.search(r'web/package\.json \("version"\)\s+0\.1\.0', msg)
    found["keel2 (KEEL2_VERSION=...)"] = None
    assert "(not found)" in mismatch(found)
    assert mismatch(dict.fromkeys(found, "1.2.0")) is None
