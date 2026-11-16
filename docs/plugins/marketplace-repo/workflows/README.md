# Workflows

A **workflow** is one YAML file that keel installs into a project (or into all projects) from
**Settings › Plugins › Marketplace › Workflows**. It needs no restart: it is data, not code. It runs only through keel's
flow engine, its agents and its gates. Before anyone installs it, keel shows its Install review: the steps, the gates,
the agents, the MCP servers, the shell commands it runs (in red) and whether it changes files.

## The files

| file                                      | what                                                                       |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| `workflows/<name>.yml`                    | the entry: name, title, publisher, repo, category, summary, tags, keel     |
| `workflows/<name>/<version>.yaml`         | (optional) the workflow file itself, kept here instead of in a release     |
| `workflows/<name>/<version>.yaml.minisig` | its signature, by a key in `publishers/<publisher>.yml`                    |

- `category`: `code`, `review`, `docs`, `release`, `quality`, `ops` or `other`.
- `keel` (optional): the keel versions it works with, for example `">=0.16.1"`.
- `repo` (optional): a GitHub repo whose releases hold `<name>-<version>.yaml` and `<name>-<version>.yaml.minisig`
  (tag `<name>-v<version>`). Leave it out when the files are kept here.

The sync reads both places the same way: the file must pass `keel-plugin workflow lint` and carry a good signature by
its publisher. It computes the Install review into the index. keel computes it again when it installs the workflow,
and refuses a workflow whose agents, MCP servers, commands or edits are not what the index says.

## Publish a workflow

In keel: a workflow's menu › **Publish to marketplace** downloads a folder with the YAML, its entry and the exact
commands (`PUBLISH.md`). Or by hand:

```
keel-plugin workflow lint release-notes-1.2.0.yaml
keel-plugin sign release-notes-1.2.0.yaml --key-file you.key
gh release create release-notes-v1.2.0 release-notes-1.2.0.yaml release-notes-1.2.0.yaml.minisig --repo you/keel-workflows
```

Then open a pull request here with `workflows/<name>.yml` (and `publishers/<you>.yml` the first time). To stop a bad
version: add it to `revoked.yml` with `kind: workflow`.
