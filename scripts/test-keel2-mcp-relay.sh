#!/usr/bin/env bash
# keel2 mcp's relay (0.18.51): the MCP server ends when keel restarts; the relay starts it again, repeats the client's
# initialize, and answers the call that was waiting. A fake server (it ends after its 2nd tools/call, like a keel
# restart) and a fake docker (`docker inspect` says the container runs) stand in; no real keel or docker is used.
# 0.18.64 a call while keel is down is answered "keel is restarting" after the hold (not after minutes), a ping at once;
# after a reconnect the client hears that the tools may have changed; keel_reconnect is the relay's own; the server
# gets its start count (KEEL_MCP_RELAY_N).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
awk "/^MCP_RELAY_PY='/{f=1;next} f&&/^'\$/{exit} f" "$here/keel2" > "$tmp/relay.py"
[ -s "$tmp/relay.py" ] || { echo "FAIL: no relay in keel2"; exit 1; }
grep -q 'exec python3 -c "$MCP_RELAY_PY"' "$here/keel2" || { echo "FAIL: keel2 mcp does not use the relay"; exit 1; }
mkdir -p "$tmp/bin"
cat > "$tmp/bin/docker" <<'D'
#!/usr/bin/env bash
[ "$1" = inspect ] && { echo true; exit 0; }
exit 1
D
cat > "$tmp/server.py" <<'S'
import json, sys, os
count_file = sys.argv[1]
starts = int(open(count_file).read() or 0) + 1 if os.path.exists(count_file) else 1
open(count_file, "w").write(str(starts))
calls = 0
for line in sys.stdin:
    m = json.loads(line)
    if m.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"serverInfo": {"name": "fake", "start": starts,
                          "relay_n": os.environ.get("KEEL_MCP_RELAY_N")}}}), flush=True)
    elif m.get("method") == "tools/call":
        calls += 1
        if starts == 1 and calls == 2:
            sys.exit(0)          # keel restarts before this call is answered
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"start": starts, "n": m["params"]["n"]}}), flush=True)
S
chmod +x "$tmp/bin/docker"
cat > "$tmp/client.py" <<'C'
import json, subprocess, os
def relay(tmp, **env):
    e = dict(os.environ, PATH=tmp + "/bin:" + os.environ["PATH"], **env)
    p = subprocess.Popen(["python3", tmp + "/relay.py", "keel-test", "python3", tmp + "/server.py", tmp + "/starts"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=e)
    p.notes = []
    return p
def send(p, m): p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
def recv(p):
    while True:                  # notifications are noted, the next answer is returned
        m = json.loads(p.stdout.readline())
        if "id" in m:
            return m
        p.notes.append(m.get("method"))
C
python3 - "$tmp" <<'P'
import sys, time
tmp = sys.argv[1]; sys.path.insert(0, tmp)
from client import relay, send, recv
p = relay(tmp)
send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
info = recv(p)["result"]["serverInfo"]
assert info["start"] == 1 and info["relay_n"] == "1", info
send(p, {"jsonrpc": "2.0", "method": "notifications/initialized"})
send(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"n": 1}})
r = recv(p); assert r["id"] == 2 and r["result"]["start"] == 1, r
send(p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"n": 2}})   # the server ends here
# 0.18.64 it reached the old server, which may have done it: never sent twice (a flow started twice), it says so
r = recv(p); assert r["id"] == 3 and r["result"]["isError"] and "it may have happened" in r["result"]["content"][0]["text"], r
send(p, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"n": 3}})
r = recv(p); assert r["id"] == 4 and r["result"]["start"] == 2, r
assert p.notes == ["notifications/tools/list_changed"], p.notes                  # the client lists the tools again
assert open(tmp + "/starts").read() == "2"
# keel_reconnect: the relay's own; it starts the server again and says so when the new one answers
send(p, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "keel_reconnect", "arguments": {}}})
r = recv(p); assert r["id"] == 5 and not r["result"]["isError"] and "start 3" in r["result"]["content"][0]["text"], r
send(p, {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"n": 4}})
r = recv(p); assert r["id"] == 6 and r["result"]["start"] == 3, r
p.stdin.close(); p.wait(timeout=10)
print("ok   a call the old server got is not sent again after a keel restart; the next goes to the new server (initialize repeated, tools listed again)")
print("ok   keel_reconnect starts the server again and answers when the new one is ready")
P
# keel stays down: a waiting call gets "keel is restarting" after the hold, not silence; a ping is answered at once
cat > "$tmp/bin/docker" <<'D'
#!/usr/bin/env bash
[ "$1" = inspect ] && { echo false; exit 0; }
exit 1
D
rm -f "$tmp/starts"
python3 - "$tmp" <<'P'
import sys, time
tmp = sys.argv[1]; sys.path.insert(0, tmp)
from client import relay, send, recv
p = relay(tmp, KEEL_MCP_RELAY_WAIT="30", KEEL_MCP_RELAY_HOLD="1")
send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}); recv(p)
send(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"n": 1}}); recv(p)
t0 = time.time()
send(p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"n": 2}})   # the server ends; keel does not come back
r = recv(p)
assert r["id"] == 3 and r["result"]["isError"] and "keel is restarting" in r["result"]["content"][0]["text"], r
assert time.time() - t0 < 10, time.time() - t0                                   # the hold (1 s), not the wait (30 s)
send(p, {"jsonrpc": "2.0", "id": 4, "method": "ping"})
r = recv(p); assert r == {"jsonrpc": "2.0", "id": 4, "result": {}}, r
send(p, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"n": 3}})   # a new call while keel is down
r = recv(p); assert r["id"] == 5 and "keel is restarting" in r["result"]["content"][0]["text"], r
p.stdin.close(); p.wait(timeout=10)
print("ok   keel stays down: a call gets \"keel is restarting\" after the hold, a ping is answered at once")
P
echo "all mcp relay checks passed"
