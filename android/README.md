# keel for Android

<p align="center"><img src="../docs/keel-android.gif" alt="keel's Android app with demo data" width="300"></p>

The phone companion of keel v2: answer the questions flows wait at (gates), follow flows, review pull request threads
with keel's "Is the reviewer right?" check, and chat with KeelBot (Ask / Fix / Edit). Kotlin, Jetpack Compose,
Material 3, one activity; minSdk 28, target 36.

## Pairing

1. Start keel so the phone can reach it: `keel2 start --wifi` (same Wi-Fi), or use a Tailscale
   (`https://<machine>.<tailnet>.ts.net`) or tunnel (`https://….trycloudflare.com`) address.
2. In keel, open Settings › Devices › Pair a phone: it shows a QR code
   `keel://pair?u=<base url>&c=<one-time code>&n=<keel name>`.
3. In the app, scan it (or type the address and the code). The app calls `POST /api/devices/pair`
   `{code, name, platform: "android"}` and keeps the device token it gets, encrypted with a key in the Android Keystore.
   Every call then sends `Authorization: Bearer <token>`. When keel answers 403 "This device was removed", the app
   forgets that keel and shows the Pair screen. Several keels can be saved; one is active (Settings).

Plain `http://` is allowed (`res/xml/network_security_config.xml`): keel on the local Wi-Fi has a LAN address no
certificate covers. Use plain http only on a network you trust.

## What each screen calls

| Screen | keel api |
|---|---|
| Pair | `POST /api/devices/pair`, `GET /api/devices/me` |
| Frame (bottom bar, project switch) | `GET /api/projects`, `GET /api/inbox/count`, `GET /api/projects/{pid}/review/prs?filter=review` |
| Inbox | `GET /api/inbox`, `GET /api/projects/{pid}/budget/now`, `POST /api/approvals/{id}/decide` (KeelBot's commands) |
| Question | `GET /api/projects/{pid}/flows/{tid}`, `POST /api/inbox/{tid}/act` `{decision, id, why, payload}` (409 = the flow asks something else now), `POST /api/threads/{tid}/stop` |
| Flows | `GET /api/projects/{pid}/runs`, `GET /api/projects/{pid}/workflows`, `POST /api/projects/{pid}/flows` |
| Flow | `GET /api/projects/{pid}/flows/{tid}`, `GET /api/threads/{tid}/history`, `POST /api/inbox/{tid}/act`, `POST /api/threads/{tid}/stop`, `POST /api/threads/{tid}/rewind` (resume) |
| PRs | `GET /api/projects/{pid}/review/prs?filter=review\|mine\|all` |
| Thread | `GET …/review/view?key=pr:N`, `GET …/review/diff`, `POST …/review/threads/{id}/reply`, `…/resolve`, `…/judge` (then `GET …/review/ai` while it runs), `…/address` |
| File diff | `GET …/review/diff?key=pr:N&path=` (unified, old/new numbers, threads inline), `POST …/review/drafts` (a line comment, pending), `DELETE …/review/drafts/{id}`, `POST …/review/submit` (Send review) |
| KeelBot | `GET/POST /api/projects/{pid}/helper/sessions`, `GET/PATCH …/sessions/{sid}`, `POST …/turn`, `…/stop`, `GET …/changes`, `POST …/undo`, `POST …/done` (keep), `GET /api/jobs/{call}/steps`, `PATCH …/sessions/{sid}` `{mode}` / `{model}`, `GET /api/providers/models` (model + effort sheet) |
| Live | `GET /api/events?project=<pid>&notify=all` (server-sent events) while the app is open; a 20 s poll when it is down |

Notifications are local: while the app is open, and every 15 minutes in the background (WorkManager), the app reads the
inbox and says "A question waits for you" for questions it has not told yet.

## Build and test

```sh
cd android
echo "sdk.dir=$HOME/Android/Sdk" > local.properties   # or set ANDROID_HOME
./gradlew :app:assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assemblePhone        # app/build/outputs/apk/phone/app-phone.apk: shrunk, 64-bit ARM, ~11 MB (to put on a phone)
./gradlew :app:testDebugUnitTest    # JVM tests: pairing links, api client (MockWebServer), gate answers, view models
```

The debug build has a demo mode (fake data in `DemoBackend.kt`, no keel needed): "Try the demo" on the Pair screen, or
`adb shell am start -n dev.keel.android/.MainActivity --ez demo true --es open flows` (`open`: inbox, flows, prs, chat,
`question:<pid>:<tid>`, `flow:<pid>:<tid>`, `pr:<pid>:<n>`).

## Code

```
app/src/main/java/dev/keel/android/
  data/      KeelApi (OkHttp + kotlinx.serialization), Models, KeelSession (active keel), KeelStore (Keystore-encrypted
             tokens), LiveHub (SSE), GateForm + Logic (answers as the web sends them), PairLink, DemoBackend
  ui/        AppRoot (navigation, bottom bar), one package per screen (screen + ViewModel), common/, theme/ (keel's colours, and its motion in Motion.kt)
  notify/    Notifier, InboxCheckWorker
```
