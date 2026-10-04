#!/usr/bin/env python3
"""Install or refresh the `timorouter` provider in an opencode config.

The model list comes from this repo's models.json (Regeneratebed by
tools/sync-models.py on the server). This script only merges that list
into the provider block; everything else in the user's config is kept.

  install:  opencode/install.sh   (interactive: asks for base URL + API key,
            then offers to schedule --update every 60 minutes)
  refresh:  opencode/update.sh   (non-interactive: for systemd/cron)

Stdlib only. Never writes secrets anywhere except the user's own config.
"""
import argparse
import datetime
import getpass
import json
import os
import shutil
import subprocess
import sys
import urllib.request

MODELS_URL = "https://raw.githubusercontent.com/timothy0509/TimoRouter-model-list/main/models.json"
PROVIDER_ID = "timorouter"
LOCAL_DEFAULT_URL = "http://127.0.0.1:8317/v1"
REMOTE_DEFAULT_URL = "https://timopc.tailc18075.ts.net:8317/v1"
SHARE_DIR = os.path.expanduser("~/.local/share/timorouter")
SERVICE_NAME = "timorouter-sync"


def default_config_path():
    home = os.path.expanduser("~")
    return os.path.join(home, ".config", "opencode", "opencode.json")


def fetch_models(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        doc = json.loads(r.read().decode("utf-8"))
    models = doc.get("models", [])
    if not models:
        raise ValueError("models.json contained no models, refusing to touch config")
    return models


def opencode_model(entry):
    """Map a models.json entry to the opencode provider model schema."""
    m = {"name": entry.get("display_name") or entry["id"]}
    m["capabilities"] = {
        "tools": True,
        "input": entry.get("input") or ["text"],
        "output": ["text"],
    }
    limit = {}
    if entry.get("context_window"):
        limit["context"] = entry["context_window"]
    if entry.get("output_tokens"):
        limit["output"] = entry["output_tokens"]
    if limit:
        m["limit"] = limit
    m["compatibility"] = {"reasoningField": "reasoning_content"}
    levels = entry.get("reasoning_levels") or []
    if entry.get("reasoning_default"):
        m["settings"] = {"reasoningEffort": entry["reasoning_default"]}
    if levels:
        m["variants"] = [
            {"id": lv, "settings": {"reasoningEffort": lv}} for lv in levels
        ]
    return m


def load_config(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def backup_config(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"{path}.bak-timorouter-{stamp}"
    shutil.copy2(path, backup)
    # Keep only the 5 newest backups.
    keep = sorted(
        (p for p in os.listdir(os.path.dirname(path) or ".")
         if p.startswith(os.path.basename(path) + ".bak-timorouter-"))
    )
    for old in keep[:-5]:
        try:
            os.remove(os.path.join(os.path.dirname(path), old))
        except OSError:
            pass
    return backup


def merge_provider(cfg, models, base_url=None, api_key=None):
    providers = cfg.setdefault("providers", {})
    prov = providers.setdefault(PROVIDER_ID, {})
    prov["name"] = "TimoRouter"
    prov["package"] = "aisdk:@ai-sdk/openai-compatible"
    settings = prov.setdefault("settings", {})
    if base_url:
        settings["baseURL"] = base_url
    if api_key:
        settings["apiKey"] = api_key
    prov["models"] = {e["id"]: opencode_model(e) for e in models}
    return cfg


def write_config(path, cfg):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def local_cpa_reachable():
    import socket
    try:
        urllib.request.urlopen(LOCAL_DEFAULT_URL + "/models", timeout=2).close()
        return True
    except urllib.error.HTTPError:
        # Any HTTP response (even 401/404) means the server is up.
        return True
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return False


def prompt(text, default=None, secret=False):
    suffix = f" [{default}]" if default else ""
    reader = (lambda p: getpass.getpass(p)) if secret else input
    while True:
        val = reader(f"{text}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return default
        print("a value is required.")


def has_systemd_user():
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", "--property=Version"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_scheduler(share_dir, mode):
    sync_py = os.path.join(share_dir, "sync.py")
    if mode == "systemd" or (mode == "auto" and has_systemd_user()):
        unit_dir = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(unit_dir, exist_ok=True)
        with open(os.path.join(unit_dir, f"{SERVICE_NAME}.service"), "w") as f:
            f.write(
                "[Unit]\n"
                "Description=Refresh TimoRouter opencode models\n"
                "\n"
                "[Service]\n"
                "Type=oneshot\n"
                f"ExecStart=/usr/bin/python3 {sync_py} --update\n"
            )
        with open(os.path.join(unit_dir, f"{SERVICE_NAME}.timer"), "w") as f:
            f.write(
                "[Unit]\n"
                "Description=Refresh TimoRouter opencode models every 60 minutes\n"
                "\n"
                "[Timer]\n"
                "OnBootSec=5min\n"
                "OnUnitActiveSec=60min\n"
                f"Unit={SERVICE_NAME}.service\n"
                "\n"
                "[Install]\n"
                "WantedBy=timers.target\n"
            )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.timer"],
            check=False,
        )
        print(f"scheduled via systemd user timer ({SERVICE_NAME}.timer, every 60 min).")
        return
    # cron fallback
    line = f"7 * * * * /usr/bin/python3 {sync_py} --update >> {share_dir}/update.log 2>&1"
    try:
        existing = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        current = existing.stdout if existing.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        current = ""
    if sync_py in current:
        print("cron entry already present, left as is.")
        return
    new = (current.rstrip("\n") + "\n" if current.strip() else "") + line + "\n"
    subprocess.run(["crontab", "-"], input=new, text=True, check=True, timeout=10)
    print("scheduled via cron (hourly at :07).")


def do_update(models_url, config_path):
    models = fetch_models(models_url)
    cfg = load_config(config_path)
    if PROVIDER_ID not in cfg.get("providers", {}):
        print(f"error: provider '{PROVIDER_ID}' not in {config_path}; "
              "run install.sh first.", file=sys.stderr)
        return 2
    old_ids = set(cfg["providers"][PROVIDER_ID].get("models", {}))
    merge_provider(cfg, models)
    new_ids = set(cfg["providers"][PROVIDER_ID]["models"])
    backup = backup_config(config_path)
    write_config(config_path, cfg)
    print(f"updated {PROVIDER_ID}: {len(new_ids)} models "
          f"(+{len(new_ids - old_ids)}, -{len(old_ids - new_ids)}).")
    if backup:
        print(f"backup: {backup}")
    current = cfg.get("model", "")
    if current.startswith(PROVIDER_ID + "/") and current.split("/", 1)[1] not in new_ids:
        print(f"warning: your active model '{current}' is no longer served; "
              "pick a new one with /models.", file=sys.stderr)
    return 0


def do_install(args):
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(SHARE_DIR, exist_ok=True)
    for name in ("sync.py", "update.sh"):
        src = os.path.join(here, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(SHARE_DIR, name))
    print(f"helper scripts copied to {SHARE_DIR}")

    cfg = load_config(args.config)
    existing = cfg.get("providers", {}).get(PROVIDER_ID, {})
    existing_settings = existing.get("settings", {})
    default_url = (existing_settings.get("baseURL")
                   or (LOCAL_DEFAULT_URL if local_cpa_reachable() else REMOTE_DEFAULT_URL))
    base_url = args.base_url or prompt("CliProxyAPI base URL", default_url)
    api_key = (args.api_key or existing_settings.get("apiKey")
               or prompt("Your CliProxyAPI api key", secret=True))

    models = fetch_models(args.models_url)
    print(f"fetched {len(models)} models.")
    merge_provider(cfg, models, base_url=base_url, api_key=api_key)
    backup = backup_config(args.config)
    write_config(args.config, cfg)
    print(f"wrote {args.config}" + (f" (backup: {backup})" if backup else ""))

    if not args.no_schedule:
        if args.schedule == "none":
            print("skipping scheduler setup.")
        else:
            install_scheduler(SHARE_DIR, args.schedule)
    print("done. Pick a timorouter/* model in opencode with /models.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--models-url", default=MODELS_URL)
    ap.add_argument("--config", default=default_config_path())
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--no-schedule", action="store_true")
    ap.add_argument("--schedule", default="auto",
                    choices=["auto", "systemd", "cron", "none"])
    args = ap.parse_args()

    if args.install:
        return do_install(args)
    if args.update:
        return do_update(args.models_url, args.config)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
