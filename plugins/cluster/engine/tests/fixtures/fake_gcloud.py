"""A stand-in gcloud for the Cluster tests: the sign-in (link, then the code on stdin), auth list, projects, GKE clusters and
get-credentials (writes a GKE context into $KUBECONFIG). It keeps its login in $CLOUDSDK_CONFIG like gcloud does, and writes each
call (its argv) to $FAKE_GCLOUD_LOG. The good code is GOOD_CODE."""

import configparser
import json
import os
import sys
from pathlib import Path

import yaml

GOOD_CODE = "4/0Ab-good-code-123"
ACCOUNT = "person@example.com"
CFG = Path(os.environ.get("CLOUDSDK_CONFIG") or Path.home() / ".config" / "gcloud")
args = [a for a in sys.argv[1:] if not a.startswith("--format")]
with open(os.environ["FAKE_GCLOUD_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")


def config():
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(CFG / "configurations" / "config_default")
    return ini


def save(ini):
    (CFG / "configurations").mkdir(parents=True, exist_ok=True)
    with open(CFG / "configurations" / "config_default", "w") as f:
        ini.write(f)
    (CFG / "active_config").write_text("default")


def account():
    acct = config().get("core", "account", fallback="")
    return acct if acct and (CFG / "credentials.db").is_file() else ""


def need_login(cmd):
    if not account():
        sys.stderr.write(f"ERROR: (gcloud.{cmd}) You do not currently have an active account selected.\nPlease run:\n\n"
                         "  $ gcloud auth login\n\nto obtain new credentials.\n")
        sys.exit(1)


def flag(name):
    return next((a.split("=", 1)[1] for a in args if a.startswith(f"--{name}=")), "")


if args[:1] == ["version"]:
    print(json.dumps({"Google Cloud SDK": "594.0.0", "core": "2026.11.07"}))
elif args[:2] == ["auth", "list"]:
    acct = account()
    print(json.dumps([{"account": acct, "status": "ACTIVE"}] if acct else []))
elif args[:2] == ["auth", "login"]:
    print("Go to the following link in your browser, and complete the sign-in prompts:\n")
    print("    https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=325&state=xyz&code_challenge=abc\n")
    sys.stdout.write("Once finished, enter the verification code provided in your browser: ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code != GOOD_CODE:
        sys.stderr.write("ERROR: (gcloud.auth.login) There was a problem with web authentication. Invalid code.\n")
        sys.exit(1)
    CFG.mkdir(parents=True, exist_ok=True)
    (CFG / "credentials.db").write_text("secret-refresh-token")
    ini = config()
    if not ini.has_section("core"):
        ini.add_section("core")
    ini.set("core", "account", ACCOUNT)
    save(ini)
    print(f"\nYou are now logged in as [{ACCOUNT}].")
elif args[:3] == ["config", "unset", "account"]:
    ini = config()
    if ini.has_section("core"):
        ini.remove_option("core", "account")
    save(ini)
    sys.stderr.write("Unset property [core/account].\n")
elif args[:2] == ["projects", "list"]:
    need_login("projects.list")
    print(json.dumps([{"projectId": "shop-prod-123", "name": "Shop (prod)"}, {"projectId": "demo-project", "name": "Demo"}]))
elif args[:3] == ["container", "clusters", "list"]:
    need_login("container.clusters.list")
    if flag("project") != "shop-prod-123":
        sys.stderr.write("ERROR: (gcloud.container.clusters.list) ResponseError: code=403, message=Required \"container.clusters.list\" "
                         "permission(s) for \"projects/" + flag("project") + "\".\n")
        sys.exit(1)
    print(json.dumps([{"name": "shop", "location": "europe-west1", "status": "RUNNING", "currentMasterVersion": "1.31.2-gke.1000",
                       "currentNodeCount": 3},
                      {"name": "batch", "zone": "europe-west1-b", "status": "RUNNING", "currentMasterVersion": "1.31.2-gke.1000"}]))
elif args[:3] == ["container", "clusters", "get-credentials"]:
    need_login("container.clusters.get-credentials")
    name, loc, project = args[3], flag("location"), flag("project")
    target = Path(os.environ["KUBECONFIG"].split(os.pathsep)[0])
    cfg = yaml.safe_load(target.read_text()) if target.is_file() else None
    cfg = cfg or {"apiVersion": "v1", "kind": "Config", "clusters": [], "contexts": [], "users": []}
    ctx = f"gke_{project}_{loc}_{name}"
    cfg["clusters"] = [c for c in cfg["clusters"] if c["name"] != ctx] + [{"name": ctx, "cluster": {"server": "https://198.51.100.7"}}]
    cfg["contexts"] = [c for c in cfg["contexts"] if c["name"] != ctx] + [{"name": ctx, "context": {"cluster": ctx, "user": ctx}}]
    cfg["users"] = [u for u in cfg["users"] if u["name"] != ctx] + [
        {"name": ctx, "user": {"exec": {"apiVersion": "client.authentication.k8s.io/v1beta1", "command": "gke-gcloud-auth-plugin",
                                         "provideClusterInfo": True}}}]
    cfg["current-context"] = ctx
    target.write_text(yaml.safe_dump(cfg))
    sys.stderr.write(f"Fetching cluster endpoint and auth data.\nkubeconfig entry generated for {name}.\n")
else:
    sys.stderr.write(f"ERROR: (gcloud) fake gcloud does not know {args}\n")
    sys.exit(2)
