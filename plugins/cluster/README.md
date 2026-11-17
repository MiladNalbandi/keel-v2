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
3. A **GKE** cluster: see [GKE sign-in](#gke-sign-in).
4. Cluster › **Connect**: every context of your kubeconfig is listed. Test it, turn it on or off, and choose what keel may do:

| access | the person on the page | an agent |
| --- | --- | --- |
| **only look** (the default) | reads only | reads only |
| **change after you approve** | runs it after the page asks "are you sure?" | waits for your yes in the Inbox |
| **change without asking** (only a cluster on this computer) | runs it after the page asks | runs it |

"Change" is a command in a pod, deleting a pod, restarting or scaling a deployment. Reading (pods, logs, describe, events) is
always allowed on a context that is on. Every command and change is written down (Overview › Recent changes), also when it was
refused.

## GKE sign-in

A GKE context in a kubeconfig signs in with a helper program, `gke-gcloud-auth-plugin`, which asks gcloud for a token. Cluster ›
Connect › **Google Cloud (GKE)** chooses how:

| choice | how kubectl signs in |
| --- | --- |
| **Your Google account (gcloud login)** | the real `gke-gcloud-auth-plugin` with gcloud's login. **Sign in with Google** shows Google's page; you sign in there and paste the code Google shows back into keel |
| **The service account of Connections › Vertex AI** (the default) | keel's own small helper, with a token keel makes from the key saved for Vertex AI |
| **A key for clusters only** | the same, with a service-account JSON key pasted there (stored encrypted, never shown again) |
| **Off** | the kubeconfig's own sign-in, as it is where keel runs |

```
 gcloud login:     kubectl ─▶ gke-gcloud-auth-plugin ─▶ gcloud (your Google login) ─▶ token ─▶ GKE
 service account:  kubectl ─▶ keel's own small helper ─▶ the token keel made from the key ─▶ GKE
```

**Where the gcloud login lives.** keel's Docker image has gcloud and `gke-gcloud-auth-plugin` (pinned, sha256 checked). keel keeps
its own gcloud login in its data (`/data/gcloud`, keel's user only), so it survives restarts. `keel2` mounts your computer's
`~/.config/gcloud` read-only, and keel copies that login **once**, when keel has no Google login yet: if you are signed in to gcloud
on your computer, you are signed in in keel too (`KEEL2_GCLOUD=0 keel2 start`: not mounted). **Sign out** removes keel's copy only:
it does not sign you out on your computer and does not revoke anything at Google, and keel does not copy your login again. In native
mode and in the desktop app keel uses **your own gcloud** on this computer: Sign in there is `gcloud auth login`, and the page says when
gcloud or the plugin is missing.

**Add a GKE cluster.** Signed in with gcloud, Connect lists your Google Cloud projects and their GKE clusters. **Add** runs
`gcloud container clusters get-credentials` into **keel's own kubeconfig** (`$KEEL_DATA/cluster/kubeconfig`; your `~/.kube` is
read-only in Docker and is never changed). kubectl reads your kubeconfig first, then keel's, so both sets of contexts show; the
added ones say "added in keel". Turn it on and choose what keel may do, as for any context.

The account needs a GKE role in the cluster's Google Cloud project: **Kubernetes Engine Viewer** to read, **Kubernetes Engine
Developer** to run commands and change things. With a service account, the token is only in kubectl's environment for that run,
never on disk, and only for contexts whose user runs `gke-gcloud-auth-plugin` by that name (`gcloud container clusters
get-credentials` writes it so). Agents of a project with the plugin on sign in the same way.

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
| A Google service-account key (GKE) | to Google only, to make an access token; the token goes to your cluster |
| Your gcloud login (GKE) | stays where keel runs (keel's data in Docker); gcloud asks Google for a token, which goes to your cluster |

## What it does not do (yet)

- **No interactive shell.** keel has no terminal yet. The Shell tab runs one command at a time (30 s, 64 KB of output) and copies
  the `kubectl exec -it` command for your own terminal.
- **No port-forward from keel.** keel in Docker cannot open a port on your computer. The page copies the `kubectl port-forward`
  command.
- **A cluster on 127.0.0.1 (kind, minikube, k3d) is not reachable from keel in Docker**: 127.0.0.1 inside keel is keel's own
  container. For kind: `docker network connect kind <keel's container>` and the kubeconfig from `kind get kubeconfig --internal`.
  Native mode has no such problem.
- A kubeconfig that signs in with another helper program (`exec`: aws, kubelogin) needs that program where keel runs; the image does
  not have them. GKE works, see [GKE sign-in](#gke-sign-in).
- CPU and memory use need metrics-server in the cluster; without it the page shows each node's size.
- Only pods, deployments, services, nodes, events and ingresses. No StatefulSets list, no Helm, no YAML editing.
- The page asks the cluster again every 5 seconds (logs every 3 s while Follow is on), not with a watch.
