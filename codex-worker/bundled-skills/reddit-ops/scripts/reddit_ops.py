#!/usr/bin/env python3
"""Client for the private reddit-ops service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def config() -> tuple[str, str]:
    base = os.getenv("REDDIT_OPS_URL", "https://reddit-ops.yiswim.cloud").strip().rstrip("/")
    key = os.getenv("REDDIT_OPS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("REDDIT_OPS_API_KEY is not configured")
    return base, key


def request(method: str, path: str, data: dict | None = None, binary: bool = False):
    base, key = config()
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = Request(
        base + path,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=120) as response:
            raw = response.read()
            return raw if binary else json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"reddit-ops HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"reddit-ops network error: {exc.reason}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the persistent Reddit browser")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("open-login")
    nav = commands.add_parser("navigate")
    nav.add_argument("url")
    snap = commands.add_parser("snapshot")
    snap.add_argument("--text-limit", type=int, default=30000)
    snap.add_argument("--link-limit", type=int, default=200)
    shot = commands.add_parser("screenshot")
    shot.add_argument("--full-page", action="store_true")
    shot.add_argument("--output-dir", default="/workspace/codex-artifacts")
    args = parser.parse_args()

    if args.command == "status":
        result = request("GET", "/api/status")
    elif args.command == "open-login":
        result = request("POST", "/api/browser/open-login", {})
    elif args.command == "navigate":
        result = request("POST", "/api/browser/navigate", {"url": args.url})
    elif args.command == "snapshot":
        query = urlencode({"textLimit": args.text_limit, "linkLimit": args.link_limit})
        result = request("GET", f"/api/page/snapshot?{query}")
    else:
        meta = request("POST", "/api/page/screenshot", {"fullPage": args.full_page})
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / meta["name"]
        target.write_bytes(request("GET", f"/api/captures/{quote(meta['name'])}", binary=True))
        result = {**meta, "artifact": str(target)}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
