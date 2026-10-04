#!/usr/bin/env python3
"""Keep the CliProxyAPI model list in sync with models.dev.

Coverage job (server side): every model id listed for --models-dev-provider
(default opencode-go, the provider matching the zen/go endpoint this server
fronts) should have a model entry in the CliProxyAPI config.yaml
(codex-api-key, openai-compatibility and claude-api-key sections), with
per-model fields (display-name, max-context-length, thinking levels)
matching models.dev. Note: input/output-modalities are only valid on
openai-compatibility entries (config.OpenAICompatibleModel). CodexModel
and ClaudeModel entries reject them, so this script never writes modality
fields outside openai-compatibility.

This script never touches models.json and it never reads it either: it
writes the CliProxyAPI model lists directly from models.dev. Unverified
concepts don't exist here -- anything missing from models.dev is treated
as a coverage gap, not a flagged entry. The probe and publish job
(models.json on this repo, strictly read-only against the server) is
tools/sync-models.py.

Usage:
  sync-cpa.py [--cpa-config PATH] [--models-dev-url URL]
              [--models-dev-file PATH] [--models-dev-provider NAME]
              [--check] [--apply] [--dry-run] [--restart]

  (no flags)   print coverage gaps and field diffs, write nothing
  --check      exit 1 if any model would be added or any field would change
  --apply      write changes (timestamped backup first)
  --dry-run    with --apply: print diffs, write nothing
  --restart    restart the cli-proxy-api user service after --apply
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import urllib.request

DEFAULT_MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_MODELS_DEV_PROVIDER = "opencode-go"
DEFAULT_CPA_CONFIG = os.path.expanduser("~/.cli-proxy-api/config.yaml")
UPSTREAM_ZEN_MODELS = "https://opencode.ai/zen/go/v1/models"
OPENCODE_GO_ENTRY = "OpenCode Go"

CPA_SECTIONS = ("codex-api-key", "openai-compatibility", "claude-api-key")

MISSING = object()  # sentinel for "key absent" in CPA field diffs


def http_get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_models_dev(url, snapshot_file):
    """Full models.dev api.json object keyed by provider name."""
    if snapshot_file:
        with open(snapshot_file) as f:
            return json.load(f)
    print(f"downloading models.dev metadata from {url} ...")
    return http_get_json(url)


def upstream_zen_ids():
    try:
        data = http_get_json(UPSTREAM_ZEN_MODELS, timeout=20)
        return sorted(m["id"] for m in data.get("data", []))
    except Exception as e:  # noqa: BLE001 - informational only
        print(f"warning: could not reach upstream zen models: {e}", file=sys.stderr)
        return []


def build_models_dev_index(dev, primary):
    """Map model id -> (source provider, model object).

    The primary provider wins; ids missing there fall back to all other
    providers searched in alphabetical order (deterministic).
    """
    index = {}
    providers = dev if isinstance(dev, dict) else {}
    prim = (providers.get(primary) or {}).get("models") or {}
    for mid, m in prim.items():
        index[mid] = (primary, m)
    for pname in sorted(providers):
        if pname == primary:
            continue
        for mid, m in ((providers.get(pname) or {}).get("models") or {}).items():
            if mid not in index:
                index[mid] = (pname, m)
    return index


def effort_levels(src):
    levels = []
    for opt in src.get("reasoning_options") or []:
        if not isinstance(opt, dict) or opt.get("type") != "effort":
            continue
        for v in opt.get("values") or []:
            if v not in levels:
                levels.append(v)
    return levels


def expected_fields(src, section):
    """Managed CPA per-model fields derived from a models.dev object.

    Values of None mean the key must be absent from the CPA model entry.
    input/output-modalities are only valid on openai-compatibility entries
    (config.OpenAICompatibleModel); CodexModel and ClaudeModel entries
    reject them, so they are only managed there.
    """
    limit = src.get("limit") or {}
    mods = src.get("modalities") or {}
    levels = effort_levels(src)
    fields = {
        "display-name": src.get("name"),
        "max-context-length": limit.get("context"),
        "thinking": {"levels": levels} if levels else None,
    }
    if section == "openai-compatibility":
        fields["input-modalities"] = list(mods.get("input") or ["text"])
        fields["output-modalities"] = list(mods.get("output") or ["text"])
    return fields


def fmt_val(v):
    if v is MISSING:
        return "<absent>"
    return repr(v)


def iter_cpa_models(cfg):
    """Yield (section, entry_name, model_dict) for every CPA model entry."""
    for section in CPA_SECTIONS:
        for e in cfg.get(section) or []:
            for m in e.get("models") or []:
                yield section, e.get("name"), m


def find_entries(cfg, mid):
    """All (section, CPA model dict) pairs matching an id by name or alias."""
    return [(section, m) for section, _, m in iter_cpa_models(cfg)
            if m.get("name") == mid or m.get("alias") == mid]


def diff_entry(model_dict, expected):
    """List of (field, old, new) where old/new may be MISSING."""
    diffs = []
    for field, want in expected.items():
        if want is None:
            if field in model_dict:
                diffs.append((field, model_dict[field], MISSING))
            continue
        if model_dict.get(field, MISSING) != want:
            diffs.append((field, model_dict.get(field, MISSING), want))
    return diffs


def main():
    ap = argparse.ArgumentParser(
        description="Sync the CliProxyAPI model list with models.dev")
    ap.add_argument("--cpa-config", default=DEFAULT_CPA_CONFIG)
    ap.add_argument("--models-dev-url", default=DEFAULT_MODELS_DEV_URL)
    ap.add_argument("--models-dev-file", default=None,
                    help="local models.dev api.json snapshot; skips download")
    ap.add_argument("--models-dev-provider", default=DEFAULT_MODELS_DEV_PROVIDER)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any model would be added or field changed")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --apply: print diffs, write nothing")
    ap.add_argument("--restart", action="store_true",
                    help="restart the cli-proxy-api user service after --apply")
    args = ap.parse_args()

    try:
        import yaml
    except ImportError:
        print("error: pyyaml is required (pip install pyyaml)", file=sys.stderr)
        return 2
    try:
        dev = load_models_dev(args.models_dev_url, args.models_dev_file)
    except (OSError, ValueError) as e:
        print(f"error: could not load models.dev metadata: {e}", file=sys.stderr)
        return 2
    try:
        with open(args.cpa_config) as f:
            cfg = yaml.safe_load(f) or {}
    except OSError as e:
        print(f"error: could not read CPA config: {e}", file=sys.stderr)
        return 2

    providers = dev if isinstance(dev, dict) else {}
    primary = (providers.get(args.models_dev_provider) or {}).get("models") or {}
    index = build_models_dev_index(dev, args.models_dev_provider)
    print(f"models.dev provider '{args.models_dev_provider}': {len(primary)} models")

    changed_entries = 0
    changed_fields = 0
    missing_ids = []
    for mid in sorted(primary):
        found = find_entries(cfg, mid)
        if not found:
            missing_ids.append(mid)
            print(f"cpa diff {mid}: <no entry> -> add to '{OPENCODE_GO_ENTRY}'")
            continue
        for section, m in found:
            expected = expected_fields(primary[mid], section)
            for field, old, new in diff_entry(m, expected):
                print(f"cpa diff {mid} [{section}]: {field}: {fmt_val(old)} -> {fmt_val(new)}")
            diffs = diff_entry(m, expected)
            if diffs:
                changed_entries += 1
                changed_fields += len(diffs)

    configured_names = {m.get("name") for _, _, m in iter_cpa_models(cfg)}
    extras = sorted(n for n in configured_names if n and n not in primary)
    if extras:
        print(f"configured locally but not in '{args.models_dev_provider}' "
              f"(kept as-is): {', '.join(extras)}")
        for mid in extras:
            if mid in index:
                print(f"  {mid}: known to models.dev via '{index[mid][0]}'")
            else:
                print(f"  {mid}: in no models.dev provider")

    upstream = upstream_zen_ids()
    if upstream:
        upstream_set, primary_set = set(upstream), set(primary)
        print(f"upstream zen serves {len(upstream_set)} models; "
              f"in zen but not models.dev '{args.models_dev_provider}': "
              f"{', '.join(sorted(upstream_set - primary_set)) or 'none'}")

    dirty = bool(missing_ids) or changed_entries > 0
    if not dirty:
        print("CPA config already matches models.dev metadata.")

    if args.check:
        return 1 if dirty else 0

    real_apply = args.apply and not args.dry_run
    if not real_apply:
        if args.apply:
            print(f"--dry-run: {len(missing_ids)} models would be added, "
                  f"{changed_entries} entries ({changed_fields} fields) "
                  f"would be updated; wrote nothing.")
        return 0

    target = next((e for e in cfg.get("openai-compatibility") or []
                   if e.get("name") == OPENCODE_GO_ENTRY), None)
    if missing_ids and target is None:
        print(f"error: no openai-compatibility entry named "
              f"'{OPENCODE_GO_ENTRY}' found", file=sys.stderr)
        return 2

    backup = args.cpa_config + ".bak-sync-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(args.cpa_config, backup)
    print(f"backed up CPA config to {backup}")

    for mid in sorted(primary):
        for section, m in find_entries(cfg, mid):
            expected = expected_fields(primary[mid], section)
            for field, want in expected.items():
                if want is None:
                    m.pop(field, None)
                else:
                    m[field] = want
    added = []
    for mid in missing_ids:
        expected = expected_fields(primary[mid], "openai-compatibility")
        item = {"name": mid, "alias": ""}
        for field, want in expected.items():
            if want is not None:
                item[field] = want
        target.setdefault("models", []).append(item)
        added.append(mid)
    with open(args.cpa_config, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    if added:
        print(f"added to CPA config: {', '.join(added)}")
    print("updated CPA model entries in place.")
    print("restart cli-proxy-api for changes to take effect (or pass --restart).")
    if args.restart:
        os.system("systemctl --user restart cli-proxy-api.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
