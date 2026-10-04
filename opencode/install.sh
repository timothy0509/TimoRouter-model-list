#!/usr/bin/env bash
# Install the `timorouter` provider into opencode config.
#
# Usage:
#   opencode/install.sh [--models-url URL] [--config PATH]
#                       [--base-url URL] [--api-key KEY]
#                       [--no-schedule] [--schedule auto|systemd|cron|none]
#
# When run from a cloned repo it uses the sibling sync.py.
# When curled (no sibling sync.py present) it downloads sync.py from
# GitHub raw into ~/.local/share/timorouter/ first.
# All flags are passed through to `sync.py --install`.
set -euo pipefail

PROVIDER_REPO="timothy0509/TimoRouter-model-list"
RAW_SYNC_URL="https://raw.githubusercontent.com/${PROVIDER_REPO}/main/opencode/sync.py"
SHARE_DIR="${HOME}/.local/share/timorouter"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [OPTIONS]

Install the timorouter provider into your opencode config.

Options:
  --models-url URL   Override models.json source URL
  --config PATH      Override opencode config path
                     (default: ~/.config/opencode/opencode.json)
  --base-url URL     CliProxyAPI base URL (else prompted)
  --api-key KEY      CliProxyAPI api key (else prompted)
  --no-schedule      Do not set up hourly --update scheduling
  --schedule MODE    auto|systemd|cron|none (default: auto)
  -h, --help         Show this help and sync.py --install help

All options are passed through to sync.py --install (stdlib only,
secrets only land in your own opencode config).
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SYNC="${SCRIPT_DIR}/sync.py"
SYNC_PY=""

if [ -f "${LOCAL_SYNC}" ]; then
  SYNC_PY="${LOCAL_SYNC}"
else
  SYNC_PY="${SHARE_DIR}/sync.py"
  if [ ! -f "${SYNC_PY}" ]; then
    echo "downloading sync.py to ${SYNC_PY} ..." >&2
    mkdir -p "${SHARE_DIR}"
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "${RAW_SYNC_URL}" -o "${SYNC_PY}"
    elif command -v wget >/dev/null 2>&1; then
      wget -qO "${SYNC_PY}" "${RAW_SYNC_URL}"
    else
      python3 -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "${RAW_SYNC_URL}" "${SYNC_PY}"
    fi
  fi
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required but was not found on PATH." >&2
  exit 1
fi

for arg in "$@"; do
  case "${arg}" in
    -h|--help)
      usage
      echo "--- sync.py --install help ---"
      exec python3 "${SYNC_PY}" --install --help
      ;;
  esac
done

exec python3 "${SYNC_PY}" --install "$@"
