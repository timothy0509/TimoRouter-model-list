#!/usr/bin/env bash
# Install / refresh Codex config.toml + model catalog from models.json.
# Interactive by default (asks for base URL + bearer token + default model);
# pass --non-interactive with flags for unattended runs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE_DIR="${SHARE_DIR:-$HOME/.local/share/timorouter}"
TARGET="$SHARE_DIR/codex-sync.py"

mkdir -p "$SHARE_DIR"
cp "$HERE/sync.py" "$TARGET"
echo "copied sync.py -> $TARGET"

exec python3 "$TARGET" "$@"
