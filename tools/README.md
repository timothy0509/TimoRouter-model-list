# Server sync tools

`sync-models.py` regenerates `models.json` from the models actually served by CliProxyAPI. The served list at `GET {base-url}/v1/models` is the source of truth. Metadata (context windows, reasoning levels, input modalities) comes from [models.dev](https://models.dev/api.json), the metadata authority: provider `--models-dev-provider` (default `opencode-go`) first, other providers alphabetically as fallback, recorded per entry in `models_dev_provider`. Every run refreshes all properties from models.dev; only `reasoning_default` is preserved from the existing file (models.dev carries no default).

## Flags

```sh
python3 tools/sync-models.py [--models-json PATH] [--base-url URL] [--api-key KEY]
                             [--check] [--apply] [--restart] [--keep-retired] [--dry-run]
                             [--models-dev-url URL] [--models-dev-file PATH]
                             [--models-dev-provider NAME] [--cpa-config PATH]
```

- `--check` - report id drift only, exit 1 if `models.json` differs from the served list. Property drift is ignored. Writes nothing.
- `--apply` - also sync full per-model fields into the model entries in `~/.cli-proxy-api/config.yaml` (`codex-api-key`, `openai-compatibility`, `claude-api-key` sections), with a timestamped backup. Served models missing everywhere are added to the `OpenCode Go` entry.
- `--dry-run` - with `--apply`: print all diffs but write nothing (neither `models.json` nor the CPA config). Per-model CPA field diffs print on every run regardless (informational).
- `--restart` - restart the `cli-proxy-api` user service after `--apply`.
- `--keep-retired` - keep entries that are no longer served instead of removing them.
- `--models-json` - path to write (default: `models.json` next to `tools/`).
- `--base-url` - CliProxyAPI base URL (default: `http://127.0.0.1:8317`).
- `--api-key` - API key (default: `CPA_API_KEY` env var, else first `api-keys` entry from the local CliProxyAPI config; requires pyyaml).
- `--models-dev-url` - models.dev api.json URL (default: `https://models.dev/api.json`).
- `--models-dev-file` - local models.dev api.json snapshot; skips download (useful for tests).
- `--models-dev-provider` - primary models.dev provider (default: `opencode-go`).
- `--cpa-config` - CliProxyAPI config path (default: `~/.cli-proxy-api/config.yaml`; point at a temp copy to test `--apply` safely).

Typical run on timopc:

```sh
python3 tools/sync-models.py --check
python3 tools/sync-models.py
git diff -- models.json
git add models.json && git commit -m "Update models.json" && git push
```

## Hourly server refresh

Probe the served list hourly and commit only on drift. See `cron-example.txt` (copy to `crontab -e`, adjust paths):

```sh
7 * * * * cd /path/to/TimoRouter-model-list && /usr/bin/python3 tools/sync-models.py --check >> /var/log/timorouter-sync.log 2>&1 || ( /usr/bin/python3 tools/sync-models.py >> /var/log/timorouter-sync.log 2>&1 && git add models.json && git -c user.name=timorouter-sync -c user.email=timorouter-sync@localhost commit -m "Update models.json" >> /var/log/timorouter-sync.log 2>&1 && git push >> /var/log/timorouter-sync.log 2>&1 )
```

Prefer a systemd user timer on timopc if one already manages the server; cron is the fallback.
