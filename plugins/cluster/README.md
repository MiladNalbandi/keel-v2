# Cluster

See your Kubernetes clusters in keel, like k9s: find a pod by name or IP, read its logs, run a command in it, and see which commit
runs where. Everything goes through **kubectl with your own kubeconfig**: keel copies no token.

```
 Cluster page ── api (/api/plugins/cluster) ── engine ── kubectl --context <name> ── your cluster
                     │ keeps each context's access
 agent: cluster_find, cluster_logs, cluster_describe ───────────────────────────────▶ reads, no OK needed
 agent: cluster_exec, cluster_change
        │
        ▼
 access of that context:  read → refused     ask → keel's Inbox: the exact command → yes → it runs
                          free (local only) → it runs
```

## Set it up

1. **kubectl and a kubeconfig.** keel's Docker image has kubectl. `keel2` mounts your `~/.kube` read-only into keel
   (`KEEL2_KUBE=0 keel2 start` does not). In native mode keel uses your own kubectl and `KUBECONFIG`.
2. Settings › Plugins: turn **Cluster** on (for agents, per project).
3. Cluster › **Connect**: every context of your kubeconfig is listed. Test it, turn it on or off, and choose what keel may do:

| access | the person on the page | an agent |
| --- | --- | --- |
| **only look** (the default) | reads only | reads only |
| **change after you approve** | runs it after the page asks "are you sure?" | waits for your yes in the Inbox |
| **change without asking** (only a cluster on this computer) | runs it after the page asks | runs it |

"Change" is a command in a pod, deleting a pod, restarting or scaling a deployment. Reading (pods, logs, describe, events) is
always allowed on a context that is on. Every command and change is written down (Overview › Recent changes), also when it was
refused.

## What runs where

keel finds the commit a deployment runs from the annotation `keel.dev/commit`, the label `org.opencontainers.image.revision`, or an
image tag that is a commit (`:a1c94e2`, `:sha-a1c94e2`). With a project chosen, Overview compares it with the project's base on its
remote (`origin/HEAD`): the same, N commits behind, ahead, or not in this repository. A "(#123)" in the commit's subject becomes the
pull request.

## What goes where

| | where it goes |
| --- | --- |
| Logs, events and describe an **agent** reads | **your model company**. Logs can hold secrets: keel does not filter them |
| What **you** read on the page | nowhere: it stays between keel and your cluster |
| Your kubeconfig and its tokens | nowhere: kubectl reads them where keel runs; keel shows only context names and server addresses |

## What it does not do (yet)

- **No interactive shell.** keel has no terminal yet. The Shell tab runs one command at a time (30 s, 64 KB of output) and copies
  the `kubectl exec -it` command for your own terminal.
- **No port-forward from keel.** keel in Docker cannot open a port on your computer. The page copies the `kubectl port-forward`
  command.
- **A cluster on 127.0.0.1 (kind, minikube, k3d) is not reachable from keel in Docker**: 127.0.0.1 inside keel is keel's own
  container. For kind: `docker network connect kind <keel's container>` and the kubeconfig from `kind get kubeconfig --internal`.
  Native mode has no such problem.
- A kubeconfig that signs in with a helper program (`exec`: aws, gcloud, kubelogin) needs that program where keel runs; the image does
  not have them.
- CPU and memory use need metrics-server in the cluster; without it the page shows each node's size.
- Only pods, deployments, services, nodes, events and ingresses. No StatefulSets list, no Helm, no YAML editing.
- The page asks the cluster again every 5 seconds (logs every 3 s while Follow is on), not with a watch.
