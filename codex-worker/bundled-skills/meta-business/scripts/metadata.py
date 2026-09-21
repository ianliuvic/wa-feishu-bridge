#!/usr/bin/env python3
"""Inspect and manage Facebook Page webhook app subscriptions."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id, require_write_confirmation


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Page app subscriptions")
    parser.add_argument("--page-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("subscriptions", help="list apps subscribed to the Page")
    subscribe = commands.add_parser("subscribe", help="subscribe the current app to Page webhook fields")
    subscribe.add_argument("--fields", required=True, help="comma-separated webhook fields")
    subscribe.add_argument("--confirm-write", action="store_true")
    subscribe.add_argument("--dry-run", action="store_true")
    unsubscribe = commands.add_parser("unsubscribe", help="unsubscribe the current app from the Page")
    unsubscribe.add_argument("--confirm-write", action="store_true")
    unsubscribe.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
        dry_run = getattr(args, "dry_run", False)
        client = None if dry_run else GraphClient().page_client(page_id)
        endpoint = f"{page_id}/subscribed_apps"
        if args.command == "subscriptions":
            assert client is not None
            print_json(client.get(endpoint, {"fields": "id,name,subscribed_fields"}))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "Page subscription change")
            method = "POST" if args.command == "subscribe" else "DELETE"
            payload = {"subscribed_fields": args.fields} if method == "POST" else {}
            if args.dry_run:
                print_json({"dry_run": True, "action": args.command, "method": method, "endpoint": endpoint, "payload": payload})
            else:
                assert client is not None
                result = client.post(endpoint, payload) if method == "POST" else client.delete(endpoint)
                print_json({"changed": True, "action": args.command, "result": result})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
