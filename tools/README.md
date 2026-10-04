# Server sync tools

`sync-models.py` regenerates `models.json` from the models actually served by CliProxyAPI. The served list at `GET {base-url}/v1/models` is the source of truth. Metadata (context windows, reasoning levels, input modalities) is preserved from the existing `models.json` and backfilled from local files when available (`~/.config/opencode/opencode.json`, `~/.codex/models-multi-agent.json`).

## Flags

```sh
python3 tools/sync-models.py [--models-json PATH] [--base-url URL] [--api-key KEY]
                             [--check] [--apply] [--restart] [--keep-retired]
```

- `--check` - report drift only, exit 1 if `models.json` differs from the served list. Writes nothing.
- `--apply` - also append newly served models missing from the `OpenCode Go` entry in `~/.cli-proxy-api/config.yaml`, with a timestamped backup.
- `--restart` - restart the `cli-proxy-api` user service after `--apply`.
- `--keep-retired` - keep entries that are no longer served instead of removing them.
- `--models-json` - path to write (default: `models.json` next to `tools/`).
- `--base-url` - CliProxyAPI base URL (default: `http://127.0.0.1:8317`).
- `--api-key` - API key (default: `CPA_API_KEY` env var, else first `api-keys` entry from the local CliProxyAPI config; requires pyyaml).

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
