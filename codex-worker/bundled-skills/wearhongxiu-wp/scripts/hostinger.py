#!/usr/bin/env python3
"""Hostinger cache operations locked to wearhongxiu.com."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ALLOWED_DOMAINS = {"wearhongxiu.com", "www.wearhongxiu.com"}


def load_env(path: Path) -> dict[str, str]:
    values = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


def config(path: str | None):
    cfg_path = Path(path) if path else Path(
        os.environ.get("WEARHONGXIU_WP_CONFIG", SKILL_DIR / "config.env")
    )
    cfg = load_env(cfg_path)
    keys = (
        "WEARHONGXIU_HOSTINGER_API_TOKEN", "WEARHONGXIU_HOSTINGER_USERNAME",
        "WEARHONGXIU_HOSTINGER_DOMAIN", "WEARHONGXIU_HOSTINGER_API_BASE",
    )
    for key in keys:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    required = keys[:3]
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise ValueError("missing Hostinger configuration: " + ", ".join(missing))
    if cfg["WEARHONGXIU_HOSTINGER_DOMAIN"].lower() not in ALLOWED_DOMAINS:
        raise ValueError("refusing cache operation for a non-Wearhongxiu domain")
    base = cfg.get("WEARHONGXIU_HOSTINGER_API_BASE") or "https://developers.hostinger.com"
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" or parsed.hostname != "developers.hostinger.com":
        raise ValueError("refusing an untrusted Hostinger API base")
    cfg["WEARHONGXIU_HOSTINGER_API_BASE"] = base.rstrip("/")
    return cfg


def cache_purge(cfg, directory: str | None):
    username = urllib.parse.quote(cfg["WEARHONGXIU_HOSTINGER_USERNAME"], safe="")
    domain = urllib.parse.quote(cfg["WEARHONGXIU_HOSTINGER_DOMAIN"], safe="")
    url = (
        f'{cfg["WEARHONGXIU_HOSTINGER_API_BASE"]}/api/hosting/v1/accounts/'
        f'{username}/websites/{domain}/cache/clear'
    )
    if directory:
        url += "?" + urllib.parse.urlencode({"directory": directory})
    request = urllib.request.Request(
        url, method="DELETE",
        headers={
            "Authorization": f'Bearer {cfg["WEARHONGXIU_HOSTINGER_API_TOKEN"]}',
            "Accept": "application/json",
            "User-Agent": "Codex-Wearhongxiu-WP/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            payload = json.loads(raw) if raw.strip() else {"success": True}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise ValueError(f"Hostinger HTTP {error.code}: {detail[:500]}") from None
    print(json.dumps({
        "domain": cfg["WEARHONGXIU_HOSTINGER_DOMAIN"],
        "cache_cleared": True,
        "response": payload,
    }, ensure_ascii=False, indent=2))


def main():
    root = argparse.ArgumentParser(description="Wearhongxiu Hostinger operations")
    root.add_argument("--config")
    sub = root.add_subparsers(dest="command", required=True)
    purge = sub.add_parser("cache-purge")
    purge.add_argument("--directory")
    args = root.parse_args()
    try:
        if args.command == "cache-purge":
            cache_purge(config(args.config), args.directory)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()

