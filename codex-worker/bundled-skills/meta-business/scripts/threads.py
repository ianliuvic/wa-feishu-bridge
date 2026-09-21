#!/usr/bin/env python3
"""Read Threads profiles and posts when the token is valid for Threads API."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id


PROFILE_FIELDS = "id,username,name,threads_profile_picture_url,threads_biography"
THREAD_FIELDS = "id,media_product_type,media_type,media_url,permalink,owner,username,text,timestamp,shortcode,thumbnail_url,children{id,media_type,media_url}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Read Threads business content")
    parser.add_argument("--threads-user-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profile", help="read a Threads profile")
    listing = commands.add_parser("list", help="list recent Threads posts")
    listing.add_argument("--limit", type=int, default=25)
    thread = commands.add_parser("thread", help="read one Threads post")
    thread.add_argument("thread_id")
    args = parser.parse_args()

    try:
        client = GraphClient()
        if args.command == "thread":
            thread_id = require_object_id(args.thread_id, "META_UNUSED", "Threads post ID")
            print_json(client.get(thread_id, {"fields": THREAD_FIELDS}))
            return
        user_id = require_object_id(args.threads_user_id, "META_THREADS_USER_ID", "Threads user ID")
        if args.command == "profile":
            print_json(client.get(user_id, {"fields": PROFILE_FIELDS}))
        else:
            if not 1 <= args.limit <= 100:
                raise MetaGraphError("--limit must be between 1 and 100")
            print_json(client.get(f"{user_id}/threads", {"fields": THREAD_FIELDS, "limit": args.limit}))
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
