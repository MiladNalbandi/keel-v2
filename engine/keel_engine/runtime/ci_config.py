"""The project's own CI config, read as the runs CI would make (keel 0.18.65, the pipeline step).

A flow passed keel's style check and tests on this machine's newest runtime, and CI failed: its style job ran on an
older runtime where the style tool flags code the local run accepted. The pipeline step (runtime/ci_pipeline.py)
runs each CI job on CI's own versions; this module only reads the config and says what those runs are.

    plan(root) -> {sources, runs, problems}

Read: .github/workflows/*.yml|yaml (jobs, runs-on, strategy.matrix with include and exclude, steps[].run, the setup
actions with their version inputs, container:, env, defaults.run.working-directory) and .gitlab-ci.yml (jobs, image:,
before_script / script / after_script, variables, parallel: matrix:, extends:, default:). Every matrix is expanded into
concrete runs (job x versions x matrix values). A run is a plain dict:

    {key, source, job, name, matrix, runtime, version, image, image_from, extensions, tools, env, steps, skip, notes,
     style}

`skip` says why keel does not run it (a deploy, release or publish job, a job that needs secrets, a job on Windows or
macOS, one that needs service containers, a manual job); "" when it runs. Secrets never reach a run: a variable whose
value names `secrets.*` makes its job skipped (GITHUB_TOKEN alone is dropped with a note). Nothing here runs anything.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import yaml

GITHUB_DIR = ".github/workflows"
GITLAB_FILE = ".gitlab-ci.yml"
MAX_RUNS = 24                         # more runs than this (a large matrix) are listed as skipped (ci.max_runs)

# a job (or workflow) by these words publishes something: keel never runs it
DEPLOY = re.compile(r"(?i)(^|[^a-z])(deploy\w*|release\w*|publish\w*)")
SECRET = re.compile(r"\bsecrets\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)|\bsecrets\s*\[")
ONLY_TOKEN = {"GITHUB_TOKEN"}         # the token GitHub gives every job: dropped, not a reason to skip
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# the events a pull request or a push runs; a workflow on other events only (a tag, a schedule) is not "CI on the change"
CI_EVENTS = {"push", "pull_request", "pull_request_target", "merge_group"}
# the style tools: a job that runs one is the style job (its result counts over keel's own style check)
STYLE = re.compile(r"(?i)php-cs-fixer|phpcs\b|phpcbf|\bpint\b|\becs\b|eslint|prettier|stylelint|\bblack\b|\bruff\b|"
                   r"flake8|isort|pycodestyle|rubocop|ktlint|spotless|checkstyle|gofmt|golangci-lint|"
                   r"(^|[^a-z])(lint|cs|code[-_ ]?style|coding[-_ ]?standards?|format(ting)?)([^a-z]|$)")

# setup actions -> the runtime they install and the input that names its version
SETUP = {
    "shivammathur/setup-php": ("php", "php-version"),
    "actions/setup-node": ("node", "node-version"),
    "actions/setup-python": ("python", "python-version"),
    "actions/setup-java": ("java", "java-version"),
    "actions/setup-go": ("go", "go-version"),
}
VERSION_FILES = {"node": "node-version-file", "python": "python-version-file", "php": "php-version-file",
                 "java": "java-version-file", "go": "go-version-file"}
# actions that change nothing a run needs here (the project is copied in; caches are the run's own)
QUIET = ("actions/checkout", "actions/cache", "actions/upload-artifact", "actions/github-script")
GITLAB_KEYS = {"stages", "variables", "image", "services", "default", "include", "workflow", "before_script",
               "after_script", "cache", "pages"}


class _Loader(yaml.SafeLoader):
    """YAML 1.1 reads `php: [7.4, 8.10]` as floats (8.10 -> 8.1): versions stay text."""


_Loader.add_constructor("tag:yaml.org,2002:float", lambda loader, node: loader.construct_scalar(node))


def load(text: str):
    return yaml.load(text, Loader=_Loader)


def _s(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def plan(root: str, max_runs: int = MAX_RUNS) -> dict:
    """{sources: [repo-relative files read], runs: [run], problems: [a file keel could not read, and why]}."""
    base = Path(root)
    sources, runs, problems = [], [], []
    wf_dir = base / GITHUB_DIR
    files = sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]) if wf_dir.is_dir() else []
    for f in files:
        rel = f.relative_to(base).as_posix()
        try:
            doc = load(f.read_text(errors="replace"))
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"{rel}: not readable YAML ({str(exc).splitlines()[0][:160]})")
            continue
        sources.append(rel)
        if isinstance(doc, dict):
            runs += github_runs(root, rel, doc)
    gl = base / GITLAB_FILE
    if gl.is_file():
        try:
            doc = load(gl.read_text(errors="replace"))
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"{GITLAB_FILE}: not readable YAML ({str(exc).splitlines()[0][:160]})")
            doc = None
        if doc is not None:
            sources.append(GITLAB_FILE)
            if isinstance(doc, dict):
                runs += gitlab_runs(root, doc)
    counted = 0
    for r in runs:
        if r["skip"]:
            continue
        counted += 1
        if counted > max_runs:
            r["skip"] = f"more than {max_runs} runs in the CI config (ci.max_runs)"
    return {"sources": sources, "runs": runs, "problems": problems}


# ------------------------------------------------------------------ matrix

def expand_matrix(matrix) -> tuple[list[dict], str]:
    """GitHub's strategy.matrix as concrete combinations: the product of the listed keys, minus `exclude` (an entry
    matches a combination when all its keys match), then `include` (an entry is added to every combination whose
    original values it does not change; one that fits none is a combination of its own). ([], why) when the matrix
    comes from an expression keel cannot read (fromJSON of another job's output)."""
    if matrix is None:
        return [{}], ""
    if isinstance(matrix, str):
        return [], f"the matrix comes from an expression ({matrix[:80]})"
    if not isinstance(matrix, dict):
        return [], "the matrix is not a mapping"
    keys, values = [], []
    for k, v in matrix.items():
        if k in ("include", "exclude"):
            continue
        if isinstance(v, str) and EXPR.search(v):
            return [], f"matrix.{k} comes from an expression ({v[:80]})"
        keys.append(str(k))
        values.append(v if isinstance(v, list) else [v])
    combos = [dict(zip(keys, c)) for c in itertools.product(*values)] if keys else []
    exclude = matrix.get("exclude") or []
    if isinstance(exclude, list):
        for ex in exclude:
            if isinstance(ex, dict):
                combos = [c for c in combos if not all(_s(c.get(k)) == _s(v) for k, v in ex.items())]
    include = matrix.get("include") or []
    if isinstance(include, str):
        return [], "matrix.include comes from an expression"
    original = set(keys)
    made = len(combos)
    for inc in include if isinstance(include, list) else []:
        if not isinstance(inc, dict):
            continue
        hit = False
        for c in combos[:made]:
            if all(_s(c.get(k)) == _s(v) for k, v in inc.items() if k in original):
                c.update(inc)
                hit = True
        if not hit:
            combos.append(dict(inc))
    return (combos or [{}]), ""


def gitlab_matrix(parallel) -> tuple[list[dict], str]:
    """GitLab's `parallel: matrix:`: each entry is the product of its keys' values; the entries' combinations add up.
    `parallel: 3` (copies of one job) is one run."""
    if not isinstance(parallel, dict):
        return [{}], ""
    m = parallel.get("matrix")
    if not isinstance(m, list):
        return [{}], ""
    out = []
    for entry in m:
        if not isinstance(entry, dict):
            continue
        keys = [str(k) for k in entry]
        vals = [v if isinstance(v, list) else [v] for v in entry.values()]
        out += [dict(zip(keys, c)) for c in itertools.product(*vals)]
    return (out or [{}]), ""


# ------------------------------------------------------------------ GitHub Actions

def _events(doc: dict) -> set[str]:
    on = doc.get("on", doc.get(True))           # YAML 1.1 reads the key `on` as true
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(x) for x in on}
    if isinstance(on, dict):
        return {str(k) for k in on}
    return set()


def _expr(text: str, ctx: dict, unresolved: list[str]) -> str:
    """`${{ matrix.php }}` and friends: matrix.*, env.*, github.workspace, runner.os / temp; anything else becomes ""
    and is noted (keel has no github context, no step outputs)."""
    def sub(m):
        e = m.group(1).strip()
        mm = re.fullmatch(r"matrix\s*(?:\.\s*([\w-]+)|\[\s*'([\w-]+)'\s*\])", e)
        if mm:
            k = mm.group(1) or mm.group(2)
            if k in ctx["matrix"]:
                return _s(ctx["matrix"][k])
        me = re.fullmatch(r"env\s*\.\s*(\w+)", e)
        if me and me.group(1) in ctx["env"]:
            return ctx["env"][me.group(1)]
        fixed = {"github.workspace": "/work", "runner.os": "Linux", "runner.temp": "/tmp", "runner.arch": "X64"}
        if e in fixed:
            return fixed[e]
        unresolved.append(e[:80])
        return ""
    return EXPR.sub(sub, str(text))


def _env_of(raw, ctx: dict, unresolved: list[str], dropped: list[str]) -> tuple[dict, list[str]]:
    """({name: value} without secrets, [the secrets other than GITHUB_TOKEN it named])."""
    out, secrets = {}, []
    if not isinstance(raw, dict):
        return out, secrets
    for k, v in raw.items():
        k = str(k)
        text = _s(v)
        names = [m.group(1) or "?" for m in SECRET.finditer(text)]
        if names or "github.token" in text:
            if set(names) - ONLY_TOKEN:
                secrets += [n for n in names if n not in ONLY_TOKEN]
            else:
                dropped.append(k)
            continue
        if ENV_NAME.match(k):
            out[k] = _expr(text, ctx, unresolved)
    return out, secrets


def _version(v: str, runtime: str) -> str:
    """CI's version words as an image tag: "20.x" -> 20, "lts/*" -> lts, "8.2" stays."""
    v = str(v or "").strip().strip("'\"")
    if not v:
        return ""
    if runtime == "node" and v.startswith("lts"):
        return "lts"
    v = re.sub(r"(\.x)+$", "", v)
    v = v.lstrip("v") if re.match(r"^v\d", v) else v
    return v


def _version_file(root: str, rel: str) -> str:
    try:
        text = (Path(root) / rel).read_text(errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line.split()[0]
    return ""


def _split_list(v) -> list[str]:
    if isinstance(v, list):
        return [_s(x).strip() for x in v if _s(x).strip()]
    return [x.strip() for x in re.split(r"[,\n]", _s(v)) if x.strip()]


def github_runs(root: str, rel: str, doc: dict) -> list[dict]:
    runs = []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return runs
    events = _events(doc)
    wf_name = _s(doc.get("name"))
    wf_skip = ""
    if DEPLOY.search(Path(rel).stem) or DEPLOY.search(wf_name):
        wf_skip = "a deploy / release / publish workflow"
    elif events and not events & CI_EVENTS:
        wf_skip = f"runs on {', '.join(sorted(events))}, not on a push or pull request"
    wf_defaults = ((doc.get("defaults") or {}).get("run") or {}) if isinstance(doc.get("defaults"), dict) else {}
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_id = str(job_id)
        strategy = job.get("strategy") if isinstance(job.get("strategy"), dict) else {}
        combos, why = expand_matrix(strategy.get("matrix"))
        if why:
            runs.append(_run(rel, job_id, _s(job.get("name")) or job_id, {}, skip=why))
            continue
        for combo in combos:
            runs.append(_github_run(root, rel, doc, job_id, job, combo, wf_skip, wf_defaults))
    return runs


def _run(source: str, job: str, name: str, matrix: dict, **kw) -> dict:
    tail = ",".join(f"{k}={_s(v)}" for k, v in matrix.items())
    r = {"key": f"{source}#{job}" + (f"[{tail}]" if tail else ""), "source": source, "job": job, "name": name,
         "matrix": {k: _s(v) for k, v in matrix.items()}, "runtime": "", "version": "", "image": "", "image_from": "",
         "extensions": [], "tools": [], "env": {}, "steps": [], "skip": "", "notes": [], "style": False}
    r.update(kw)
    return r


def _github_run(root: str, rel: str, doc: dict, job_id: str, job: dict, combo: dict, wf_skip: str, wf_defaults: dict) -> dict:
    unresolved: list[str] = []
    dropped: list[str] = []
    ctx = {"matrix": combo, "env": {}}
    wf_env, wf_secrets = _env_of(doc.get("env"), ctx, unresolved, dropped)
    ctx["env"] = dict(wf_env)
    job_env, job_secrets = _env_of(job.get("env"), ctx, unresolved, dropped)
    ctx["env"].update(job_env)
    name = _expr(_s(job.get("name")) or job_id, ctx, [])
    r = _run(rel, job_id, name, combo, env=dict(ctx["env"]))
    skip = wf_skip
    if not skip and (DEPLOY.search(job_id) or DEPLOY.search(_s(job.get("name")))):
        skip = "a deploy / release / publish job"
    if not skip and job.get("environment"):
        skip = "a deploy job (it names a GitHub environment)"
    if not skip and job.get("uses"):
        skip = f"calls another workflow ({_s(job['uses'])[:80]})"
    runs_on = _expr(_s(job.get("runs-on")), ctx, [])
    if not skip and re.search(r"(?i)windows|macos", runs_on):
        skip = f"runs on {runs_on}: keel runs Linux containers only"
    if not skip and job.get("services"):
        names = ", ".join(str(k) for k in (job["services"] or {})) if isinstance(job["services"], dict) else "services"
        skip = f"needs service containers ({names}): keel does not start them"
    secrets = list(wf_secrets) + list(job_secrets)
    cont = job.get("container")
    image = _expr(cont.get("image") if isinstance(cont, dict) else _s(cont), ctx, unresolved).strip() if cont else ""
    if isinstance(cont, dict):
        c_env, c_secrets = _env_of(cont.get("env"), ctx, unresolved, dropped)
        r["env"].update(c_env)
        secrets += c_secrets
        if cont.get("credentials"):
            secrets.append("container.credentials")
    if image:
        r.update(image=image, image_from="config")
        rt, ver = runtime_of_image(image)
        r.update(runtime=rt, version=ver)
    defaults = (((job.get("defaults") or {}).get("run") or {}) if isinstance(job.get("defaults"), dict) else {}) or wf_defaults
    for n, step in enumerate(job.get("steps") or [], 1):
        if not isinstance(step, dict):
            continue
        first = (_s(step.get("run")).strip().splitlines() or [""])[0]
        sname = _s(step.get("name")) or _s(step.get("uses")) or first or f"step {n}"
        if step.get("uses"):
            _github_uses(root, r, step, ctx, unresolved, secrets)
            continue
        if step.get("run") is None:
            continue
        s_env, s_secrets = _env_of(step.get("env"), {**ctx, "env": {**ctx["env"]}}, unresolved, dropped)
        secrets += s_secrets
        run_text = _s(step.get("run"))
        secrets += [m.group(1) or "?" for m in SECRET.finditer(run_text) if (m.group(1) or "?") not in ONLY_TOKEN]
        if "secrets.GITHUB_TOKEN" in run_text or "github.token" in run_text:
            dropped.append(f"step {n}")
        shell = _s(step.get("shell") or defaults.get("shell") or "")
        r["steps"].append({"name": _expr(sname, ctx, [])[:120], "run": _expr(run_text, ctx, unresolved),
                           "dir": _expr(_s(step.get("working-directory") or defaults.get("working-directory") or "."), ctx, unresolved),
                           "env": s_env, "allow_fail": _s(step.get("continue-on-error")) == "true",
                           "shell": shell})
    if secrets and not skip:
        skip = f"needs secrets ({', '.join(sorted(set(secrets)))[:120]}): keel never passes secrets to a run"
    if not skip and not r["steps"]:
        skip = "no run steps (only actions keel does not run)"
    if dropped:
        r["notes"].append(f"GITHUB_TOKEN is not given to the run (in {', '.join(sorted(set(dropped)))[:120]})")
    if unresolved:
        r["notes"].append("expressions keel cannot fill, left empty: " + ", ".join(sorted(set(unresolved)))[:200])
    if job.get("needs"):
        r["notes"].append(f"needs {_s(job['needs'])[:80]}: run on its own here")
    r["skip"] = skip
    r["style"] = bool(STYLE.search(job_id) or STYLE.search(r["name"]) or any(STYLE.search(s["run"]) for s in r["steps"]))
    return r


def _github_uses(root: str, r: dict, step: dict, ctx: dict, unresolved: list[str], secrets: list[str]) -> None:
    uses = _s(step.get("uses"))
    action = uses.split("@")[0].lower()
    with_ = step.get("with") if isinstance(step.get("with"), dict) else {}
    for v in with_.values():
        secrets += [m.group(1) or "?" for m in SECRET.finditer(_s(v)) if (m.group(1) or "?") not in ONLY_TOKEN]
    if action in SETUP:
        rt, key = SETUP[action]
        ver = _expr(_s(with_.get(key)), ctx, unresolved).strip()
        if not ver and with_.get(VERSION_FILES.get(rt, "")):
            ver = _version_file(root, _expr(_s(with_[VERSION_FILES[rt]]), ctx, unresolved))
        ver = _version(ver.splitlines()[0] if ver else "", rt)
        if r["runtime"] and r["runtime"] != rt:
            r["notes"].append(f"also sets up {rt} {ver or ''}".strip() + f": the image is for {r['runtime']}")
            return
        if r["image_from"] == "config":
            r["notes"].append(f"sets up {rt} {ver} inside the job's container")
            return
        r["runtime"], r["version"] = rt, ver
        if rt == "php":
            r["extensions"] = [e for e in _split_list(_expr(_s(with_.get("extensions")), ctx, unresolved))
                               if e and not e.startswith(":") and e != "none"]
            cov = _s(with_.get("coverage")).strip()
            if cov in ("pcov", "xdebug", "xdebug2") and cov not in r["extensions"]:
                r["extensions"].append("xdebug" if cov == "xdebug2" else cov)
            r["tools"] = _split_list(_expr(_s(with_.get("tools")), ctx, unresolved))
        if rt == "java" and _s(with_.get("distribution")) not in ("", "temurin", "adopt", "adopt-hotspot"):
            r["notes"].append(f"java distribution {_s(with_.get('distribution'))}: keel runs Eclipse Temurin")
        return
    if action == "ramsey/composer-install":
        deps = _s(with_.get("dependency-versions")).strip()
        opts = _expr(_s(with_.get("composer-options")), ctx, unresolved).strip()
        cmd = ("composer update --prefer-lowest --prefer-stable" if deps == "lowest" else
               "composer update" if deps == "highest" else "composer install")
        r["steps"].append({"name": "composer install (ramsey/composer-install)",
                           "run": f"{cmd} --no-interaction --no-progress" + (f" {opts}" if opts else ""),
                           "dir": _s(with_.get("working-directory") or "."), "env": {}, "allow_fail": False, "shell": ""})
        return
    if action.startswith(QUIET) or action.startswith("./"):
        if action.startswith("./"):
            r["notes"].append(f"uses the local action {uses[:80]}: not run here")
        return
    r["notes"].append(f"uses {uses[:80]}: not run here")


# ------------------------------------------------------------------ GitLab CI

def _gl_list(v) -> list[str]:
    """A script: a string or a list (lists inside lists come from YAML anchors)."""
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            out += _gl_list(x)
        return out
    return [_s(v)]


def _gl_vars(v) -> dict:
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, dict):
                x = x.get("value", "")
            if ENV_NAME.match(str(k)):
                out[str(k)] = _s(x)
    return out


def _gl_expand(text: str, env: dict) -> str:
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: env.get(m.group(1) or m.group(2), m.group(0)), text)


def _gl_job(doc: dict, name: str, seen: tuple = ()) -> dict:
    """The job with its `extends:` merged in (hidden templates first, the job's own keys last; variables merge)."""
    job = doc.get(name)
    if not isinstance(job, dict) or name in seen:
        return {}
    ext = job.get("extends")
    merged: dict = {}
    for parent in ([ext] if isinstance(ext, str) else ext if isinstance(ext, list) else []):
        p = _gl_job(doc, str(parent), (*seen, name))
        merged = {**merged, **p, "variables": {**(merged.get("variables") or {}), **(p.get("variables") or {})}}
    return {**merged, **job, "variables": {**(merged.get("variables") or {}), **(job.get("variables") or {})}}


def gitlab_runs(root: str, doc: dict) -> list[dict]:
    rel = GITLAB_FILE
    runs = []
    default = doc.get("default") if isinstance(doc.get("default"), dict) else {}
    g_vars = _gl_vars(doc.get("variables"))
    g_image = default.get("image", doc.get("image"))
    g_before = default.get("before_script", doc.get("before_script"))
    g_after = default.get("after_script", doc.get("after_script"))
    g_services = default.get("services", doc.get("services"))
    notes = ["include: is not followed (only this file is read)"] if doc.get("include") else []
    for name in doc:
        name = str(name)
        if name in GITLAB_KEYS or name.startswith("."):
            continue
        job = _gl_job(doc, name)
        if not job or not ("script" in job or "trigger" in job):
            continue
        combos, _why = gitlab_matrix(job.get("parallel"))
        for combo in combos:
            runs.append(_gitlab_run(rel, name, job, combo, g_vars, g_image, g_before, g_after, g_services, notes))
    return runs


def _gitlab_run(rel, name, job, combo, g_vars, g_image, g_before, g_after, g_services, notes) -> dict:
    env = {**g_vars, **_gl_vars(job.get("variables")), **{k: _s(v) for k, v in combo.items()}}
    r = _run(rel, name, name, combo, notes=list(notes))
    skip = ""
    stage = _s(job.get("stage"))
    if DEPLOY.search(name) or DEPLOY.search(stage):
        skip = "a deploy / release / publish job"
    elif job.get("environment"):
        skip = "a deploy job (it names an environment)"
    elif job.get("trigger"):
        skip = "starts another pipeline (trigger:)"
    elif _s(job.get("when")) in ("manual", "never"):
        skip = f"runs only by hand (when: {_s(job.get('when'))})"
    elif job.get("secrets"):
        skip = "needs secrets (secrets:): keel never passes secrets to a run"
    elif job.get("services", g_services):
        skip = "needs service containers (services:): keel does not start them"
    image = job.get("image", g_image)
    image = image.get("name") if isinstance(image, dict) else image
    image = _gl_expand(_s(image), env).strip()
    if image:
        rt, ver = runtime_of_image(image)
        r.update(image=image, image_from="config", runtime=rt, version=ver)
    elif not skip:
        r["notes"].append("no image: is named (GitLab's default image is the runner's)")
    before = _gl_list(job.get("before_script", g_before))
    script = _gl_list(job.get("script"))
    after = _gl_list(job.get("after_script", g_after))
    if before or script:
        r["steps"].append({"name": "script", "run": "\n".join(before + script), "dir": ".", "env": {}, "allow_fail":
                           _s(job.get("allow_failure")) == "true", "shell": ""})
    if after:
        r["steps"].append({"name": "after_script", "run": "\n".join(after), "dir": ".", "env": {}, "allow_fail": True,
                           "shell": ""})
    r["env"] = env
    r["skip"] = skip
    r["style"] = bool(STYLE.search(name) or STYLE.search(stage) or any(STYLE.search(s["run"]) for s in r["steps"]))
    return r


# ------------------------------------------------------------------ images

def runtime_of_image(image: str) -> tuple[str, str]:
    """("php", "7.4") from php:7.4-cli, ("node", "18") from node:18-alpine, ("java", "17") from eclipse-temurin:17-jdk.
    ("", "") when the image is not one keel knows."""
    m = re.match(r"^(?:docker\.io/)?(?:library/)?([a-z0-9._/-]+?)(?::([\w.-]+))?(?:@.*)?$", image.strip())
    if not m:
        return "", ""
    repo, tag = m.group(1), m.group(2) or ""
    name = repo.rsplit("/", 1)[-1]
    rt = {"php": "php", "node": "node", "python": "python", "eclipse-temurin": "java", "openjdk": "java",
          "golang": "go", "composer": "php"}.get(name, "")
    if not rt:
        return "", ""
    ver = re.match(r"^v?(\d+(?:\.\d+)*)", tag)
    return rt, (ver.group(1) if ver else tag)
