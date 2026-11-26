"""keel-plugin dev: build, pack (unsigned), docker cp into keel's /data/plugins/dev, install-file, restart, report. With
a stand-in docker (a script on PATH) and a stand-in keel (a local HTTP server); never a real keel."""

from __future__ import annotations

import functools
import http.server
import json
import os
import stat
import threading
from pathlib import Path

import pytest
from conftest import set_manifest, write

from keel_plugin import dev, template
from keel_plugin.lint import lint

FAKE_DOCKER = """#!/usr/bin/env bash
# a stand-in for docker: logs each call; inspect says the container runs (unless PB_DOCKER_DOWN is set);
# cp <file> <container>:<path> copies the file to $PB_DOCKER_ROOT/<path>
set -euo pipefail
echo "$*" >> "$PB_DOCKER_LOG"
case "$1" in
  inspect) if [ -n "${PB_DOCKER_DOWN:-}" ]; then echo "Error: No such object: $4" >&2; exit 1; fi; echo true ;;
  exec) case "$*" in *internal-token*) printf 'dev-token' ;; esac; exit 0 ;;
  cp) dst="${3#*:}"; mkdir -p "$PB_DOCKER_ROOT$(dirname "$dst")"; cp "$2" "$PB_DOCKER_ROOT$dst" ;;
  *) exit 3 ;;
esac
"""


class Keel(http.server.BaseHTTPRequestHandler):
    """A stand-in keel api: install-file, restart, the installed list and the plugin host."""

    state: dict = {}

    def log_message(self, *a, **k):
        pass

    def _send(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"null")

    def do_POST(self):
        s = self.state
        s.setdefault("tokens", []).append(self.headers.get("X-Keel-Token"))
        s["calls"].append(("POST", self.path, self._body()))
        if self.path == "/api/plugins/install-file":
            code, body = s["install"]
            return self._send(code, body)
        if self.path == "/api/plugins/restart":
            s["pending_reads"] = 2           # the old api still answers "pending" once, then keel is back
            code, body = s["restart"]
            return self._send(code, body)
        self._send(404, {"error": "no such route"})

    def do_GET(self):
        s = self.state
        s.setdefault("tokens", []).append(self.headers.get("X-Keel-Token"))
        s["calls"].append(("GET", self.path, None))
        if self.path == "/api/plugins/installed":
            s["pending_reads"] = max(0, s.get("pending_reads", 0) - 1)
            return self._send(200, {"plugins": [], "restart": {"pending": s["pending_reads"] > 0}})
        if self.path == "/api/plugin-host":
            return self._send(200, s["host"])
        self._send(404, {"error": "no such route"})


@pytest.fixture
def keel():
    state = {"calls": [], "install": (200, {"name": "notes", "version": "0.1.0", "title": "Notes", "source": "file"}),
             "restart": (202, {"restarting": True, "scheduled": False, "running": 0}),
             "host": {"sdk": 1, "mode": "on", "problems": [],
                      "plugins": [{"name": "notes", "title": "Notes", "version": "0.1.0", "source": "file",
                                   "parts": ["content"]}]}}
    handler = type("K", (Keel,), {"state": state})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """The stand-in docker on PATH: (its log, the container's file system root)."""
    bin_dir = tmp_path / "bin"
    exe = write(bin_dir / "docker", FAKE_DOCKER)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    log, root = tmp_path / "docker.log", tmp_path / "container"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PB_DOCKER_LOG", str(log))
    monkeypatch.setenv("PB_DOCKER_ROOT", str(root))
    monkeypatch.delenv("PB_DOCKER_DOWN", raising=False)
    monkeypatch.delenv("KEEL_NAME", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("KEEL_INTERNAL_TOKEN_FILE", raising=False)
    monkeypatch.setattr(dev.time, "sleep", lambda s: None)
    return log, root


@pytest.fixture
def notes(tmp_path) -> Path:
    """A content-only plugin from keel-plugin new --parts content."""
    return template.new("notes", tmp_path / "src", publisher="ana-k", parts=template.parts_of("content"))


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.is_file() else []


def test_dev_puts_a_content_only_plugin_into_keel_unsigned_and_says_what_loaded(notes, keel, docker, run):
    log, root = docker
    code, out, err = run("dev", notes, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 0, out + err
    assert calls(log) == ["inspect -f {{.State.Running}} keel-dev", "exec keel-dev mkdir -p /data/plugins/dev",
                          f"cp {calls(log)[2].split()[1]} keel-dev:/data/plugins/dev/notes-0.1.0.kplug",
                          "exec keel-dev cat /data/run/internal-token"]
    # keel 0.18.1 answers no caller without a credential: every call carries keel's internal token
    assert set(keel["tokens"]) == {"dev-token"}
    copied = root / "data/plugins/dev/notes-0.1.0.kplug"
    report = lint(copied)
    assert report.ok and report.manifest.trust() == "content"
    assert not copied.with_name(copied.name + ".minisig").exists()          # unsigned
    posts = [(path, body) for method, path, body in keel["calls"] if method == "POST"]
    assert posts == [("/api/plugins/install-file", {"path": "/data/plugins/dev/notes-0.1.0.kplug", "force": True}),
                     ("/api/plugins/restart", {"now": True})]
    assert [p for m, p, _ in keel["calls"] if m == "GET"][-1] == "/api/plugin-host"
    assert "pack     notes-0.1.0.kplug (" in out and "trust content" in out
    assert "unsigned: this .kplug has no signature" in out
    assert "install  Notes 0.1.0: installed from the file" in out
    assert "loaded   Notes 0.1.0 · parts: content · from file" in out
    assert "its workflows: notes-flow" in out and "its agents: notes-agent" in out
    assert out.rstrip().splitlines()[-1].startswith("note     notes 0.1.0 is unsigned")


def test_a_native_keels_token_comes_from_the_file_keel_internal_token_file_names(notes, keel, docker, run, monkeypatch, tmp_path):
    log, root = docker
    f = tmp_path / "token"
    f.write_text("native-token\n")
    monkeypatch.setenv("KEEL_INTERNAL_TOKEN_FILE", str(f))
    code, out, err = run("dev", notes, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 0, out + err
    assert set(keel["tokens"]) == {"native-token"}
    assert not any("internal-token" in c for c in calls(log))           # not read from the container


def test_the_container_is_given_else_keel_name_else_keel_v2(monkeypatch):
    monkeypatch.delenv("KEEL_NAME", raising=False)
    assert dev.container_name(None) == "keel-v2"
    monkeypatch.setenv("KEEL_NAME", "my-keel")
    assert dev.container_name(None) == "my-keel"
    assert dev.container_name("keel-dev") == "keel-dev"


def test_dev_stops_before_docker_when_lint_fails(notes, keel, docker, run):
    log, _ = docker
    write(notes / "content/link-target.txt", "x")
    (notes / "content/a-link").symlink_to(notes / "content/link-target.txt")
    code, _out, err = run("dev", notes, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 1 and "may not hold links" in err
    assert calls(log) == [] and keel["calls"] == []


def test_dev_says_when_there_is_no_keel_container(notes, keel, docker, run, monkeypatch):
    monkeypatch.setenv("PB_DOCKER_DOWN", "1")
    monkeypatch.setenv("KEEL_NAME", "keel-dev")
    code, _out, err = run("dev", notes, "--keel", keel["url"])
    assert code == 1 and "there is no running keel container called keel-dev" in err
    assert keel["calls"] == []


def test_dev_shows_keels_refusal_and_a_restart_it_cannot_do(notes, keel, docker, run):
    keel["install"] = (409, {"error": "Notes asks for extra Python libraries.", "hint": "keel does not install them yet."})
    code, _out, err = run("dev", notes, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 1 and "keel did not install it (409): Notes asks for extra Python libraries." in err
    keel["install"] = (200, {"name": "notes", "version": "0.1.0", "title": "Notes"})
    keel["restart"] = (409, {"error": "keel-start does not run this keel"})
    code, _out, err = run("dev", notes, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 1 and "keel cannot restart itself (keel-start does not run this keel)" in err


def test_dev_reports_a_plugin_keel_left_out(notes, keel, docker, run):
    keel["host"] = {"sdk": 1, "mode": "on", "plugins": [],
                    "problems": [{"name": "notes", "version": "0.1.0", "error": "needs keel >=9.0.0, this is keel 0.15.6"}]}
    code, out, _ = run("dev", notes, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 1 and "left out notes 0.1.0: needs keel >=9.0.0, this is keel 0.15.6" in out


def test_dev_says_when_keel_does_not_answer(notes, docker, run):
    code, _out, err = run("dev", notes, "--keel", "http://127.0.0.1:9", "--container", "keel-dev")
    assert code == 1 and "keel does not answer at http://127.0.0.1:9" in err


def test_a_web_part_must_be_built_or_built_with_keel_src(tmp_path, keel, docker, run):
    src = template.new("pages", tmp_path / "src", parts=template.parts_of("web,content"))
    set_manifest(src, "web: { entry: web/index.js }", "web: { entry: web/dist/index.js }")
    code, _out, err = run("dev", src, "--keel", keel["url"], "--container", "keel-dev")
    assert code == 1 and "parts.web.entry 'web/dist/index.js' is missing" in err and "--keel-src" in err


BUILD = """#!/usr/bin/env bash
# a stand-in for keel's scripts/build-plugin.sh <plugin-dir> <out-dir>: checks the plugin is inside this checkout's
# plugins/, then stages it with a built web/dist/index.js
set -euo pipefail
dir="$1" out="$2"
case "$dir" in "$(pwd)/plugins/"*) ;; *) echo "not in plugins/: $dir" >&2; exit 1 ;; esac
name="$(basename "$dir")"
stage="$out/$name/0.1.0"
mkdir -p "$stage/web/dist"
cp "$dir/keel-plugin.yml" "$stage/"
cp -R "$dir/content" "$stage/content"
echo "export default { name: '$name' }" > "$stage/web/dist/index.js"
echo "$dir" > "$(pwd)/built-from"
"""


def test_keel_src_builds_the_plugin_in_a_keel_checkout_and_leaves_no_copy(tmp_path, keel, docker, run):
    checkout = tmp_path / "keel"
    script = write(checkout / "scripts/build-plugin.sh", BUILD)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (checkout / "plugins").mkdir()
    src = template.new("pages", tmp_path / "src", parts=template.parts_of("web,content"))
    set_manifest(src, "web: { entry: web/index.js }", "web: { entry: web/dist/index.js }")
    write(src / "node_modules/x/index.js", "big")                     # never copied into keel's checkout
    keel["install"] = (200, {"name": "pages", "version": "0.1.0", "title": "Pages"})
    keel["host"]["plugins"] = [{"name": "pages", "title": "Pages", "version": "0.1.0", "source": "file",
                                "parts": ["web", "content"]}]
    code, out, err = run("dev", src, "--keel", keel["url"], "--container", "keel-dev", "--keel-src", checkout)
    assert code == 0, out + err
    assert (checkout / "built-from").read_text().strip() == str((checkout / "plugins/pages").resolve())
    assert not (checkout / "plugins/pages").exists()                   # the copy is gone after the build
    report = lint(docker[1] / "data/plugins/dev/pages-0.1.0.kplug")
    assert report.ok and report.manifest.trust() == "web"
    assert "loaded   Pages 0.1.0 · parts: web, content · from file" in out
    # keel's own plugin of that name is never written over
    (checkout / "plugins/pages").mkdir()
    code, _out, err = run("dev", src, "--keel", keel["url"], "--container", "keel-dev", "--keel-src", checkout)
    assert code == 1 and "keel has a plugin called pages" in err
    code, _out, err = run("dev", src, "--keel-src", tmp_path / "nothing")
    assert code == 1 and "is not a keel checkout" in err


def test_new_parts_makes_a_content_only_plugin_with_an_example(tmp_path, run):
    code, _out, err = run("new", "notes", "--dir", tmp_path, "--publisher", "ana-k", "--parts", "content")
    assert code == 0, err
    root = tmp_path / "notes"
    names = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert names == [".gitignore", "README.md", "content/README.md", "content/agents/notes-agent.md",
                     "content/workflows/notes-flow.yaml", "keel-plugin.yml"]
    code, out, _ = run("lint", root)
    assert code == 0 and "trust: content" in out and "ok: no problems found" in out
    code, _out, err = run("new", "other", "--dir", tmp_path, "--parts", "engine,api")
    assert code == 1 and "choose from engine, web, content" in err
    assert template.parts_of("content, web") == ("web", "content")
