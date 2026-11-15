# keel desktop

keel without Docker, as an app for Linux (AppImage, x64) and macOS (Apple chips). Download it from a release; open it;
keel runs. The only thing the computer needs is **git**.

```
 ┌──────────────────────────── keel app (Electron) ─────────────────────────────┐
 │ main process (desktop/src)                                                   │
 │   ├─ window ──► http://localhost:<port>   keel's own web page, signed in     │
 │   ├─ first start, errors, "keel needs git": the app's own pages (ui/)        │
 │   └─ starts ONE child, in its own process group:                             │
 │        bash keel-native-start   (keel-start's loop, as keel2 start --native) │
 │          ├─ Python  keel_engine   127.0.0.1:<engine port>                    │
 │          ├─ Java    api.jar       127.0.0.1:<back port>   (the Kotlin api)   │
 │          └─ Python  keel_api      127.0.0.1:<port>        the front door     │
 │ inside: Python 3.12 + keel's packages · a small Java 21 · ripgrep · keel     │
 └──────────────────────────────────────────────────────────────────────────────┘
   keel's data (keel.db, keys, logs) → one folder per computer user
   the agents' commands (git, npm, pytest …) → the programs on YOUR computer, YOUR PATH
   the agents run as you (as in keel2's native mode)
```

## Using it

- **First start:** keel says that its agents run as you (your files, SSH keys, network). Choose a project folder, or
  try the demo project first. Add your AI provider's API key in keel under **Connections**: keel calls the provider's
  own API with it.
- **More folders:** File › Add Project Folder. keel restarts with it; every git repository in it becomes a project.
- **Open in Browser** (File menu): keel in your own browser, signed in.
- **Quit:** keel stops with the app. When agents are working, the app asks first (their step starts again later).
  On macOS, closing the window keeps keel running; the Dock icon opens it again.
- **Help menu:** restart keel, restart in safe mode (only the plugins that came with keel), the logs, the sign-in
  key for your own terminal apps (keel TUI), report a problem.
- **A new version:** keel's update notice says when one is out; download the new app and open it. Your data stays.

Where keel keeps its data (never inside the app, never shared with `keel2`'s keels):

| | data, desktop.json | logs |
|---|---|---|
| macOS | `~/Library/Application Support/keel` | `~/Library/Logs/keel` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/keel` | `…/keel/logs` |

The app starts keel on port 8180 (the next free one when it is taken) and keeps that port. Only this computer can
reach it (127.0.0.1).

## Limits of this first version

- **macOS: not signed by Apple** (no Developer ID yet; it is signed ad hoc). The first open needs System Settings ›
  Privacy & Security › Open Anyway, or `xattr -dr com.apple.quarantine /Applications/keel.app`. Only Apple chips.
- **No self-update yet:** a new version is a new download.
- **No Windows yet:** keel's engine and its start scripts need a POSIX system.
- **git is not inside the app.** On macOS the app offers Apple's Command Line Tools; on Linux it shows the install
  command.
- No tray icon, no start at login, no `keel2` commands against the app's keel yet.

## Build it

```bash
scripts/build-native.sh dist                                    # keel's native release (JDK 21, Node 20)
scripts/build-desktop-runtime.sh dist/keel-v2-*-native.tar.gz   # Python, Java, ripgrep and keel → desktop/build/stage
scripts/test-desktop-runtime.sh desktop/build/stage             # keel from that bundle: healthy, a flow to its gate, a clean stop
cd desktop && npm ci && npm test
npx electron-builder --linux AppImage --x64 --publish never     # or on a Mac: --mac dmg zip --arm64
KEEL_DESKTOP_SMOKE=1 KEEL_DESKTOP_HOME=/tmp/kd dist/linux-unpacked/keel --ozone-platform=headless   # SMOKE OK …
```

- `desktop/runtimes.lock` pins Python (python-build-standalone) and ripgrep, with their sha256. The small Java is made
  with `jlink` from the build's own JDK 21.
- `npm start` runs the app from this folder with `desktop/build/stage` (or `KEEL_DESKTOP_RESOURCES`).
- `KEEL_DESKTOP_HOME` puts all of keel desktop's data into one folder (tests).
- CI: `.github/workflows/desktop.yml` builds and tests both apps on pull requests that change them, and on every
  release tag (after the native tarball), and attaches them to the GitHub release.
