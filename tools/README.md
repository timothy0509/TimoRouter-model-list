# Server sync tools

Two jobs, two scripts. Do not mix them.

## Probe and publish: `sync-models.py`

`sync-models.py` regenerates `models.json` from the models actually served by CliProxyAPI. The served list at `GET {base-url}/v1/models` is the source of truth for which ids are on the repo. Metadata (context windows, reasoning levels, input/output modalities) comes from [models.dev](https://models.dev/api.json), the metadata authority: provider `--models-dev-provider` (default `opencode-go`) first, other providers alphabetically as fallback, recorded per entry in `models_dev_provider`. Every run refreshes all properties from models.dev; only `reasoning_default` is preserved from the existing file (models.dev carries no default).

This script is read-only against the server. It probes the served list and writes `models.json`. It never modifies the CliProxyAPI config.

```sh
python3 tools/sync-models.py [--models-json PATH] [--base-url URL] [--api-key KEY]
                             [--check] [--keep-retired]
                             [--models-dev-url URL] [--models-dev-file PATH]
                             [--models-dev-provider NAME]
```

- `--check` - report id drift only, exit 1 if `models.json` differs from the served list. Property drift is ignored. Writes nothing.
- `--keep-retired` - keep entries that are no longer served instead of removing them.
- `--models-json` - path to write (default: `models.json` next to `tools/`).
- `--base-url` - CliProxyAPI base URL (default: `https://timopc.tailc18075.ts.net:8317`).
- `--api-key` - API key (default: `CPA_API_KEY` env var, else first `api-keys` entry from the local CliProxyAPI config; requires pyyaml).
- `--models-dev-url` - models.dev api.json URL (default: `https://models.dev/api.json`).
- `--models-dev-file` - local models.dev api.json snapshot; skips download (useful for tests).
- `--models-dev-provider` - primary models.dev provider (default: `opencode-go`).

Typical run on timopc:

```sh
python3 tools/sync-models.py --check
python3 tools/sync-models.py
git diff -- models.json
git add models.json && git commit -m "Update models.json" && git push
```

## Coverage: `sync-cpa.py`

`sync-cpa.py` keeps the CliProxyAPI model list in sync with models.dev. Every model id listed for `--models-dev-provider` (default `opencode-go`, the provider matching the zen/go endpoint this server fronts) should have a model entry in `~/.cli-proxy-api/config.yaml` (`codex-api-key`, `openai-compatibility` and `claude-api-key` sections), with per-model fields (`display-name`, `max-context-length`, `input/output-modalities`, `thinking.levels`) matching models.dev. Models configured locally but absent from the models.dev provider are reported and kept as-is. New ids are added to the `OpenCode Go` entry.

```sh
python3 tools/sync-cpa.py [--cpa-config PATH] [--models-dev-url URL]
                          [--models-dev-file PATH] [--models-dev-provider NAME]
                          [--check] [--apply] [--dry-run] [--restart]
```

- no flags - print coverage gaps and field diffs, write nothing.
- `--check` - exit 1 if any model would be added or any field would change.
- `--apply` - write changes (timestamped backup first).
- `--dry-run` - with `--apply`: print diffs, write nothing.
- `--restart` - restart the `cli-proxy-api` user service after `--apply`.
- `--cpa-config` - CliProxyAPI config path (default: `~/.cli-proxy-api/config.yaml`; point at a temp copy to test `--apply` safely).

Typical run on timopc:

```sh
python3 tools/sync-cpa.py --apply --dry-run
python3 tools/sync-cpa.py --apply --restart
```

## Hourly server refresh

GitHub Actions probes the served list hourly over the tailnet and commits only on drift. See `.github/workflows/refresh-models.yml`. It needs three repo secrets: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` (Tailscale OAuth client with auth keys scope, `tag:ci` allowed) and `CPA_API_KEY` (a CliProxyAPI api key). `tools/cron-example.txt` notes the same setup.
