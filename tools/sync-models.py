#!/usr/bin/env python3
"""Regenerate models.json from the models actually served by CliProxyAPI.

Source of truth for the served list: GET {base_url}/v1/models.
Richer per-model metadata (context windows, reasoning levels, modalities)
is preserved from the existing models.json and auto-enriched from local
files when available:
  - ~/.config/opencode/opencode.json  (timorouter provider block)
  - ~/.codex/models-multi-agent.json  (model catalog)

Usage:
  sync-models.py [--models-json PATH] [--base-url URL]
                 [--check] [--apply] [--restart] [--keep-retired]

  --check    report drift only, exit 1 if models.json differs from served list
  --apply    also add newly-served models missing from the CliProxyAPI
             config.yaml (openai-compatibility entry), with backup
  --restart  restart the cli-proxy-api user service after --apply
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import urllib.request

REPO_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models.json")
CPA_CONFIG = os.path.expanduser("~/.cli-proxy-api/config.yaml")
OPENCODE_CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")
CODEX_CATALOG = os.path.expanduser("~/.codex/models-multi-agent.json")
UPSTREAM_ZEN_MODELS = "https://opencode.ai/zen/go/v1/models"


def http_get_json(url, timeout=20):
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
        with open(CPA_CONFIG) as f:
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
        data = http_get_json(UPSTREAM_ZEN_MODELS)
        return sorted(m["id"] for m in data.get("data", []))
    except Exception as e:  # noqa: BLE001 - informational only
        print(f"warning: could not reach upstream zen models: {e}", file=sys.stderr)
        return []


def load_models_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"meta": {}, "models": []}


def enrich_from_opencode(entry):
    """Fill missing fields from the local opencode timorouter block."""
    try:
        with open(OPENCODE_CONFIG) as f:
            cfg = json.load(f)
        models = cfg["providers"]["timorouter"]["models"]
    except (OSError, KeyError, ValueError):
        return entry
    src = models.get(entry["id"])
    if src:
        fill_from_opencode_model(entry, src)
        return entry
    # Fall back to the CliProxyAPI openai-compatibility entry (no secrets involved).
    for e in cpa_openai_models():
        if e.get("name") == entry["id"] or e.get("alias") == entry["id"]:
            fake = {"limit": {}, "capabilities": {}, "settings": {}, "variants": []}
            if e.get("input-modalities"):
                fake["capabilities"]["input"] = e["input-modalities"]
            if (e.get("thinking") or {}).get("levels"):
                fake["variants"] = [{"id": lv} for lv in e["thinking"]["levels"]]
            fill_from_opencode_model(entry, fake)
    return entry


def cpa_openai_models():
    try:
        import yaml
    except ImportError:
        return []
    try:
        with open(CPA_CONFIG) as f:
            cfg = yaml.safe_load(f)
    except (OSError, ValueError):
        return []
    out = []
    for e in cfg.get("openai-compatibility") or []:
        out.extend(e.get("models") or [])
    return out


def fill_from_opencode_model(entry, src):
    caps = src.get("capabilities", {})
    limit = src.get("limit", {})
    settings = src.get("settings", {})
    variants = src.get("variants", [])
    if entry.get("display_name") in (None, "", entry["id"]):
        entry["display_name"] = src.get("name", entry["id"])
    if entry.get("context_window") is None and limit.get("context"):
        entry["context_window"] = limit["context"]
    if entry.get("output_tokens") is None and limit.get("output"):
        entry["output_tokens"] = limit["output"]
    if entry.get("input") in (None, ["text"]) and caps.get("input"):
        entry["input"] = caps["input"]
    if entry.get("reasoning_default") is None and settings.get("reasoningEffort"):
        entry["reasoning_default"] = settings["reasoningEffort"]
    if not entry.get("reasoning_levels") and variants:
        entry["reasoning_levels"] = [v["id"] for v in variants if v.get("id")]


def enrich_from_codex_catalog(entry):
    """Fill still-missing fields from the local codex model catalog."""
    try:
        with open(CODEX_CATALOG) as f:
            catalog = json.load(f)
    except (OSError, ValueError):
        return entry
    for m in catalog.get("models", []):
        if m.get("slug") != entry["id"]:
            continue
        if entry.get("display_name") in (None, "", entry["id"]):
            entry["display_name"] = m.get("display_name", entry["id"])
        if entry.get("context_window") is None and m.get("context_window"):
            entry["context_window"] = m["context_window"]
        if entry.get("input") in (None, ["text"]) and m.get("input_modalities"):
            entry["input"] = m["input_modalities"]
        if entry.get("reasoning_default") is None and m.get("default_reasoning_level"):
            entry["reasoning_default"] = m["default_reasoning_level"]
        if not entry.get("reasoning_levels"):
            levels = [l["effort"] for l in m.get("supported_reasoning_levels", []) if l.get("effort")]
            if levels:
                entry["reasoning_levels"] = levels
        break
    return entry


def skeleton(model_id):
    return {
        "id": model_id,
        "display_name": model_id,
        "context_window": None,
        "output_tokens": None,
        "input": ["text"],
        "reasoning_default": None,
        "reasoning_levels": [],
        "unverified": True,
    }


def apply_to_cpa_config(new_ids, known):
    """Add missing models to the OpenCode Go openai-compatibility entry."""
    import yaml

    with open(CPA_CONFIG) as f:
        cfg = yaml.safe_load(f)
    entries = cfg.get("openai-compatibility") or []
    target = next((e for e in entries if e.get("name") == "OpenCode Go"), None)
    if target is None:
        print("error: no openai-compatibility entry named 'OpenCode Go' found", file=sys.stderr)
        return False
    existing = {m.get("name") for m in target.get("models", [])}
    added = []
    for mid in new_ids:
        if mid in existing:
            continue
        info = known.get(mid, {})
        item = {"name": mid, "alias": ""}
        levels = info.get("reasoning_levels") or []
        if levels:
            item["thinking"] = {"levels": levels}
        target.setdefault("models", []).append(item)
        added.append(mid)
    if not added:
        print("CPA config already covers all served models.")
        return True
    backup = CPA_CONFIG + ".bak-sync-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(CPA_CONFIG, backup)
    print(f"backed up CPA config to {backup}")
    with open(CPA_CONFIG, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    print(f"added to CPA config: {', '.join(added)}")
    print("restart cli-proxy-api for changes to take effect (or pass --restart).")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-json", default=REPO_DEFAULT)
    ap.add_argument("--base-url", default="http://127.0.0.1:8317")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--keep-retired", action="store_true")
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("CPA_API_KEY") or read_cpa_api_key()
    if not api_key:
        print("error: no API key (pass --api-key, set CPA_API_KEY, or install pyyaml so the key can be read from the local CPA config)", file=sys.stderr)
        return 2

    served = served_model_ids(args.base_url, api_key)
    print(f"served by CliProxyAPI: {len(served)} models")

    doc = load_models_json(args.models_json)
    known = {m["id"]: m for m in doc.get("models", [])}

    added, retired = [], []
    for mid in served:
        if mid not in known:
            entry = skeleton(mid)
            entry = enrich_from_opencode(entry)
            entry = enrich_from_codex_catalog(entry)
            if (entry["context_window"] is not None or entry["reasoning_levels"]):
                entry.pop("unverified", None)
            known[mid] = entry
            added.append(mid)

    current_ids = set(known)
    for mid in sorted(current_ids - set(served)):
        retired.append(mid)
        if not args.keep_retired:
            del known[mid]

    # Backfill gaps in entries that are still unverified or missing metadata.
    for mid in served:
        entry = known.get(mid)
        if entry is None:
            continue
        if entry.get("unverified") or entry.get("context_window") is None:
            entry = enrich_from_opencode(entry)
            entry = enrich_from_codex_catalog(entry)
            if entry["context_window"] is not None or entry["reasoning_levels"]:
                entry.pop("unverified", None)
            known[mid] = entry

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
            "count": len(served),
        },
        "models": [known[mid] for mid in sorted(known)],
    }
    with open(args.models_json, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"wrote {args.models_json}")

    if args.apply and added:
        ok = apply_to_cpa_config(added, known)
        if not ok:
            return 2
        if args.restart:
            os.system("systemctl --user restart cli-proxy-api.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
