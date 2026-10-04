#!/usr/bin/env bash
# Install (or refresh) the TimoRouter model picks in ~/.claude/settings.json.
# Copies sync.py to ~/.local/share/timorouter/claude-sync.py, then runs it.
# Interactive by default; extra args are passed through to sync.py
# (e.g. ./install.sh --non-interactive --sonnet <id> --haiku <id> ...).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE_DIR="${SHARE_DIR:-$HOME/.local/share/timorouter}"
mkdir -p "$SHARE_DIR"
cp "$HERE/sync.py" "$SHARE_DIR/claude-sync.py"
echo "helper script copied to $SHARE_DIR/claude-sync.py"

exec python3 "$SHARE_DIR/claude-sync.py" "$@"
