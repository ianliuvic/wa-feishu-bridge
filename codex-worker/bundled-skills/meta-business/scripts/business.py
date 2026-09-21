#!/usr/bin/env python3
"""Inspect business portfolios and manage explicit asset assignments."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id, require_write_confirmation


EDGE_FIELDS = {
    "owned-pages": ("owned_pages", "id,name,username,link"),
    "client-pages": ("client_pages", "id,name,username,link"),
    "owned-ad-accounts": ("owned_ad_accounts", "id,account_id,name,account_status,currency,timezone_name"),
    "client-ad-accounts": ("client_ad_accounts", "id,account_id,name,account_status,currency,timezone_name"),
    "instagram-accounts": ("instagram_accounts", "id,username,name,profile_picture_url"),
    "catalogs": ("owned_product_catalogs", "id,name,vertical,product_count"),
    "pixels": ("owned_pixels", "id,name,last_fired_time"),
    "system-users": ("system_users", "id,name,role"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage authorized Meta business assets")
    parser.add_argument("--business-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("businesses", help="list businesses visible to the token")
    info = commands.add_parser("info", help="read the configured business")
    assets = commands.add_parser("assets", help="list one type of business asset")
    assets.add_argument("asset_type", choices=sorted(EDGE_FIELDS))
    assets.add_argument("--limit", type=int, default=100)
    assigned = commands.add_parser("assigned-users", help="list system users assigned to an asset")
    assigned.add_argument("asset_id")
    assign = commands.add_parser("assign-user", help="assign a system user and tasks to an asset")
    assign.add_argument("asset_id")
    assign.add_argument("--system-user-id", required=True)
    assign.add_argument("--tasks", required=True, help="comma-separated Meta task names")
    assign.add_argument("--confirm-write", action="store_true")
    assign.add_argument("--dry-run", action="store_true")
    remove = commands.add_parser("remove-user", help="remove a system user from an asset")
    remove.add_argument("asset_id")
    remove.add_argument("--system-user-id", required=True)
    remove.add_argument("--confirm-write", action="store_true")
    remove.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        dry_run = getattr(args, "dry_run", False)
        client = None if dry_run else GraphClient()
        if args.command == "businesses":
            assert client is not None
            print_json(client.get("me/businesses", {"fields": "id,name,verification_status,created_time"}))
            return
        business_id = None
        if args.command in {"info", "assets"}:
            business_id = require_object_id(args.business_id, "META_BUSINESS_BUSINESS_ID", "business ID")
        if args.command == "info":
            assert client is not None
            print_json(client.get(business_id, {"fields": "id,name,verification_status,created_time,primary_page{id,name}"}))
        elif args.command == "assets":
            assert client is not None
            if not 1 <= args.limit <= 100:
                raise MetaGraphError("--limit must be between 1 and 100")
            edge, fields = EDGE_FIELDS[args.asset_type]
            print_json(client.get(f"{business_id}/{edge}", {"fields": fields, "limit": args.limit}))
        elif args.command == "assigned-users":
            assert client is not None
            asset_id = require_object_id(args.asset_id, "META_UNUSED", "asset ID")
            print_json(client.get(f"{asset_id}/assigned_users", {"fields": "id,name,tasks"}))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "business asset assignment")
            asset_id = require_object_id(args.asset_id, "META_UNUSED", "asset ID")
            user_id = require_object_id(args.system_user_id, "META_UNUSED", "system user ID")
            endpoint = f"{asset_id}/assigned_users"
            if args.command == "assign-user":
                tasks = [item.strip() for item in args.tasks.split(",") if item.strip()]
                if not tasks:
                    raise MetaGraphError("--tasks must contain at least one task")
                method, payload = "POST", {"user": user_id, "tasks": tasks}
            else:
                method, payload = "DELETE", {"user": user_id}
            if args.dry_run:
                print_json({"dry_run": True, "action": args.command, "method": method, "endpoint": endpoint, "payload": payload})
            else:
                assert client is not None
                result = client.post(endpoint, payload) if method == "POST" else client.delete(endpoint, payload)
                print_json({"changed": True, "action": args.command, "result": result})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
