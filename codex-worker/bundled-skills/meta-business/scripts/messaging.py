#!/usr/bin/env python3
"""Read conversations and send authorized Page or Instagram messages."""

import argparse

from meta_graph import (
    GraphClient,
    MetaGraphError,
    print_json,
    require_object_id,
    require_public_https_url,
    require_write_confirmation,
)


CONVERSATION_FIELDS = "id,link,updated_time,unread_count,participants{id,name,email,username},messages.limit(20){id,message,from,to,created_time,attachments}"
MESSAGE_FIELDS = "id,message,from,to,created_time,attachments,shares"


def add_send_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recipient-id", required=True)
    parser.add_argument("--messaging-type", choices=("RESPONSE", "UPDATE", "MESSAGE_TAG"), default="RESPONSE")
    parser.add_argument("--tag")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Meta Page and Instagram messaging")
    parser.add_argument("--page-id")
    commands = parser.add_subparsers(dest="command", required=True)
    conversations = commands.add_parser("conversations", help="list Page or Instagram conversations")
    conversations.add_argument("--platform", choices=("messenger", "instagram"), default="messenger")
    conversations.add_argument("--limit", type=int, default=25)
    conversation = commands.add_parser("conversation", help="read a conversation")
    conversation.add_argument("conversation_id")
    messages = commands.add_parser("messages", help="list messages in a conversation")
    messages.add_argument("conversation_id")
    messages.add_argument("--limit", type=int, default=50)
    text = commands.add_parser("send-text", help="send a text message")
    add_send_flags(text)
    text.add_argument("--text", required=True)
    media = commands.add_parser("send-media", help="send a public media URL")
    add_send_flags(media)
    media.add_argument("--type", choices=("image", "video", "audio", "file"), required=True)
    media.add_argument("--url", required=True)
    args = parser.parse_args()

    try:
        page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
        dry_run = getattr(args, "dry_run", False)
        client = None if dry_run else GraphClient().page_client(page_id)
        if args.command == "conversations":
            assert client is not None
            if not 1 <= args.limit <= 100:
                raise MetaGraphError("--limit must be between 1 and 100")
            print_json(client.get(f"{page_id}/conversations", {"platform": args.platform, "fields": CONVERSATION_FIELDS, "limit": args.limit}))
        elif args.command in {"conversation", "messages"}:
            assert client is not None
            conversation_id = require_object_id(args.conversation_id, "META_UNUSED", "conversation ID")
            if args.command == "conversation":
                print_json(client.get(conversation_id, {"fields": CONVERSATION_FIELDS}))
            else:
                if not 1 <= args.limit <= 100:
                    raise MetaGraphError("--limit must be between 1 and 100")
                print_json(client.get(f"{conversation_id}/messages", {"fields": MESSAGE_FIELDS, "limit": args.limit}))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "message send")
            recipient_id = require_object_id(args.recipient_id, "META_UNUSED", "recipient-scoped ID")
            if args.messaging_type == "MESSAGE_TAG" and not args.tag:
                raise MetaGraphError("--tag is required with MESSAGE_TAG")
            payload = {
                "recipient": {"id": recipient_id},
                "messaging_type": args.messaging_type,
                "tag": args.tag,
            }
            if args.command == "send-text":
                payload["message"] = {"text": args.text}
            else:
                payload["message"] = {"attachment": {"type": args.type, "payload": {"url": require_public_https_url(args.url, "--url"), "is_reusable": True}}}
            payload = {key: value for key, value in payload.items() if value is not None}
            if args.dry_run:
                print_json({"dry_run": True, "action": args.command, "endpoint": f"{page_id}/messages", "payload": payload})
            else:
                assert client is not None
                print_json({"sent": True, "action": args.command, "result": client.post(f"{page_id}/messages", payload)})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
