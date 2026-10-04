#!/usr/bin/env python3
"""Regenerate models.json from the models actually served by CliProxyAPI.

Source of truth for the served id list: GET {base_url}/v1/models.
Metadata authority for every per-model property: models.dev api.json
(provider --models-dev-provider first, all other providers searched
alphabetically as fallback for served ids missing there).

This script is strictly read-only against the server: it probes the served
list and writes models.json. It never modifies the CliProxyAPI config.
Keeping the CliProxyAPI config itself in sync with models.dev is the job
of tools/sync-cpa.py.

Usage:
  sync-models.py [--models-json PATH] [--base-url URL] [--api-key KEY]
                 [--check] [--keep-retired]
                 [--models-dev-url URL] [--models-dev-file PATH]
                 [--models-dev-provider NAME]

  --check    report id drift only, exit 1 if models.json differs from served
             list (property drift is ignored). Writes nothing.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

REPO_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models.json")
DEFAULT_MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_MODELS_DEV_PROVIDER = "opencode-go"
UPSTREAM_ZEN_MODELS = "https://opencode.ai/zen/go/v1/models"


def http_get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def read_cpa_api_key():
    """First api-key from the local CliProxyAPI config (no secret in repo)."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(os.path.expanduser("~/.cli-proxy-api/config.yaml")) as f:
            cfg = yaml.safe_load(f)
        keys = cfg.get("api-keys") or []
        return keys[0] if keys else None
    except (OSError, ValueError):
        return None


def served_model_ids(base_url, api_key):
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return sorted(m["id"] for m in data.get("data", []))


def upstream_zen_ids():
    try:
        data = http_get_json(UPSTREAM_ZEN_MODELS, timeout=20)
        return sorted(m["id"] for m in data.get("data", []))
    except Exception as e:  # noqa: BLE001 - informational only
        print(f"warning: could not reach upstream zen models: {e}", file=sys.stderr)
        return []


def load_models_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"meta": {}, "models": []}


def load_models_dev(url, snapshot_file):
    """Full models.dev api.json object keyed by provider name."""
    if snapshot_file:
        with open(snapshot_file) as f:
            return json.load(f)
    print(f"downloading models.dev metadata from {url} ...")
    return http_get_json(url)


def build_models_dev_index(dev, primary):
    """Map model id -> (source provider, model object).

    The primary provider wins; served ids missing there fall back to all
    other providers searched in alphabetical order (deterministic).
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


def infer_reasoning_default(levels):
    if not levels:
        return None
    if "high" in levels:
        return "high"
    if "medium" in levels:
        return "medium"
    return levels[len(levels) // 2]


def effort_levels(src):
    levels = []
    for opt in src.get("reasoning_options") or []:
        if not isinstance(opt, dict) or opt.get("type") != "effort":
            continue
        for v in opt.get("values") or []:
            if v not in levels:
                levels.append(v)
    return levels


def option_types(src):
    return {o.get("type") for o in (src.get("reasoning_options") or []) if isinstance(o, dict)}


def entry_from_models_dev(mid, src, provider, prev):
    """Fresh models.json entry; every property comes from models.dev.

    models.dev carries no reasoning default, so it is preserved from the
    existing file for known ids and inferred for new ids -- except for
    toggle-only entries (empty levels + reasoning toggle), whose default
    is always 'none' (they take no effort level).
    """
    prev = prev or {}
    levels = effort_levels(src)
    mods = src.get("modalities") or {}
    limit = src.get("limit") or {}
    if prev:
        default = prev.get("reasoning_default", infer_reasoning_default(levels))
    else:
        default = infer_reasoning_default(levels)
    if not levels and "toggle" in option_types(src):
        default = "none"
    return {
        "id": mid,
        "display_name": src.get("name") or mid,
        "description": src.get("description"),
        "family": src.get("family"),
        "context_window": (limit.get("context")),
        "output_tokens": (limit.get("output")),
        "input_modalities": list(mods.get("input") or ["text"]),
        "output_modalities": list(mods.get("output") or ["text"]),
        "reasoning_default": default,
        "reasoning_levels": levels,
        "reasoning_toggle": "toggle" in option_types(src),
        "tool_call": bool(src.get("tool_call")),
        "attachment": src.get("attachment"),
        "temperature": src.get("temperature"),
        "structured_output": src.get("structured_output"),
        "models_dev_provider": provider,
    }


def unverified_entry(mid, prev):
    """Entry for an id found in NO models.dev provider."""
    prev = prev or {}
    return {
        "id": mid,
        "display_name": prev.get("display_name") or mid,
        "description": None,
        "family": None,
        "context_window": None,
        "output_tokens": None,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "reasoning_default": None,
        "reasoning_levels": [],
        "reasoning_toggle": False,
        "tool_call": False,
        "attachment": None,
        "temperature": None,
        "structured_output": None,
        "models_dev_provider": None,
        "unverified": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-json", default=REPO_DEFAULT)
    ap.add_argument("--base-url", default="http://127.0.0.1:8317")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--keep-retired", action="store_true")
    ap.add_argument("--models-dev-url", default=DEFAULT_MODELS_DEV_URL)
    ap.add_argument("--models-dev-file", default=None,
                    help="local models.dev api.json snapshot; skips download")
    ap.add_argument("--models-dev-provider", default=DEFAULT_MODELS_DEV_PROVIDER)
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("CPA_API_KEY") or read_cpa_api_key()
    if not api_key:
        print("error: no API key (pass --api-key, set CPA_API_KEY, or install pyyaml so the key can be read from the local CPA config)", file=sys.stderr)
        return 2

    served = served_model_ids(args.base_url, api_key)
    print(f"served by CliProxyAPI: {len(served)} models")

    try:
        dev = load_models_dev(args.models_dev_url, args.models_dev_file)
    except (OSError, ValueError) as e:
        print(f"error: could not load models.dev metadata: {e}", file=sys.stderr)
        return 2
    index = build_models_dev_index(dev, args.models_dev_provider)
    n_primary = sum(1 for p, _ in index.values() if p == args.models_dev_provider)
    print(f"models.dev: {len(index)} indexed models ({n_primary} from '{args.models_dev_provider}')")

    doc = load_models_json(args.models_json)
    prev_known = {m["id"]: m for m in doc.get("models", [])}

    added, retired = [], []
    for mid in served:
        if mid not in prev_known:
            added.append(mid)
    for mid in sorted(set(prev_known) - set(served)):
        retired.append(mid)

    # Refresh ALL properties from models.dev every run (not just new ids).
    known = {}
    fallback_sources = {}
    for mid in served:
        if mid in index:
            provider, src = index[mid]
            if provider != args.models_dev_provider:
                fallback_sources[mid] = provider
            known[mid] = entry_from_models_dev(mid, src, provider, prev_known.get(mid))
        else:
            known[mid] = unverified_entry(mid, prev_known.get(mid))
    if fallback_sources:
        print("models.dev fallback providers: "
              + ", ".join(f"{m} ({p})" for m, p in sorted(fallback_sources.items())))
    unverified = sorted(mid for mid in served if known[mid].get("unverified"))
    if unverified:
        print(f"in no models.dev provider (unverified): {', '.join(unverified)}")

    if args.keep_retired:
        for mid in retired:
            known[mid] = prev_known[mid]
    if added:
        print(f"new models: {', '.join(added)}")
    if retired:
        print(f"no longer served ({'kept' if args.keep_retired else 'removed'}): {', '.join(retired)}")
    if not added and not retired:
        print("models.json already matches the served list.")

    upstream = upstream_zen_ids()
    if upstream:
        served_set = set(served)
        print(f"upstream zen has {len(upstream)} models; "
              f"not served locally: {', '.join(sorted(set(upstream) - served_set)) or 'none'}")

    if args.check:
        return 1 if (added or retired) else 0

    doc = {
        "meta": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": args.base_url.rstrip("/") + "/v1/models",
            "models_dev": args.models_dev_url,
            "models_dev_provider": args.models_dev_provider,
            "count": len(known),
        },
        "models": [known[mid] for mid in sorted(known)],
    }
    with open(args.models_json, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"wrote {args.models_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
