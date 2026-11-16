# keel plugin: @TITLE@

@SUMMARY@

|         |                                  |
| ------- | -------------------------------- |
| id      | `@NAME@`                         |
| version | @VERSION@                        |
| needs   | keel core (plugin SDK 1)@NEEDS@  |
| parts   | @PARTS@                          |
| keel    | built against keel-v2 @KEEL_COMMIT@ |

## Install

In keel: **Settings › Plugins › Marketplace › @TITLE@ › Install**. keel checks the file's signature, shows what the
plugin may do, and asks you before it installs. keel's own image has this plugin inside already.

## How this repo is built

This repo has only the plugin. It is built with **keel's own build**, at the keel-v2 commit in [`keel.ref`](../keel.ref):

```
 this repo                      ci.yml (every push, every pull request)           a tag v<version>: release.yml
 ─────────                      ───────────────────────────────────────           ─────────────────────────────
 keel-plugin.yml  (manifest)    1 keel-v2 at keel.ref into keel/                   the same build, then
 api/ web/ engine/ content/ …   2 this plugin into keel/plugins/@NAME@/                sign (KEEL_PUBLISHER_KEY)
 keel.ref  (a keel-v2 commit)   3 the plugins it needs: their repos (a tag that     GitHub release:
 .github/workflows/               fits), or keel-v2's own copy                       @NAME@-<version>.kplug
                                4 keel's tests and build for each part                @NAME@-<version>.kplug.minisig
                                5 keel-plugin pack + lint: @NAME@-<version>.kplug
```

Steps 2–5 are one script of keel-v2: `scripts/build-plugin-repo.sh`.

## Make a release

1. Change `version` in `keel-plugin.yml` (and `VERSION` in the engine package, when there is one). Merge it to `main`.
2. Push the tag: `git tag v1.2.0 && git push origin v1.2.0`. The tag must be `v` + the manifest's version, or the
   release workflow stops.
3. `release.yml` builds, tests, signs with the keel publisher key (the keel-studio organisation secret
   `KEEL_PUBLISHER_KEY`) and makes the GitHub release. The marketplace lists it within the hour.

## Build it on your computer

In a **throw-away** clone of keel-v2 (the script changes that clone's `plugins/`):

```
git clone https://github.com/MiladNalbandi/keel-v2 /tmp/keel && git -C /tmp/keel checkout "$(sed -e 's/#.*//' keel.ref | tr -d ' ' | sed '/^$/d' | tail -n 1)"
bash /tmp/keel/scripts/build-plugin-repo.sh . dist --throwaway
```

It needs git, python3, uv, Java 21 (an api part) and Node 20 (a web part).

## Build against a newer keel

Put a newer keel-v2 commit in `keel.ref`, in a pull request. CI shows if the plugin still builds and its tests pass.

## License

MIT
