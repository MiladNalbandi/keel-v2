#!/usr/bin/env python3
"""A stand-in for Slack on this computer, to try the Slack plugin in a running keel without a workspace: the Web API methods keel calls, and a Socket Mode WebSocket.
It holds what Slack's docs say, as far as keel uses it. It is not Slack.

    python3 fake_slack.py [port]        (needs the websockets library: keel's engine has it)

    keel: Settings › Slack, or POST /api/plugins/slack with {"api_url": "http://127.0.0.1:<port>/api", "bot_token": "xoxb-x", "app_token": "xapp-x", ...}
    POST /_send   {"type": "events_api" | "interactive", "payload": {...}}   sends one envelope to keel, answers its id
                  a direct message, a mention, a reply in a thread (thread_ts), a button (block_actions, with a trigger_id), a modal sent back (view_submission)
    GET  /_views                                                             the modals keel opened (views.open), newest last
    GET  /_calls                                                             what keel called, and the envelope ids it acknowledged
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from websockets.sync.server import serve

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18450
calls: list[dict] = []
acks: list[str] = []
sockets: list = []


def socket_mode(ws):
    sockets.append(ws)
    ws.send(json.dumps({"type": "hello"}))
    try:
        for raw in ws:
            acks.append(json.loads(raw).get("envelope_id", ""))
    finally:
        sockets.remove(ws)


class Http(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, value, status=200):
        out = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        if self.path == "/_views":
            return self._json({"views": [c["body"].get("view") for c in calls if c["method"] == "views.open"]})
        self._json({"calls": calls, "acks": acks, "connected": len(sockets)})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/_send":
            eid = uuid.uuid4().hex
            for ws in list(sockets):
                ws.send(json.dumps({"envelope_id": eid, **body}))
            return self._json({"envelope_id": eid, "sent_to": len(sockets)})
        method = self.path.rsplit("/", 1)[-1]
        calls.append({"method": method, "token_kind": self.headers.get("Authorization", "")[7:12], "body": body})
        if method == "auth.test":
            return self._json({"ok": True, "team_id": "T1", "team": "Northwind", "user_id": "UBOT"})
        if method == "apps.connections.open":
            return self._json({"ok": True, "url": f"ws://127.0.0.1:{PORT + 1}/socket"})
        if method == "views.open":
            # as Slack does: a modal needs a trigger, and its title is at most 24 characters
            view = body.get("view") or {}
            if not body.get("trigger_id"):
                return self._json({"ok": False, "error": "invalid_trigger"})
            if len((view.get("title") or {}).get("text") or "") > 24:
                return self._json({"ok": False, "error": "invalid_arguments"})
            return self._json({"ok": True, "view": {"id": f"V{len(calls)}"}})
        self._json({"ok": True, "channel": body.get("channel", "C1"), "ts": f"{len(calls)}.000100"})    # a member id as channel: a direct message


threading.Thread(target=lambda: serve(socket_mode, "127.0.0.1", PORT + 1).serve_forever(), daemon=True).start()
ThreadingHTTPServer(("127.0.0.1", PORT), Http).serve_forever()
