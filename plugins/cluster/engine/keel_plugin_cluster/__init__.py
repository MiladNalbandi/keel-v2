"""The Cluster plugin's engine part (issue #82): Kubernetes through kubectl with the person's own kubeconfig. Its routes serve the
Cluster page (routes.py, through the api's ClusterController), its tools serve agents and KeelBot (tools.py, the keel-cluster server
in server.py). Reading is always allowed on a context that is on; a command or a change follows the context's access, and an
agent's one waits for the person's OK in keel's Inbox.

keel loads it as an add-on (KEEL_PLUGIN_ADDONS=keel_plugin_cluster). This module stays light.
"""

from __future__ import annotations

from pathlib import Path

# the same version as ../../keel-plugin.yml (tests/test_cluster.py checks it)
VERSION = "0.2.0"
CONTENT = Path(__file__).resolve().parents[2] / "content"

KEELBOT = ("Kubernetes clusters are linked to this project: cluster_find finds pods, deployments, services and nodes by name or IP "
           "(an empty query lists what needs attention), cluster_logs reads a pod's log (previous=true: before its last restart), "
           "cluster_describe describes it with its events. cluster_exec and cluster_change (delete a pod, restart or scale a "
           "deployment) follow the cluster's access in Cluster › Connect: refused when it is read only, else they wait for the "
           "person's OK in keel's Inbox; say so when you use one.")


def _call(call: dict, tool: str, args: dict) -> str:
    from .tools import call_tool

    return call_tool(call, tool, args)


def _router():
    from .routes import router

    return router


ADDON = {
    "name": "cluster",
    "title": "Cluster",
    "version": VERSION,
    "content": CONTENT,
}

PART = {
    "name": "cluster",
    "title": "Cluster",
    "per_project": True,
    "read_tools": ("cluster_find", "cluster_logs", "cluster_describe"),
    "act_tools": ("cluster_exec", "cluster_change"),
    "mcp": {"server": "keel-cluster", "module": "keel_plugin_cluster.server", "call": _call,
            "prompt": "Kubernetes: cluster_find, cluster_logs and cluster_describe read it. cluster_exec and cluster_change wait for "
                      "the person's OK."},
    "router": _router,
    "keelbot": {"prompt": KEELBOT, "actions": []},
}
