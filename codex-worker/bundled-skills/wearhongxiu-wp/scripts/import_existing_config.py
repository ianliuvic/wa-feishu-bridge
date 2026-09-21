#!/usr/bin/env python3
"""One-time migration of existing Wearhongxiu credentials into this skill."""

from __future__ import annotations

import argparse
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_line(key: str, value: str) -> str:
    safe = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{safe}"'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wordpress", type=Path, required=True)
    parser.add_argument("--hostinger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    wp = load_env(args.wordpress)
    hostinger = load_env(args.hostinger)
    values = {
        "WEARHONGXIU_WP_URL": wp.get("WORDPRESS_BASE_URL", "https://wearhongxiu.com"),
        "WEARHONGXIU_WP_USER": wp.get("WORDPRESS_USERNAME", ""),
        "WEARHONGXIU_WP_APP_PASSWORD": wp.get("WORDPRESS_APPLICATION_PASSWORD", ""),
        "WEARHONGXIU_WP_TIMEOUT": "30",
        "WEARHONGXIU_HOSTINGER_API_TOKEN": hostinger.get("HOSTINGER_API_TOKEN", ""),
        "WEARHONGXIU_HOSTINGER_USERNAME": hostinger.get("HOSTINGER_USERNAME", ""),
        "WEARHONGXIU_HOSTINGER_DOMAIN": hostinger.get("HOSTINGER_DOMAIN", "wearhongxiu.com"),
        "WEARHONGXIU_HOSTINGER_API_BASE": hostinger.get(
            "HOSTINGER_API_BASE", "https://developers.hostinger.com"
        ),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise SystemExit("error: missing source values: " + ", ".join(missing))
    if values["WEARHONGXIU_WP_URL"].rstrip("/") != "https://wearhongxiu.com":
        raise SystemExit("error: source WordPress config is not Wearhongxiu")
    if values["WEARHONGXIU_HOSTINGER_DOMAIN"].lower() not in {
        "wearhongxiu.com", "www.wearhongxiu.com"
    }:
        raise SystemExit("error: source Hostinger config is not Wearhongxiu")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "# Dedicated configuration for the wearhongxiu-wp skill.\n"
        + "\n".join(env_line(key, value) for key, value in values.items())
        + "\n",
        encoding="utf-8",
    )
    print("Wearhongxiu credentials migrated into the independent skill configuration.")


if __name__ == "__main__":
    main()

