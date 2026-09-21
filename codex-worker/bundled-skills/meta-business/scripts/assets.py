#!/usr/bin/env python3
"""Inspect the Meta system-user identity and assigned business assets."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id


PAGE_FIELDS = "id,name,username,link,instagram_business_account{id,username,name,profile_picture_url}"
IG_FIELDS = "id,username,name,profile_picture_url,followers_count,media_count"


def verify(client: GraphClient, page_id: str | None, ig_user_id: str | None) -> dict:
    identity = client.get("me", {"fields": "id,name"})
    result = {"identity": identity, "graph_api_version": client.version}
    if page_id:
        result["page"] = client.get(page_id, {"fields": PAGE_FIELDS})
    if ig_user_id:
        result["instagram"] = client.get(ig_user_id, {"fields": IG_FIELDS})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect authorized Meta business assets")
    parser.add_argument("--page-id")
    parser.add_argument("--ig-user-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify", help="verify identity and configured assets")
    commands.add_parser("permissions", help="list token permissions")
    pages = commands.add_parser("list-pages", help="list visible Pages and linked Instagram accounts")
    pages.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    try:
        client = GraphClient()
        if args.command == "verify":
            page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
            ig_user_id = require_object_id(args.ig_user_id, "META_BUSINESS_IG_USER_ID", "Instagram user ID")
            print_json(verify(client, page_id, ig_user_id))
        elif args.command == "permissions":
            print_json(client.get("me/permissions"))
        else:
            if not 1 <= args.limit <= 100:
                parser.error("--limit must be between 1 and 100")
            print_json({"data": client.get_pages("me/accounts", {"fields": PAGE_FIELDS, "limit": args.limit})})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
