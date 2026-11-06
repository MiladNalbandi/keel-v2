"""A stand-in kubectl for the Cluster tests: answers from the JSON next to it and writes each call (its argv) to $FAKE_KUBE_LOG."""

import json
import os
import sys
from pathlib import Path

HERE = Path(os.environ.get("FAKE_KUBE_DIR") or Path(__file__).resolve().parent)
args = sys.argv[1:]
with open(os.environ["FAKE_KUBE_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
ctx = args[args.index("--context") + 1] if "--context" in args else ""
rest = [a for i, a in enumerate(args) if a != "--context" and (i == 0 or args[i - 1] != "--context")]
if ctx == "far-away":
    sys.stderr.write("Unable to connect to the server: dial tcp 10.9.9.9:6443: i/o timeout\n")
    sys.exit(1)
if ctx == "gke-prod":
    # what kubectl does for an exec user: run the helper found on PATH and take the token from its ExecCredential
    import shutil
    import subprocess

    helper = shutil.which("gke-gcloud-auth-plugin")
    if not helper:
        sys.stderr.write('error: exec: executable gke-gcloud-auth-plugin not found\n')
        sys.exit(1)
    env = dict(os.environ, KUBERNETES_EXEC_INFO=json.dumps({"apiVersion": "client.authentication.k8s.io/v1beta1",
                                                             "kind": "ExecCredential", "spec": {"interactive": False}}))
    out = subprocess.run([helper], capture_output=True, text=True, env=env)
    cred = json.loads(out.stdout) if out.returncode == 0 else {}
    with open(os.environ["FAKE_KUBE_LOG"] + ".cred", "a") as f:
        f.write(json.dumps(cred) + "\n")
    if cred.get("kind") != "ExecCredential" or (cred.get("status") or {}).get("token") != os.environ.get("FAKE_GKE_ACCEPTS"):
        sys.stderr.write("error: You must be logged in to the server (Unauthorized)\n")
        sys.exit(1)
if rest[:2] == ["version", "--client"]:
    print(json.dumps({"clientVersion": {"gitVersion": "v1.32.3"}}))
elif rest[:2] == ["config", "view"]:
    print((HERE / "config.json").read_text())
elif rest[:1] == ["version"]:
    print(json.dumps({"serverVersion": {"gitVersion": "v1.31.2"}}))
elif rest[:2] == ["get", "nodes"] and ctx == "staging-eu":
    sys.stderr.write('Error from server (Forbidden): nodes is forbidden: User "me" cannot list resource "nodes" at the cluster scope\n')
    sys.exit(1)
elif rest[:2] == ["get", "nodes"]:
    print((HERE / "nodes.json").read_text())
elif rest[:2] == ["get", "pods,deployments,services,events,ingresses"]:
    print((HERE / "all.json").read_text())
elif rest[:2] == ["get", "deployments"]:
    items = [o for o in json.loads((HERE / "all.json").read_text())["items"] if o["kind"] == "Deployment"]
    print(json.dumps({"items": items}))
elif rest[:2] == ["get", "pod"]:
    pod = next(o for o in json.loads((HERE / "all.json").read_text())["items"] if o["kind"] == "Pod" and o["metadata"]["name"] == rest[2])
    print(json.dumps(pod))
elif rest[:2] == ["top", "nodes"]:
    print("node-a   410m   41%   2048Mi   63%")
elif rest[:1] == ["logs"]:
    if "--previous" in rest:
        print("2026-11-05T12:41:03Z ERROR KeyError: 'customer_email'")
    else:
        print("2026-11-05T12:41:02Z worker starting\n2026-11-05T12:41:03Z picked job 88211")
elif rest[:1] == ["describe"]:
    print(f"Name:  {rest[2]}\nEvents:  <none>")
elif rest[:1] == ["exec"]:
    print("hello from the pod")
elif rest[:1] in (["delete"], ["rollout"], ["scale"]):
    print("ok")
else:
    sys.stderr.write(f"fake kubectl does not know {rest}\n")
    sys.exit(1)
