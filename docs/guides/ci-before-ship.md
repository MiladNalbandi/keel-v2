# Run your CI jobs before ship

Before ship, keel runs your project's own CI jobs on CI's versions, so a problem shows up before the pull request.

## Why

**CI** (continuous integration) is the service that runs your checks for each push, for example GitHub Actions
or GitLab CI.

keel's own style check and tests run on one runtime: the one keel has. CI may run them on other versions (for
example PHP 7.4, 8.1 and 8.2). A newer or older style tool there can flag code that the local run accepted. So
since keel 0.18.65 the ship flow runs the project's own CI jobs itself, in the step `ci`.

## Where it is in the ship flow

```text
 ◆ which steps ─ verify ─ lint ─ CI jobs ─ release ─ cover ─ deps ─ audit ─ trace ─ lens reviewers ─ spec walk
   ─◆ final review ─ knowledge update ─ PR body ─◆ open the PR (never pushes)
```

- At **Which steps run** you can skip the CI jobs (like the other optional steps).
- A failed CI job is an exception at the **final review**. The reviewers get the report.
- The final review, the PR body and the ship reviewers show the same report.

## What keel runs

keel reads `.github/workflows/*.yml` and `.gitlab-ci.yml`. It makes one run for each job and each version in the
job's **matrix** (the list of versions a job runs on).

```text
 CI config ──► jobs × matrix
    ├─ deploy / release / publish, needs secrets, Windows / macOS,
    │  service containers, manual                         ──► skipped (keel says why)
    └─ each other run ──► with Docker: a throw-away container on CI's image
                      ──► without Docker: here, when this machine has CI's version;
                                          else "could not run"
```

The report has one line per run: the job, the versions, the result (passed, failed, could not run, skipped), the
time, and the end of the failing command's output.

## Docker

keel runs the containers itself. Agents never get Docker. keel has Docker when you start it with `--docker`, and
in native mode:

```bash
keel2 start --docker ~/Projects/my-app
```

Each run is kept apart from your computer:

- it gets a temporary copy of the flow's worktree (a **worktree** is a copy of the project on its own branch),
- no mount of your folders, no Docker socket, no `--privileged`, no host network,
- only the images the CI config names, and keel's own official list,
- no secrets: a job that needs one is skipped, and `GITHUB_TOKEN` is left out.

When the flow's branch changed the CI config, its jobs run only in containers, never on keel's own machine.

## Settings

Put these in the project's `.keel/config.yml`:

```yaml
ci:
  run: auto        # auto (when a CI config exists) | on | off
  timeout: 15      # minutes per run
  budget: 45       # minutes for all runs together
  max_runs: 24     # more runs than this (a large matrix) are skipped
  jobs: [cs]       # only these jobs (optional)
  extensions: report   # report | install (PHP extensions setup-php would install)
```

## PHP projects

An official PHP image has only PHP. keel installs composer (and git, unzip) when a step uses it, and the tools that
`setup-php` lists. A missing extension is a note ("extension intl not available"). With `extensions: install`
keel tries to install it.

When CI has a style job, its result on CI's version is what counts. When your style check ran on another version,
the report says so.

## Run it now, from an AI tool

You do not have to wait for ship. From an AI tool connected to keel (see
[Connect an AI tool (MCP)](#/help/connect-an-ai-tool)):

- `keel_ci_run` (write level) runs the jobs now and returns the table. It runs in the project folder, or in a
  flow's worktree when you pass `thread_id`. `jobs` picks some jobs.
- `keel_ci_report` (read level) shows the last report, and whether it is about the newest commit (HEAD).

## CI on GitHub after a push

This is a different thing: the **CI/CD** plugin watches your pipelines on GitHub Actions and tells you when one
fails. **Run › Jobs › Pipelines** shows the runs. Open a failed one and press **Fix it**, **Run the failed jobs
again**, or **Ask KeelBot why**.
