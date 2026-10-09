from __future__ import annotations

from pathlib import Path

import pytest

from keel_plugin import cli, template

TOOL = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def run(capsys):
    """keel-plugin <argv> in this process: (exit code, stdout, stderr)."""

    def go(*argv: str) -> tuple[int, str, str]:
        code = cli.main([str(a) for a in argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    return go


@pytest.fixture
def plugin(tmp_path) -> Path:
    """A plugin folder made from the template: hello (engine, web, content)."""
    return template.new("hello", tmp_path / "src", publisher="keel")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def set_manifest(folder: Path, old: str, new: str) -> None:
    f = folder / "keel-plugin.yml"
    text = f.read_text(encoding="utf-8")
    assert old in text, old
    f.write_text(text.replace(old, new), encoding="utf-8")
