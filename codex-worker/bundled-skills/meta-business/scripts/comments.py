#!/usr/bin/env python3
"""Read and moderate Facebook Page and Instagram comments."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id, require_write_confirmation


PAGE_FIELDS = "id,message,from{id,name},created_time,like_count,comment_count,can_hide,is_hidden,permalink_url"
IG_FIELDS = "id,text,timestamp,username,like_count,hidden,replies{id,text,timestamp,username,like_count}"


def add_write_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def checked_limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise MetaGraphError("--limit must be between 1 and 100")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Read and moderate Meta comments")
    parser.add_argument("--page-id")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list comments on a Page post or Instagram media")
    listing.add_argument("platform", choices=("page", "instagram"))
    listing.add_argument("object_id")
    listing.add_argument("--limit", type=int, default=50)
    replies = commands.add_parser("replies", help="list replies to a comment")
    replies.add_argument("platform", choices=("page", "instagram"))
    replies.add_argument("comment_id")
    replies.add_argument("--limit", type=int, default=50)
    reply = commands.add_parser("reply", help="reply to a comment")
    reply.add_argument("platform", choices=("page", "instagram"))
    reply.add_argument("comment_id")
    reply.add_argument("--message", required=True)
    add_write_flags(reply)
    hide = commands.add_parser("hide", help="hide or unhide a comment")
    hide.add_argument("platform", choices=("page", "instagram"))
    hide.add_argument("comment_id")
    hide.add_argument("--hidden", choices=("true", "false"), required=True)
    add_write_flags(hide)
    delete = commands.add_parser("delete", help="delete a comment")
    delete.add_argument("platform", choices=("page", "instagram"))
    delete.add_argument("comment_id")
    add_write_flags(delete)
    reaction = commands.add_parser("reaction", help="like or unlike a Page comment")
    reaction.add_argument("comment_id")
    reaction.add_argument("action", choices=("like", "unlike"))
    add_write_flags(reaction)
    args = parser.parse_args()

    try:
        dry_run = getattr(args, "dry_run", False)
        client = None
        if not dry_run:
            root_client = GraphClient()
            platform = getattr(args, "platform", "page")
            if platform == "page":
                page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
                client = root_client.page_client(page_id)
            else:
                client = root_client
        if args.command in {"list", "replies"}:
            assert client is not None
            source = args.object_id if args.command == "list" else args.comment_id
            object_id = require_object_id(source, "META_UNUSED", "object ID")
            fields = PAGE_FIELDS if args.platform == "page" else IG_FIELDS
            edge = "comments" if args.command == "list" else "replies"
            print_json(client.get(f"{object_id}/{edge}", {"fields": fields, "limit": checked_limit(args.limit)}))
            return
        require_write_confirmation(args.confirm_write, args.dry_run, "comment change")
        comment_id = require_object_id(args.comment_id, "META_UNUSED", "comment ID")
        if args.command == "reply":
            method, endpoint, payload = "POST", f"{comment_id}/replies", {"message": args.message}
        elif args.command == "hide":
            key = "is_hidden" if args.platform == "page" else "hide"
            method, endpoint, payload = "POST", comment_id, {key: args.hidden}
        elif args.command == "delete":
            method, endpoint, payload = "DELETE", comment_id, {}
        else:
            method = "POST" if args.action == "like" else "DELETE"
            endpoint, payload = f"{comment_id}/likes", {}
        action = f"{args.command}_{getattr(args, 'platform', 'page')}"
        if args.dry_run:
            print_json({"dry_run": True, "action": action, "method": method, "endpoint": endpoint, "payload": payload})
        else:
            assert client is not None
            result = client.post(endpoint, payload) if method == "POST" else client.delete(endpoint, payload)
            print_json({"changed": True, "action": action, "result": result})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
