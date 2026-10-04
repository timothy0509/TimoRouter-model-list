#!/usr/bin/env bash
# Refresh timorouter models (non-interactive). Manual or scheduled runs:
#   opencode/update.sh [--models-url URL] [--config PATH]
set -euo pipefail

SHARE_DIR="${HOME}/.local/share/timorouter"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SYNC_PY=""
if [ -f "${SCRIPT_DIR}/sync.py" ]; then
  SYNC_PY="${SCRIPT_DIR}/sync.py"
elif [ -f "${SHARE_DIR}/sync.py" ]; then
  SYNC_PY="${SHARE_DIR}/sync.py"
else
  echo "error: sync.py not found; run install.sh first." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required but was not found on PATH." >&2
  exit 1
fi

for arg in "$@"; do
  case "${arg}" in
    -h|--help)
      echo "Usage: $(basename "$0") [--models-url URL] [--config PATH]"
      echo "Refreshes the timorouter provider model list (non-interactive)."
      exec python3 "${SYNC_PY}" --update --help
      ;;
  esac
done

exec python3 "${SYNC_PY}" --update "$@"
