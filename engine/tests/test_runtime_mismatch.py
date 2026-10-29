"""keel 0.18.75 this machine's runtime against the versions CI runs (a field report: the container's PHP 8.4, CI's 8.1 to
8.3, and php-cs-fixer, which does not support 8.4, failed on every commit)."""

from keel_engine.runtime import ci_config

WF = """on: [pull_request]
jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        php: ['8.1', '8.2', '8.3']
    steps:
      - uses: shivammathur/setup-php@v2
        with: { php-version: '${{ matrix.php }}' }
      - run: composer test
"""


def project(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text(WF)
    return str(tmp_path)


def test_a_local_runtime_ci_does_not_run_is_named(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setattr(ci_config, "local_version", lambda rt, root=None: "8.4" if rt == "php" else None)
    assert ci_config.runtime_mismatch(root) == ["php 8.4 here; CI runs 8.1, 8.2, 8.3"]
    monkeypatch.setattr(ci_config, "local_version", lambda rt, root=None: "8.2")
    assert ci_config.runtime_mismatch(root) == []
    monkeypatch.setattr(ci_config, "local_version", lambda rt, root=None: None)     # not installed here: nothing to say
    assert ci_config.runtime_mismatch(root) == []


def test_no_ci_file_says_nothing(tmp_path):
    assert ci_config.runtime_mismatch(str(tmp_path)) == []
