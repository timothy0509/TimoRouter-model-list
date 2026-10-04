#!/usr/bin/env python3
"""Regenerate models.json from the models actually served by CliProxyAPI.

Source of truth for the served list: GET {base_url}/v1/models.
Metadata authority for every per-model property: models.dev api.json
(provider --models-dev-provider first, all other providers searched
alphabetically as fallback for served ids missing there).

Usage:
  sync-models.py [--models-json PATH] [--base-url URL] [--api-key KEY]
                 [--check] [--apply] [--restart] [--keep-retired] [--dry-run]
                 [--models-dev-url URL] [--models-dev-file PATH]
                 [--models-dev-provider NAME] [--cpa-config PATH]

  --check    report id drift only, exit 1 if models.json differs from served
             list (property drift is ignored). Writes nothing.
  --apply    also sync full per-model fields into the CliProxyAPI config.yaml
             model entries (codex-api-key, openai-compatibility, claude-api-key
             sections), with backup. Adds served models missing everywhere to
             the "OpenCode Go" openai-compatibility entry.
  --dry-run  print all diffs but write nothing (neither models.json nor CPA
             config). Most useful combined with --apply.
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
DEFAULT_MODELS_DEV_URL = "https://models.dev/api.json"
DEFAULT_MODELS_DEV_PROVIDER = "opencode-go"
DEFAULT_CPA_CONFIG = os.path.expanduser("~/.cli-proxy-api/config.yaml")
UPSTREAM_ZEN_MODELS = "https://opencode.ai/zen/go/v1/models"

CPA_SECTIONS = ("codex-api-key", "openai-compatibility", "claude-api-key")

MISSING = object()  # sentinel for "key absent" in CPA field diffs


def http_get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def read_cpa_api_key(cpa_config):
    """First api-key from the local CliProxyAPI config (no secret in repo)."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        with open(cpa_config) as f:
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
    existing file for known ids and inferred for new ids.
    """
    prev = prev or {}
    levels = effort_levels(src)
    mods = src.get("modalities") or {}
    limit = src.get("limit") or {}
    if prev:
        default = prev.get("reasoning_default", infer_reasoning_default(levels))
    else:
        default = infer_reasoning_default(levels)
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


def fmt_val(v):
    if v is MISSING:
        return "<absent>"
    return repr(v)


def cpa_expected_fields(entry):
    """Managed CPA per-model fields derived from a models.json entry.

    Values of None mean the key must be absent from the CPA model entry.
    """
    return {
        "display-name": entry.get("display_name"),
        "max-context-length": entry.get("context_window"),
        "input-modalities": list(entry.get("input_modalities") or ["text"]),
        "output-modalities": list(entry.get("output_modalities") or ["text"]),
        "thinking": {"levels": list(entry.get("reasoning_levels") or [])}
        if (entry.get("reasoning_levels") or []) else None,
    }


def iter_cpa_models(cfg):
    """Yield (section, entry_name, model_dict) for every CPA model entry."""
    for section in CPA_SECTIONS:
        for e in cfg.get(section) or []:
            for m in e.get("models") or []:
                yield section, e.get("name"), m


def diff_cpa_entry(model_dict, expected):
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


def sync_cpa_config(served, known, cpa_config, dry_run):
    """Diff (and with --apply, write) full per-model CPA fields.

    Returns (changed_entries, changed_fields, missing_ids) computed from the
    pre-write state. Never touches keys outside the managed field set.
    """
    import yaml

    with open(cpa_config) as f:
        cfg = yaml.safe_load(f) or {}

    changed_entries = 0
    changed_fields = 0
    missing_ids = []
    writes = []  # (model_dict, expected) mutations to apply

    for mid in served:
        entry = known.get(mid)
        if entry is None:
            continue
        expected = cpa_expected_fields(entry)
        found = False
        for section, ename, m in iter_cpa_models(cfg):
            if m.get("name") != mid and m.get("alias") != mid:
                continue
            found = True
            diffs = diff_cpa_entry(m, expected)
            for field, old, new in diffs:
                print(f"cpa diff {mid} [{section}/{ename}]: {field}: {fmt_val(old)} -> {fmt_val(new)}")
            if diffs:
                changed_entries += 1
                changed_fields += len(diffs)
                writes.append((m, expected))
        if not found:
            missing_ids.append(mid)
            print(f"cpa diff {mid}: <no entry in {', '.join(CPA_SECTIONS)}> -> add to 'OpenCode Go'")

    if not writes and not missing_ids:
        print("CPA config already matches models.dev metadata.")
        return changed_entries, changed_fields, missing_ids

    if dry_run:
        print(f"--dry-run: {changed_entries} entries ({changed_fields} fields) "
              f"would be updated, {len(missing_ids)} models would be added; wrote nothing.")
        return changed_entries, changed_fields, missing_ids

    target = next((e for e in cfg.get("openai-compatibility") or [] if e.get("name") == "OpenCode Go"), None)
    if missing_ids and target is None:
        print("error: no openai-compatibility entry named 'OpenCode Go' found", file=sys.stderr)
        return changed_entries, changed_fields, missing_ids

    backup = cpa_config + ".bak-sync-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(cpa_config, backup)
    print(f"backed up CPA config to {backup}")

    for m, expected in writes:
        for field, want in expected.items():
            if want is None:
                m.pop(field, None)
            else:
                m[field] = want
    added = []
    for mid in missing_ids:
        entry = known[mid]
        expected = cpa_expected_fields(entry)
        item = {"name": mid, "alias": ""}
        for field, want in expected.items():
            if want is not None:
                item[field] = want
        target.setdefault("models", []).append(item)
        added.append(mid)
    with open(cpa_config, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    if added:
        print(f"added to CPA config: {', '.join(added)}")
    print(f"updated {len(writes)} CPA model entries in place.")
    print("restart cli-proxy-api for changes to take effect (or pass --restart).")
    return changed_entries, changed_fields, missing_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-json", default=REPO_DEFAULT)
    ap.add_argument("--base-url", default="http://127.0.0.1:8317")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--keep-retired", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print diffs but write nothing (models.json nor CPA config)")
    ap.add_argument("--models-dev-url", default=DEFAULT_MODELS_DEV_URL)
    ap.add_argument("--models-dev-file", default=None,
                    help="local models.dev api.json snapshot; skips download")
    ap.add_argument("--models-dev-provider", default=DEFAULT_MODELS_DEV_PROVIDER)
    ap.add_argument("--cpa-config", default=DEFAULT_CPA_CONFIG)
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("CPA_API_KEY") or read_cpa_api_key(args.cpa_config)
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

    # CPA field sync preview: informational on every run, including --check.
    # A real --apply run below prints the same diffs as it writes, so it
    # needs no separate preview.
    real_apply = args.apply and not args.dry_run and not args.check
    if not real_apply:
        try:
            sync_cpa_config(served, known, args.cpa_config, dry_run=True)
        except ImportError:
            print("warning: pyyaml not installed; skipping CPA field diff", file=sys.stderr)
        except OSError as e:
            print(f"warning: could not read CPA config: {e}", file=sys.stderr)

    if args.check:
        return 1 if (added or retired) else 0

    if args.dry_run:
        print("--dry-run: wrote nothing (models.json and CPA config unchanged).")
        return 0

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

    if args.apply:
        try:
            sync_cpa_config(served, known, args.cpa_config, dry_run=False)
        except ImportError:
            print("error: pyyaml is required for --apply", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"error: could not update CPA config: {e}", file=sys.stderr)
            return 2
        if args.restart:
            os.system("systemctl --user restart cli-proxy-api.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
