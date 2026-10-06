# Eval sets for keel's quality runs

Each folder is one eval set: a tiny project and the cases keel runs on it (the Quality page, `docs/CONTRACT.md`
"v0.8.0: quality runs"). For every case keel copies the project into a fresh git repository, runs the case's flow in
run mode `auto` with the chosen model, and scores the run.

```
evals/<name>/eval.yml      name, description, project (a folder here, or "demo" for keel's bundled demo), cases
evals/<name>/project/      the project's files (committed once, on main, before each case starts)
```

A case: `id`, `title`, `workflow` (default `change`), `request` (what the agents read), optional `acs`
(`[{id, layer, title}]`) and `cap_tokens` (default: twice the flow's estimate).
