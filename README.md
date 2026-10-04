# TimoRouter model list

Model list served by CliProxyAPI on timopc, plus client installers that consume it.

`models.json` is generated from `GET /v1/models` on the server. Clients fetch it from this repo and merge it into their local config. No secrets are stored in this repo.

## Layout

- `models.json` - generated list with metadata (context window, input modalities, reasoning levels).
- `tools/` - server-side generator (`sync-models.py`) and refresh docs.
- `opencode/` - opencode client helper (`sync.py`) plus `install.sh` and `update.sh`.
- `claude/` - Claude Code client installer.
- `codex/` - Codex client installer.

## Server maintainer workflow

Run on timopc, where CliProxyAPI is reachable at `http://127.0.0.1:8317`:

```sh
python3 tools/sync-models.py --check
python3 tools/sync-models.py
```

If new models are served but missing from the CliProxyAPI config:

```sh
python3 tools/sync-models.py --apply
python3 tools/sync-models.py --apply --restart
```

`--check` reports drift only (exit 1 on drift). `--apply` appends missing models to the `OpenCode Go` entry in `~/.cli-proxy-api/config.yaml` with a timestamped backup. `--restart` restarts the `cli-proxy-api` user service after applying.

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
