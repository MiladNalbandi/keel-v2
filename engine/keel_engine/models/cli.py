"""Running coding-agent CLIs (claude, codex, copilot, opencode) safely.

Subscription mode means the CLI uses the login of your plan. If an API key is in its environment,
the CLI quietly bills the API instead, so subscription mode gives the child only a safe set of
variables (no keys, no tokens). The prompt goes through stdin where the CLI allows it, the child
gets its own process group, and a timeout or a stop kills the whole group.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import signal
from typing import Callable

from .base import ModelError

KILL_GRACE = 10

SAFE_ENV_VARS = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ",
                 "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
                 "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"}
SAFE_ENV_PREFIXES = ("LC_", "XDG_", "CLAUDE_CONFIG", "CODEX_HOME", "COPILOT_", "OPENCODE_")

# Never passed to a subscription-mode child, even when a prefix above would match.
SECRET_VARS = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY", "GITHUB_TOKEN",
               "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "KEEL_INTERNAL_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"}


def claude_login_env(keys: dict | None = None) -> dict:
    """The subscription login for the claude child: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`).

    StartThread.keys["claude_oauth"] wins over the engine's environment. API keys are never passed:
    with one in its environment the CLI bills the API instead of the plan.
    """
    tok = (keys or {}).get("claude_oauth") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    return {"CLAUDE_CODE_OAUTH_TOKEN": tok} if tok else {}


def copilot_login_env(keys: dict | None = None) -> dict:
    """GitHub token for the Copilot CLI child only: keys["copilot"], else GH_TOKEN / COPILOT_GITHUB_TOKEN (GITHUB_TOKEN last)."""
    tok = (keys or {}).get("copilot")
    if tok:
        return {"GH_TOKEN": tok, "COPILOT_GITHUB_TOKEN": tok}
    out = {k: os.environ[k] for k in ("GH_TOKEN", "COPILOT_GITHUB_TOKEN") if os.environ.get(k)}
    if not out and os.environ.get("GITHUB_TOKEN"):
        out["GH_TOKEN"] = os.environ["GITHUB_TOKEN"]
    return out

def codex_login_env(keys: dict | None = None) -> dict:
    """ChatGPT login for the codex child: keys["codex_auth"] is the content of ~/.codex/auth.json (saved in keel's
    database). It is written to a private CODEX_HOME under $KEEL_DATA so the user's own ~/.codex is never touched."""
    auth = (keys or {}).get("codex_auth")
    if not auth:
        return {}
    home = Path(os.environ.get("KEEL_DATA", "./.data")).resolve() / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    f = home / "auth.json"
    if not f.exists() or f.read_text() != auth:
        f.write_text(auth)
        os.chmod(f, 0o600)
    return {"CODEX_HOME": str(home)}


INSTALL_HINT = {
    "claude": "Install Claude Code: npm install -g @anthropic-ai/claude-code, then run `claude` and /login.",
    "codex": "Install Codex: npm install -g @openai/codex, then `codex login`.",
    "copilot": "Install the Copilot CLI: npm install -g @github/copilot, then `copilot` and /login.",
    "opencode": "Install OpenCode: npm install -g opencode-ai, then `opencode auth login` (GitHub Copilot).",
}
LOGIN_HINT = {
    "claude": "Run `claude setup-token` on your computer and save the token in Connections › Claude login token (or ./keel2 token claude).",
    "codex": "Run `codex login` on your computer and paste ~/.codex/auth.json in Connections › Codex login (or ./keel2 token codex).",
    "copilot": "Save a GitHub token with Copilot access in Connections › GitHub token for Copilot (or ./keel2 token copilot).",
    "opencode": "Run `opencode auth login` and pick GitHub Copilot.",
}


def find(tool: str) -> str:
    path = os.environ.get(f"KEEL_{tool.upper()}_BIN") or shutil.which(tool)
    if not path:
        raise ModelError(f"`{tool}` is not installed or not on PATH.", INSTALL_HINT.get(tool, ""))
    return path


def safe_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if (k in SAFE_ENV_VARS or k.startswith(SAFE_ENV_PREFIXES)) and k not in SECRET_VARS}
    env.update(extra or {})
    return env


LIMIT_RE = re.compile(r"usage limit|rate limit|\b429\b|limit reached|quota|too many requests")
AUTH_RE = re.compile(r"not logged in|please run /login|unauthori[sz]ed|\b401\b|invalid api key|authentication|codex login")


def _result_line(stdout: str) -> dict | None:
    """The CLI's final JSON result (claude stream-json: {"type": "result", "subtype": ..., "num_turns": ...})."""
    for line in reversed(stdout.strip().splitlines()[-20:]):
        line = line.strip()
        if line.startswith("{") and '"result"' in line:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "result":
                return data
    return None


def _last_assistant_text(stdout: str) -> str:
    """The last thing the CLI said (claude prints "Not logged in · Please run /login" as an assistant message)."""
    for line in reversed(stdout.strip().splitlines()[-30:]):
        if line.lstrip().startswith("{") and '"assistant"' in line:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                    return b["text"].strip()[:300]
    return ""


def result_usage(res: dict | None) -> dict:
    """Token use from a CLI's final result line (claude): counted even when the run failed."""
    u = (res or {}).get("usage") or {}
    if not u:
        return {}
    return {"tokens_in": int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0)),
            "tokens_out": int(u.get("output_tokens", 0)), "tokens_cached": int(u.get("cache_read_input_tokens", 0)),
            "cost_usd": float((res or {}).get("total_cost_usd") or 0.0)}


def classify_failure(tool: str, stdout: str, stderr: str, code) -> ModelError:
    err = _classify(tool, stdout, stderr, code)
    err.usage = result_usage(_result_line(stdout))
    return err


def _classify(tool: str, stdout: str, stderr: str, code) -> ModelError:
    res = _result_line(stdout)
    if res and (res.get("subtype") == "error_max_turns" or res.get("terminal_reason") == "max_turns"):
        n = res.get("num_turns")
        return ModelError(f"The agent used all its turns{f' ({n})' if n else ''} before it finished.",
                          "Approve to try again (it starts fresh), or give this agent more turns: maxTurns in its agent file.")
    if res and res.get("subtype") == "error_during_execution":
        msg = str(res.get("result") or res.get("error") or "").strip()[:300]
        return ModelError(f"`{tool}` stopped with an error{': ' + msg if msg else ''}.", "Approve to try again.")
    # JSON lines (token counts, model names) are left out of the text checks: "inputTokens":429 is not HTTP 429.
    plain_out = "\n".join(l for l in stdout.splitlines() if not l.lstrip().startswith("{"))
    said = ""
    if res:
        said = str(res.get("result") or "; ".join(str(x) for x in res.get("errors") or []) or "")
    if not said:
        said = _last_assistant_text(stdout)
    plain_out = f"{plain_out}\n{said}".strip()
    short = (stderr.strip() or plain_out.strip())[-600:] or f"no message (exit code {code})"
    tail = f"{plain_out[-3000:]}\n{stderr[-3000:]}".lower()
    if LIMIT_RE.search(tail):
        return ModelError(f"`{tool}` hit a usage limit: {short}", "Wait for the limit to reset, or switch this agent to another model.")
    if AUTH_RE.search((stderr if stderr.strip() else plain_out)[-3000:].lower()):
        return ModelError(f"`{tool}` is not logged in inside the container.", LOGIN_HINT.get(tool, ""))
    return ModelError(f"`{tool}` exited with code {code}: {short}")


async def run_cli(tool: str, argv: list[str], *, stdin: str = "", cwd: str | None = None, env: dict | None = None,
                  timeout: int = 1800, on_line: Callable[[str], None] | None = None) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=16 * 1024 * 1024)

    def kill(sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    out_lines: list[str] = []
    err_chunks: list[bytes] = []

    async def read_out():
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip("\n")
            out_lines.append(line)
            if on_line:
                try:
                    on_line(line)
                except Exception:  # a parser bug must not kill the run
                    pass

    async def read_err():
        assert proc.stderr
        err_chunks.append(await proc.stderr.read())

    async def feed():
        assert proc.stdin
        if stdin:
            proc.stdin.write(stdin.encode())
            await proc.stdin.drain()
        proc.stdin.close()

    try:
        await asyncio.wait_for(asyncio.gather(feed(), read_out(), read_err(), proc.wait()), timeout=timeout)
    except asyncio.TimeoutError:
        kill(signal.SIGTERM)
        await asyncio.sleep(0)
        asyncio.get_running_loop().call_later(KILL_GRACE, kill, signal.SIGKILL)
        raise ModelError(f"`{tool}` did not finish within {timeout}s and was stopped.")
    except asyncio.CancelledError:
        kill(signal.SIGTERM)
        asyncio.get_running_loop().call_later(KILL_GRACE, kill, signal.SIGKILL)
        raise
    stdout, stderr = "\n".join(out_lines), b"".join(err_chunks).decode(errors="replace")
    if proc.returncode != 0:
        raise classify_failure(tool, stdout, stderr, proc.returncode)
    return stdout, stderr
