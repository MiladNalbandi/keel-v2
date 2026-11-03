"""0.21.3 agents run as their own user.

keel's api and engine run as the user `keel`. Before 0.21.3 the agents (the coding-agent CLIs, the project's own test and
build commands, a stack's setup.run, KeelBot's shell) ran as `keel` too, so the only thing between an agent and keel's
own files (/data/run/internal-token, /data/master.key, keel.db, /data/plugins) and the api's environment
(/proc/<pid>/environ) was the guard's check of the command text. A command the check did not see through
(`cat /data/ru[n]/internal-token`, a script it wrote first) read them.

In keel's image (docker/keel-start) every one of those now starts as the user KEEL_AGENT_USER names (`agent`):

    sudo -n -E -u agent -- /usr/local/bin/keel-as-agent <program> <args>

/etc/sudoers.d/keel-agent lets `keel` run only that helper, only as `agent` (a user with fewer rights than keel's own,
never root). The helper sets umask 002 and the PATH keel gives it (sudo's secure_path replaces PATH), then execs the
program. `-E` keeps the environment the engine prepared (SETENV in the rule): nothing goes on a command line.

    - The agent's primary group is keel's group: the project folders are shared through it (files group rw, see share).
    - keel's own files are not shared: keel-start makes /data 0710, the secrets 0600/0700, keel's home 0700, and runs
      the api and the engine with umask 077. Only the folders the agents need are opened to the group (keel-start).
    - A different uid: the agent cannot read /proc/<pid>/environ of the api or the engine, nor signal them.
    - keel cannot signal the agent's processes either (sudo stays between them), so a stop goes through the same
      helper as the agent (kill_group).

What the agents' user still has (by design, or because nothing in between can take it away):
    - the CLI's own login: CLAUDE_CODE_OAUTH_TOKEN (claude), GH_TOKEN / COPILOT_GITHUB_TOKEN (copilot) in the CLI's
      environment, codex's auth.json in CODEX_HOME, opencode's auth.json in its home. The shell commands the model runs
      are the CLI's children, as the same user, so they can read it (rules.CLI_LOGIN refuses a command that names it;
      Claude Code's CLAUDE_CODE_SUBPROCESS_ENV_SCRUB would need bubblewrap in the container and is not turned on).
      A project's own commands that keel runs (tests, builds: agent_tools.command_env) get no key and no token.
    - keel's MCP read token (KEEL_MCP_TOKEN in mcp.json, for keel's read-only MCP server).
    - with keel2 --docker, the host's Docker socket (keel-stack-setup agent-docker): Docker reaches everything of the
      host, keel's data volume too.

Native mode (keel2 start --native) is unchanged: there is no second user on the person's computer, and agents run as
the person, as before (KEEL_NATIVE=1 turns this off). So is a container where keel-start found no working sudo rule
(it says so in the log and does not set KEEL_AGENT_USER).

Stdlib only.
"""

from __future__ import annotations

import os
import pwd
import signal
import subprocess
from pathlib import Path

USER_ENV = "KEEL_AGENT_USER"
HELPER = "/usr/local/bin/keel-as-agent"
PATH_ENV = "KEEL_AGENT_PATH"          # the PATH the helper sets (sudo replaces PATH with its secure_path)
NICE_ENV = "KEEL_AGENT_NICE"          # the helper starts the program with this nice value (Settings › Resources)
# never in an agent's environment, whatever the caller passed (the api's key to everything)
NEVER = ("KEEL_INTERNAL_TOKEN", "KEEL_INTERNAL_TOKEN_FILE")
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
SHARE_TIMEOUT = 120


def user() -> str | None:
    """The agents' own user, or None: agents run as keel's user (native mode, no such user, or keel-start turned it
    off)."""
    name = os.environ.get(USER_ENV, "").strip()
    if not name or os.environ.get("KEEL_NATIVE") == "1" or os.name != "posix":
        return None
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        return None
    if pw.pw_uid == os.getuid():
        return None
    return name


def enabled() -> bool:
    return user() is not None


def home() -> str:
    """The agents' home folder (their CLIs' own settings live there), or keel's own when agents run as keel."""
    u = user()
    if u:
        return pwd.getpwnam(u).pw_dir
    return os.path.expanduser("~")


def _own_home() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        return os.path.expanduser("~")


def agent_env(env: dict | None) -> dict:
    """The environment for the agent's program: `env` as the caller made it (os.environ without its keys when None),
    never keel's internal token, and the agent's own HOME, USER and LOGNAME in place of keel's."""
    if env is None:
        env = {k: v for k, v in os.environ.items() if not any(h in k.upper() for h in SECRET_HINTS)}
    out = {k: v for k, v in env.items() if k not in NEVER}
    u = user()
    if u:
        if not out.get("HOME") or out.get("HOME") == _own_home():
            out["HOME"] = home()
        out["USER"] = out["LOGNAME"] = u
    return out


def wrap(argv: list[str], env: dict | None = None, nice: int | None = None) -> tuple[list[str], dict | None]:
    """(argv, env) that start `argv` as the agents' user. Unchanged when agents run as keel's user (env None stays
    None: the child inherits, as before)."""
    u = user()
    if not u:
        return list(argv), env
    e = agent_env(env)
    e[PATH_ENV] = e.get("PATH") or os.defpath
    if nice:
        e[NICE_ENV] = str(int(nice))
    return ["sudo", "-n", "-E", "-u", u, "--", HELPER, *argv], e


def shell(command: str) -> list[str]:
    """A shell command as an argv for wrap (Popen's shell=True runs /bin/sh -c too)."""
    return ["/bin/sh", "-c", command]


def kill_group(pgid: int, sig: int = signal.SIGKILL) -> None:
    """Signals the process group `pgid` (a child started with start_new_session). keel's own signal reaches the sudo in
    front; the agent's processes behind it get the signal from the agent's user, through the helper."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    u = user()
    if not u:
        return
    try:
        subprocess.run(["sudo", "-n", "-u", u, "--", HELPER, "kill", f"-{int(sig)}", "--", f"-{int(pgid)}"],
                       capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


# ------------------------------------------------------------------ files the agents and keel both work on

# Every file group rw and every folder group rwx, symlinks left alone (chmod would follow them out of the folder), on
# this file system only, and only the runner's own files (chmod of another user's file fails anyway).
def _find_argv(root: str, uid: int) -> list[str]:
    return ["find", root, "-xdev", "!", "-type", "l", "-uid", str(uid),
            "(", "(", "-type", "d", "!", "-perm", "-g=rwx", ")", "-o", "(", "!", "-type", "d", "!", "-perm", "-g=rw", ")", ")",
            "-exec", "chmod", "g+rwX", "{}", "+"]


def share(root: str | None, *, keel: bool = True, agent: bool = True) -> None:
    """Makes the project folder `root` workable for both users: what keel wrote (umask 077, its git) group rw for the
    agent, and what the agent wrote with a mode of its own (a 0600 temp file renamed into place) group rw for keel.
    Nothing when agents run as keel's user."""
    u = user()
    if not u or not root or not os.path.isdir(root):
        return
    root = os.path.realpath(root)
    if keel:
        _passable(os.path.dirname(root))
    try:
        if keel:
            subprocess.run(_find_argv(root, os.getuid()), capture_output=True, timeout=SHARE_TIMEOUT,
                           stdin=subprocess.DEVNULL)
        if agent:
            argv, env = wrap(_find_argv(root, pwd.getpwnam(u).pw_uid), {"PATH": os.environ.get("PATH", os.defpath)})
            subprocess.run(argv, capture_output=True, timeout=SHARE_TIMEOUT, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _passable(folder: str) -> None:
    """The agents' user may pass through keel's folders above a shared one (a temp folder made 0700, the data folder):
    group x only, never r or w."""
    me = os.getuid()
    while folder and folder != os.path.dirname(folder):
        try:
            st = os.stat(folder)
            if st.st_uid == me and not st.st_mode & 0o010:
                os.chmod(folder, (st.st_mode & 0o7777) | 0o010)
        except OSError:
            pass
        folder = os.path.dirname(folder)


def readable(folder: str | Path | None) -> None:
    """keel's files for one run (the guard context, the hook's settings, mcp.json, the command wrappers): readable by
    the agent's user, never writable by it (group r-X, no group w; the folder stays keel's). Nothing when agents run
    as keel's user."""
    if not enabled() or not folder or not os.path.isdir(folder):
        return
    _passable(os.path.dirname(os.path.realpath(folder)))
    for base, dirs, files in os.walk(folder):
        for name in [base] + [os.path.join(base, f) for f in files]:
            try:
                st = os.lstat(name)
                if st.st_uid != os.getuid() or os.path.islink(name):
                    continue
                mode = st.st_mode & 0o7777
                want = (mode | 0o040 | (0o010 if mode & 0o100 or os.path.isdir(name) else 0)) & ~0o022 & ~0o007
                if want != mode:
                    os.chmod(name, want)
            except OSError:
                pass


def private_mode() -> int:
    """The mode of a file keel writes for one run that the agent's hook reads (the guard context): 0640 when agents
    run as their own user (they read it through keel's group and cannot change it), else 0600."""
    return 0o640 if enabled() else 0o600


def shared(path: str | Path | None) -> None:
    """A folder of keel's the agents read and write (the CLIs' homes under /data: agent-home, codex-home): group rwX on
    it and on what keel wrote in it."""
    if not enabled() or not path or not os.path.exists(path):
        return
    share(str(path), keel=True, agent=False)


def copy_login(rel: str) -> None:
    """A CLI login file keel keeps in its own home (`rel`, relative to it) copied into the agents' home, group rw, so the
    CLI finds it when it runs as the agents' user. Nothing when agents run as keel's user or keel has no such file."""
    if not enabled():
        return
    src, dst = Path(_own_home()) / rel, Path(home()) / rel
    try:
        data = src.read_bytes()
    except OSError:
        return
    try:
        if dst.exists() and dst.read_bytes() == data:
            return
    except OSError:
        dst.unlink(missing_ok=True)         # the CLI rewrote it with a mode of its own
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        share(str(Path(home()) / Path(rel).parts[0]), agent=False)
    except OSError:
        pass
