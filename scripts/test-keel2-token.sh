#!/usr/bin/env bash
# Test `keel2 token` (0.18.12): it reads a pipe (pbpaste | keel2 token anthropic) with no prompt, removes what a copy adds
# (spaces, line breaks), refuses a value that cannot be a token and says why, says what to do when nothing arrived, and
# shows keel's reason when keel refuses a value. A fake `docker` (its python3 is this computer's) and a fake `curl` on
# PATH record every call; nothing runs. Only fake tokens are used.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd -P)"
keel2="$here/keel2"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
export XDG_CONFIG_HOME="$scratch/config" KEEL2_HOME="$scratch/home" KEEL_OPEN=0 NO_COLOR=1 KEEL_NAME=keel-token-test HOME="$scratch/user"
mkdir -p "$scratch/bin" "$XDG_CONFIG_HOME" "$KEEL2_HOME" "$HOME"

cat > "$scratch/bin/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_CALLS"
case "$1" in
  info) exit 0 ;;
  inspect) case "$*" in *State.Running*) echo true ;; esac; exit 0 ;;
  port) echo "127.0.0.1:8188"; exit 0 ;;
  exec)
    case "$*" in
      *internal-token*) printf 'fake-internal-token' ;;
      *" python3 "*) shift; while [ "$1" != python3 ]; do shift; done; shift; exec python3 "$@" ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
FAKE
# curl: saves the request body it reads from stdin; answers FAKE_ANSWER with the status FAKE_CODE (-w '\n%{http_code}')
cat > "$scratch/bin/curl" <<'FAKE'
#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_CALLS"
case "$*" in
  *--data-binary*) cat > "$FAKE_BODY" ;;
esac
ok='{"hint":"…XYZ"}'
printf '%s' "${FAKE_ANSWER:-$ok}"
case "$*" in *http_code*) printf '\n%s' "${FAKE_CODE:-200}" ;; esac
FAKE
chmod +x "$scratch/bin/docker" "$scratch/bin/curl"
export PATH="$scratch/bin:$PATH" FAKE_CALLS="$scratch/calls" FAKE_BODY="$scratch/body"

fails=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; fails=$((fails + 1)); }
has() { if printf '%s' "$2" | grep -qF -- "$3"; then pass "$1"; else fail "$1 (no '$3' in: $2)"; fi; }
hasnt() { if printf '%s' "$2" | grep -qF -- "$3"; then fail "$1 ('$3' in: $2)"; else pass "$1"; fi; }
saved() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["value"])' "$FAKE_BODY"; }

echo "keel2 token"
rm -f "$FAKE_BODY"
out="$(printf 'sk-ant-api03-test_ABC\n' | "$keel2" token anthropic 2>&1)" && code=0 || code=$?
has "a pipe saves the token" "$out" "saved ANTHROPIC_API_KEY in keel's database (encrypted) · …XYZ"
hasnt "a pipe gets no prompt" "$out" "Paste"
[ "$code" = 0 ] && pass "it exits 0" || fail "exit $code"
[ "$(saved)" = "sk-ant-api03-test_ABC" ] && pass "the value keel gets is the token" || fail "keel got: $(saved)"

rm -f "$FAKE_BODY"
out="$(printf 'sk-ant-api03-test\n  _ABC' | "$keel2" token anthropic 2>&1)" || true
[ "$(saved)" = "sk-ant-api03-test_ABC" ] && pass "a token wrapped over two lines (no last newline) comes in one piece" || fail "keel got: $(saved) / $out"

rm -f "$FAKE_BODY"
out="$(printf 'sk-ant-api03-test#_ABC' | "$keel2" token anthropic 2>&1)" && code=0 || code=$?
[ "$code" = 1 ] && pass "a token with a # is refused" || fail "exit $code"
has "it names the bad character" "$out" "Not saved: it has characters a token never has (#)."
has "it says how to copy it in one piece" "$out" "pbpaste | keel2 token anthropic"
hasnt "the token is never printed" "$out" "test#_ABC"
[ -f "$FAKE_BODY" ] && fail "the bad token was sent to keel" || pass "nothing is sent to keel"

out="$(printf 'api03-test_ABC' | "$keel2" token anthropic 2>&1)" || true
has "an Anthropic key must start with sk-ant-" "$out" "an Anthropic API key starts with sk-ant- (this one has 14 characters)."

out="$(: | "$keel2" token anthropic 2>&1)" && code=0 || code=$?
[ "$code" = 1 ] && pass "an empty pipe fails" || fail "exit $code"
has "it says nothing arrived" "$out" "Nothing arrived"
has "it suggests the pipe (macOS)" "$out" "Copy the token and run: pbpaste | keel2 token anthropic"
has "it suggests the pipe (Linux)" "$out" "wl-paste | keel2 token anthropic"

out="$("$keel2" token anthropic sk-ant-api03-te•st </dev/null 2>&1)" || true
has "a value on the command line is checked too" "$out" "characters a token never has (•)"

out="$(printf 'x' | "$keel2" token claude 2>&1)" && code=0 || code=$?
[ "$code" = 1 ] && pass "a Claude login is refused (#101: API keys only)" || fail "exit $code"
has "it names the key to save instead" "$out" "keel2 token anthropic"
out="$(printf 'x' | "$keel2" token codex 2>&1)" || true
has "a Codex login is refused too" "$out" "keel2 token openai"

out="$(printf 'gho_te st#x' | "$keel2" token copilot 2>&1)" || true
has "other tokens are checked lightly" "$out" "characters a token never has (#)"

out="$(printf 'sk-ant-api03-test_ABC' | FAKE_CODE=400 FAKE_ANSWER='{"error":"The Anthropic API key was refused.","hint":"Make a new one."}' "$keel2" token anthropic 2>&1)" && code=0 || code=$?
[ "$code" = 1 ] && pass "keel's refusal fails the command" || fail "exit $code"
has "keel's reason is shown" "$out" "Not saved: The Anthropic API key was refused."
has "keel's hint is shown" "$out" "Make a new one."
out="$(printf 'sk-ant-api03-test_ABC' | FAKE_CODE=500 FAKE_ANSWER='oops' "$keel2" token anthropic 2>&1)" || true
has "an answer without a reason still says why" "$out" "keel answered 500."

[ "$fails" = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
