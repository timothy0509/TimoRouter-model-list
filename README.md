# TimoRouter model list

Model list served by CliProxyAPI on timopc, plus client installers that consume it.

`models.json` is generated from `GET /v1/models` on the server. Clients fetch it from this repo and merge it into their local config. No secrets are stored in this repo.

## Layout

- `models.json` - generated list with full CliProxyAPI-supported properties per model (context window, input/output modalities, reasoning levels and toggle default, tool calling, attachment, temperature, structured output, family). Metadata comes from models.dev (`opencode-go` provider, fallback recorded per entry).
- `tools/` - server scripts: `sync-models.py` (probe served list, publish `models.json`) and `sync-cpa.py` (keep CliProxyAPI config in sync with models.dev), plus refresh docs.
- `opencode/` - opencode client helper (`sync.py`) plus `install.sh` and `update.sh`.
- `claude/` - Claude Code client installer.
- `codex/` - Codex client installer.

## Server maintainer workflow

Run on timopc, where CliProxyAPI is reachable at `http://127.0.0.1:8317`:

```sh
python3 tools/sync-models.py --check
python3 tools/sync-models.py
```

`--check` reports id drift only (exit 1 on drift). `sync-models.py` only probes the server and writes the repo file. It never touches the CliProxyAPI config.

To bring the CliProxyAPI config itself in line with models.dev (new ids get entries, stale fields get corrected):

```sh
python3 tools/sync-cpa.py --apply --dry-run
python3 tools/sync-cpa.py --apply --restart
```

`--apply` writes with a timestamped backup. `--restart` restarts the `cli-proxy-api` user service after applying.

Then publish:

```sh
git diff -- models.json
git add models.json
git commit -m "Update models.json"
git push
```

See `tools/README.md` for all flags and the hourly server refresh.

## Client install

```sh
curl -fsSL https://raw.githubusercontent.com/timothy0509/TimoRouter-model-list/main/opencode/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/timothy0509/TimoRouter-model-list/main/claude/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/timothy0509/TimoRouter-model-list/main/codex/install.sh | bash
```

Each installer asks for the CliProxyAPI base URL and API key, writes only the user's local config, and offers the 60-minute updater below.

## Automatic refresh every 60 minutes

Clients schedule `sync.py --update` every 60 minutes: a systemd user timer (`timorouter-sync.timer`) when systemd is available, otherwise a cron entry (hourly at `:07`). The updater re-fetches `models.json` from this repo and keeps existing base URL and key.

## Secrets

No API keys in this repo. Server scripts read the key from `CPA_API_KEY` or the first `api-keys` entry in `~/.cli-proxy-api/config.yaml`. Client installers store the key only in the user's own config file.
