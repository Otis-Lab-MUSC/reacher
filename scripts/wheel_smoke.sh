#!/usr/bin/env bash
# Install a built wheel into a clean venv and prove it runs: version matches
# the wheel metadata, firmware hex is packaged, and the server answers /health.
set -euo pipefail

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "usage: $0 <path-to-wheel>" >&2
    exit 2
fi
WHEEL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"

WORK="$(mktemp -d)"
SERVER_PID=""
LOG="$WORK/server.log"

cleanup() {
    local rc=$?
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ "$rc" -ne 0 ] && [ -f "$LOG" ]; then
        echo "--- last 20 server log lines (API key lines removed) ---" >&2
        grep -v 'API key' "$LOG" | tail -n 20 >&2 || true
    fi
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT

fail() {
    echo "wheel_smoke: FAIL: $*" >&2
    exit 1
}

VENV="$WORK/venv"
if command -v uv >/dev/null 2>&1; then
    uv venv --quiet "$VENV"
    uv pip install --quiet --python "$VENV/bin/python" "$WHEEL"
else
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --quiet "$WHEEL"
fi
PY="$VENV/bin/python"
REACHER="$VENV/bin/reacher"
[ -x "$REACHER" ] || fail "console script 'reacher' not installed"

echo "==> version"
REPORTED="$("$REACHER" --version)" || fail "reacher --version exited non-zero"
# -I keeps the cwd off sys.path so the source tree can't shadow the installed package
"$PY" -I - "$REPORTED" <<'PYEOF' || fail "reacher --version does not match wheel metadata"
import re
import sys
from importlib.metadata import version

reported = sys.argv[1].strip()
meta = version("reacher2p")
try:
    from packaging.version import Version
    same = Version(reported) == Version(meta)
except ImportError:
    # packaging is not a runtime dependency; fold the SemVer spellings by hand
    def norm(v):
        v = v.lower().lstrip("v")
        v = re.sub(r"[-_.]?(alpha|a)[-_.]?(\d+)", r"a\2", v)
        v = re.sub(r"[-_.]?(beta|b)[-_.]?(\d+)", r"b\2", v)
        return re.sub(r"[-_.]?(rc|c)[-_.]?(\d+)", r"rc\2", v)
    same = norm(reported) == norm(meta)
if not same:
    sys.exit(f"reacher --version printed {reported!r}, wheel metadata says {meta!r}")
print(f"ok: {reported} == {meta}")
PYEOF

echo "==> packaged firmware"
python3 -I - "$WHEEL" <<'PYEOF' || fail "wheel is missing firmware hex files"
import sys
import zipfile

names = zipfile.ZipFile(sys.argv[1]).namelist()
missing = [
    board for board in ("mega", "uno")
    if not any(n.startswith(f"reacher/hex/{board}/") and n.endswith(".hex") for n in names)
]
if missing:
    sys.exit(f"no .hex under reacher/hex/ for: {', '.join(missing)}")
print("ok: hex present for mega and uno")
PYEOF

echo "==> boot"
PORT="$(python3 -I -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
mkdir "$WORK/home"
HOME="$WORK/home" REACHER_NO_BROWSER=1 BROWSER=true REACHER_PORT="$PORT" \
    REACHER_HOST=127.0.0.1 "$REACHER" >"$LOG" 2>&1 &
SERVER_PID=$!

STATUS=""
for _ in $(seq 1 60); do
    kill -0 "$SERVER_PID" 2>/dev/null || fail "server exited before /health answered"
    STATUS="$(python3 -I - "$PORT" <<'PYEOF' || true
import sys
import urllib.request

try:
    print(urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=2).status)
except Exception:
    pass
PYEOF
)"
    [ "$STATUS" = "200" ] && break
    sleep 1
done
[ "$STATUS" = "200" ] || fail "/health did not return 200 within 60s (last status: ${STATUS:-none})"
echo "ok: /health 200 on port $PORT"
echo "wheel_smoke: PASS"
