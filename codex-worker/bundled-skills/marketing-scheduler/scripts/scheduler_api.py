#!/usr/bin/env python3
"""Small CLI for the authenticated marketing scheduler API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def request(method: str, path: str, payload: dict | None = None) -> object:
    base = os.getenv("MARKETING_SCHEDULER_URL", "").rstrip("/")
    token = os.getenv("MARKETING_SCHEDULER_TOKEN", "")
    if not base or not token:
        die("MARKETING_SCHEDULER_URL or MARKETING_SCHEDULER_TOKEN is not configured")
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        die(f"scheduler API error {exc.code}: {exc.read().decode(errors='replace')[:1000]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage durable marketing jobs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")

    create = sub.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--cron", required=True)
    create.add_argument("--timezone", required=True)
    create.add_argument("--prompt", required=True)
    create.add_argument("--chat-id")

    for action in ("pause", "resume", "run", "delete"):
        command = sub.add_parser(action)
        command.add_argument("--id", required=True)

    args = parser.parse_args()
    if args.command == "list":
        result = request("GET", "/api/scheduler/tasks")
    elif args.command == "create":
        result = request(
            "POST",
            "/api/scheduler/tasks",
            {
                "name": args.name,
                "cron": args.cron,
                "timezone": args.timezone,
                "prompt": args.prompt,
                "chat_id": args.chat_id,
            },
        )
    elif args.command == "delete":
        result = request("DELETE", f"/api/scheduler/tasks/{args.id}")
    else:
        result = request("POST", f"/api/scheduler/tasks/{args.id}/{args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
