# Use keel from your phone

keel's Android app shows your flows, gates and Inbox; keel stays on your computer, and each phone pairs once.

## What the app does

The app answers keel's questions, follows flows, shows pull request threads and chats with KeelBot. It talks to
keel on your computer directly. Nothing goes through another server.

The phone needs a way to reach your computer. There are three ways:

```text
 phone (keel app)
    │
    ├─ same Wi-Fi ───────► http://192.168.1.20:8080      keel2 start --wifi
    ├─ Tailscale ────────► https on your tailnet          keel2 share tailscale
    └─ public tunnel ────► a public https address         keel2 share tunnel
                                   │
                                   ▼
                          my-computer (keel)
```

## Step 1: make keel reachable

Pick one of these:

```bash
keel2 start --wifi        # reachable on your Wi-Fi (plain http inside your network)
keel2 share tailscale     # https on your tailnet (tailscale serve); keel itself stays on 127.0.0.1
keel2 share tunnel        # a public https address (a Cloudflare quick tunnel); keel2 asks first
```

A **tailnet** is your own private network in Tailscale. Your phone and your computer must both be in it.

To see or stop sharing:

```bash
keel2 share               # what is shared now
keel2 share off           # stop sharing (or: keel2 share off tailscale | tunnel)
```

## Step 2: pair the phone

1. In the dashboard, open **Settings › Access › Devices** and press **Pair a phone**.
   (Before keel 0.18.66 this was **Settings › Devices**. Now it is part of the **Access** tab.)
2. Open the keel app on your phone and scan the QR code.
3. The code works once, for 10 minutes. If it runs out, press **Pair a phone** again.

The list shows every paired phone and when keel last saw it. **Remove** signs a phone out at once.

## Wi-Fi details

- `keel2 restart` remembers `--wifi`. A `keel2 start` without `--wifi` makes keel reachable from this computer
  only again.
- `KEEL_BIND=0.0.0.0` (or one address) does the same as `--wifi`.
- The address is `http://<this computer's address>:<port>`, for example `http://192.168.1.20:8080`.
- When keel2 shows the wrong Wi-Fi address, tell it the right one with `KEEL_LAN_IP`:

```bash
KEEL_LAN_IP=192.168.1.20 keel2 start --wifi
```

## Is it safe?

- **Wi-Fi is plain http.** "Plain" means not encrypted: other people on the same network could read the
  traffic. Use it only on a network you trust.
- **Tailscale** gives https and works anywhere (at home, at work, on mobile data).
- **A tunnel's address is public**, and it changes each time the tunnel starts. Anyone with the address reaches
  only the sign-in and the pairing. Everything else needs your sign-in or a paired phone. Wrong tries are slowed
  down (keel answers 429, "too many requests").
- The tunnel uses `cloudflared` when it is installed, else Docker's `cloudflare/cloudflared` image.

## Something does not work

- The phone cannot open the address: check that the phone is on the same Wi-Fi, or use Tailscale.
- The address is wrong: set `KEEL_LAN_IP` (see above).
- More help: [Troubleshooting](#/help/troubleshooting).
