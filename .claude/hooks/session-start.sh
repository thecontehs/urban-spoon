#!/bin/bash
# SessionStart hook: install pinned Python dependencies for the watch skill in
# Claude Code cloud sessions. Local sessions are left alone (exit 0 immediately).
# Idempotent: skips pip when requirements are unchanged since the last success.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
VENV="${HOME}/.cache/urban-spoon/venv"     # outside the repo; never committed
LOG="${HOME}/.cache/urban-spoon/session-start.log"
STAMP="${VENV}/.requirements.sha256"
mkdir -p "$(dirname "$VENV")"

want="$(cat "$PROJECT_DIR/requirements.txt" "$PROJECT_DIR/requirements-dev.txt" | sha256sum | cut -d' ' -f1)"
have="$(cat "$STAMP" 2>/dev/null || true)"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" >>"$LOG" 2>&1
  have=""
fi

if [ "$want" != "$have" ]; then
  # Wheels only (no sdist build scripts); exact pins from the repo's requirement files.
  if "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --only-binary=:all: \
       -r "$PROJECT_DIR/requirements-dev.txt" >>"$LOG" 2>&1; then
    echo "$want" > "$STAMP"
  else
    echo "urban-spoon session-start: pip install failed; see $LOG" >&2
    exit 0   # never block the session; preflight.py will report what is missing
  fi
fi

# Put the venv's yt-dlp, deno and pytest on PATH for the rest of the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  line="export PATH=\"$VENV/bin:\$PATH\""
  grep -qxF "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || echo "$line" >> "$CLAUDE_ENV_FILE"
fi
exit 0
