"""GKE sign-in without gcloud. A GKE context's kubeconfig user runs `gke-gcloud-auth-plugin`, which needs gcloud and a gcloud login.
When the api sends a Google service-account key with a call (Cluster › Connect › Google Cloud: the Vertex AI connection's key or a key
for clusters only), keel answers that exec call itself: kubectl runs with keel's own `gke-gcloud-auth-plugin` first on its PATH, and
that small program prints the access token keel made from the key (an ExecCredential, the exec plugin protocol). The kubeconfig is
not changed; the token lives in kubectl's environment for that one run and is never written to disk."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from pathlib import Path

from .kube import ClusterError

PLUGIN = "gke-gcloud-auth-plugin"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email"]
HINT_KEY = "Paste the service account's JSON key again in Cluster › Connect › Google Cloud, or choose the Vertex AI connection's key."
ROLE_HINT = ("Give the account a GKE role in the cluster's Google Cloud project: Kubernetes Engine Viewer to read, Kubernetes Engine "
             "Developer to run commands and change things.")

_KEY: ContextVar[str | None] = ContextVar("cluster_google_key", default=None)
_LOCK = threading.Lock()
_CREDS: dict[str, object] = {}

SHIM = '''#!{python} -I
"""keel's gke-gcloud-auth-plugin: prints the Google access token keel made for this kubectl run (KEEL_GKE_TOKEN)."""
import json, os, sys
token = os.environ.get("KEEL_GKE_TOKEN")
if not token:
    sys.stderr.write("keel has no Google token for this cluster: set one in Cluster > Connect > Google Cloud.\\n")
    sys.exit(1)
try:
    info = json.loads(os.environ.get("KUBERNETES_EXEC_INFO") or "{{}}")
except ValueError:
    info = {{}}
status = {{"token": token}}
if os.environ.get("KEEL_GKE_EXPIRY"):
    status["expirationTimestamp"] = os.environ["KEEL_GKE_EXPIRY"]
print(json.dumps({{"apiVersion": info.get("apiVersion") or "client.authentication.k8s.io/v1beta1", "kind": "ExecCredential",
                  "status": status}}))
'''


@contextmanager
def signin(key: str | None):
    """The Google key for the kubectl runs inside: the api sends it with each call (none = the kubeconfig's own sign-in)."""
    t = _KEY.set((key or "").strip() or None)
    try:
        yield
    finally:
        _KEY.reset(t)


def key() -> str | None:
    return _KEY.get()


def parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except (TypeError, ValueError):
        raise ClusterError(400, "The Google key is not JSON.", HINT_KEY) from None
    if not isinstance(info, dict) or info.get("type") != "service_account" or not info.get("client_email") or not info.get("private_key"):
        raise ClusterError(400, "The Google key is not a service-account key.", HINT_KEY)
    return info


def email() -> str | None:
    try:
        info = parse(key())
    except ClusterError:
        return None
    return info.get("client_email") if info else None


def _credentials(raw: str):
    digest = hashlib.sha256(raw.encode()).hexdigest()
    creds = _CREDS.get(digest)
    if creds is None:
        try:
            from google.oauth2 import service_account
        except ImportError:
            raise ClusterError(409, "keel's engine has no google-auth library.", "Update keel: GKE sign-in needs it.") from None
        try:
            creds = service_account.Credentials.from_service_account_info(parse(raw), scopes=SCOPES)
        except (ValueError, KeyError):
            raise ClusterError(400, "The Google key is not valid.", HINT_KEY) from None
        _CREDS.clear()  # one key in use at a time; a new key replaces the old one
        _CREDS[digest] = creds
    return creds


def token(raw: str) -> tuple[str, str | None]:
    """A Google access token for the key, made again a few minutes before the old one runs out."""
    with _LOCK:
        creds = _credentials(raw)
        if not creds.valid:
            from google.auth.exceptions import GoogleAuthError
            from google.auth.transport.requests import Request

            try:
                creds.refresh(Request())
            except (GoogleAuthError, OSError) as exc:
                raise ClusterError(409, f"Google gave keel no token for {creds.service_account_email}: {str(exc)[:300]}",
                                   HINT_KEY) from None
        exp = creds.expiry.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if creds.expiry else None
        return creds.token, exp


def shim_dir() -> Path:
    """The folder with keel's gke-gcloud-auth-plugin, written on first use (it is put first on kubectl's PATH)."""
    base = os.environ.get("KEEL_DATA")
    d = (Path(base) / "cluster" if base else Path.home() / ".keel" / "cluster") / "bin"
    p = d / PLUGIN
    body = SHIM.format(python=sys.executable)
    try:
        if p.read_text(encoding="utf-8") == body:
            return d
    except OSError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{PLUGIN}.{os.getpid()}"
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o700)
    os.replace(tmp, p)
    return d


def auth_of(user: dict) -> str:
    """How a kubeconfig user signs in: gke (the GKE helper by its name), gke-path (the GKE helper by a path), gcp (the old gcp
    auth-provider, removed from kubectl), exec (another helper) or kubeconfig (a token or a certificate in the file)."""
    ex = user.get("exec") or {}
    cmd = str(ex.get("command") or "")
    if cmd:
        if os.path.basename(cmd) == PLUGIN:
            return "gke" if cmd == PLUGIN else "gke-path"
        return "exec"
    if (user.get("auth-provider") or {}).get("name") == "gcp":
        return "gcp"
    return "kubeconfig"


def env_for(auth: str, env: dict) -> dict:
    """kubectl's environment for a context: keel's helper and a fresh token for a GKE context while a Google key is set."""
    raw = key()
    if auth != "gke" or not raw:
        return env
    tok, exp = token(raw)
    env = dict(env)
    env["PATH"] = str(shim_dir()) + os.pathsep + env.get("PATH", "")
    env["KEEL_GKE_TOKEN"] = tok
    if exp:
        env["KEEL_GKE_EXPIRY"] = exp
    return env


def hint(auth: str, err: str) -> str | None:
    """A hint for a failed kubectl run on a context that signs in with Google, or None."""
    low = err.lower()
    who = email()
    if auth in ("gke", "gke-path") and not who and PLUGIN in low:
        return "This is a GKE cluster. keel can sign in with a Google service account: choose one in Cluster › Connect › Google Cloud."
    if auth == "gke-path" and who:
        return (f"Your kubeconfig runs {PLUGIN} by its full path, so keel cannot sign in for it. Run `gcloud container clusters "
                f"get-credentials` again (it writes the short name), or change the user's exec command to {PLUGIN}.")
    if auth == "gcp":
        return "This kubeconfig uses the old gcp sign-in, which kubectl no longer has. Run `gcloud container clusters get-credentials` again."
    if auth == "gke" and who and ("unauthorized" in low or "must be logged in" in low or "forbidden" in low):
        return f"keel signed in as {who}, and the cluster said no. {ROLE_HINT}"
    return None
