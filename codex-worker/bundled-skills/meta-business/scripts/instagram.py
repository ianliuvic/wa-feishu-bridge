#!/usr/bin/env python3
"""Read and publish Instagram professional-account media through Meta Graph API."""

import argparse
import re
import time

from meta_graph import (
    GraphClient,
    MetaGraphError,
    dry_run_result,
    load_json_object,
    normalize_post_text,
    print_json,
    require_allowed_fields,
    require_object_id,
    require_public_https_url,
    require_publish_confirmation,
)
from media_stage import preflight_local, preflight_url, resolve_public_media


ACCOUNT_FIELDS = "id,username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,username,children{id,media_type,media_url,permalink}"
STATUS_FIELDS = "id,status_code,status"
TERMINAL_SUCCESS = {"FINISHED", "PUBLISHED"}
TERMINAL_FAILURE = {"ERROR", "EXPIRED"}
PUBLISH_OPTION_FIELDS = {
    "user_tags", "location_id", "product_tags", "is_branded_content",
    "branded_content_sponsor_page_id", "thumb_offset", "cover_url", "copyright",
}
URL_PATTERN = re.compile(r"(?i)(?:https?://|www\.)\S+")


def require_instagram_caption(value: str | None) -> str | None:
    value = normalize_post_text(value)
    if value and URL_PATTERN.search(value):
        raise MetaGraphError('Instagram captions must not contain URLs; use wording such as "Link in bio" instead')
    return value


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def wait_for_container(client: GraphClient, creation_id: str, interval: int, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while True:
        last = client.get(creation_id, {"fields": STATUS_FIELDS})
        state = str(last.get("status_code") or "").upper()
        if state in TERMINAL_SUCCESS:
            return last
        if state in TERMINAL_FAILURE:
            raise MetaGraphError(f"Instagram container {creation_id} failed: {last.get('status') or state}")
        if time.monotonic() >= deadline:
            raise MetaGraphError(f"timed out waiting for Instagram container {creation_id}; last state: {state or 'unknown'}")
        time.sleep(interval)


def publish_creation(client: GraphClient, ig_user_id: str, creation_id: str) -> dict:
    published = client.post(f"{ig_user_id}/media_publish", {"creation_id": creation_id})
    media_id = published.get("id")
    result = {"published": True, "ig_user_id": ig_user_id, "creation_id": creation_id, "created": published}
    if media_id:
        result["media"] = client.get(media_id, {"fields": MEDIA_FIELDS})
    return result


def publish_instagram_reel(
    client: GraphClient, ig_user_id: str, payload: dict, interval: int, timeout: int
) -> dict:
    created = client.post(f"{ig_user_id}/media", payload)
    creation_id = require_object_id(created.get("id"), "META_UNUSED", "creation ID")
    container = wait_for_container(client, creation_id, interval, timeout)
    result = publish_creation(client, ig_user_id, creation_id)
    result["container"] = container
    media = result.get("media") or {}
    result["verification"] = {
        "media_id": media.get("id"), "media_product_type": media.get("media_product_type"),
        "media_type": media.get("media_type"), "permalink": media.get("permalink"),
        "thumbnail_url": media.get("thumbnail_url"),
    }
    return result


def image_payload(image_url: str, caption: str | None = None, carousel_item: bool = False) -> dict:
    payload = {"image_url": require_public_https_url(image_url, "--image-url")}
    if caption:
        payload["caption"] = require_instagram_caption(caption)
    if carousel_item:
        payload["is_carousel_item"] = "true"
    return payload


def reel_payload(video_url: str, caption: str | None, share_to_feed: bool) -> dict:
    payload = {
        "media_type": "REELS",
        "video_url": require_public_https_url(video_url, "--video-url"),
        "share_to_feed": "true" if share_to_feed else "false",
    }
    if caption:
        payload["caption"] = require_instagram_caption(caption)
    return payload


def dry_run_carousel(ig_user_id: str, urls: list[str], caption: str | None) -> dict:
    children = [image_payload(url, carousel_item=True) for url in urls]
    parent = {"media_type": "CAROUSEL", "children": "<creation IDs from child containers>"}
    if caption:
        parent["caption"] = require_instagram_caption(caption)
    return {
        "dry_run": True,
        "action": "instagram_carousel",
        "target_id": ig_user_id,
        "child_endpoint": f"{ig_user_id}/media",
        "children": children,
        "parent_endpoint": f"{ig_user_id}/media",
        "parent_payload": parent,
        "publish_endpoint": f"{ig_user_id}/media_publish",
    }


def add_publish_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument("--confirm-publish", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--interval", type=positive_int, default=5)
    command.add_argument("--timeout", type=positive_int, default=600)
    command.add_argument("--options-file", help="JSON object with tags, location, shopping, or branded-content fields")


def apply_publish_options(payload: dict, path: str | None) -> dict:
    if not path:
        return payload
    options = require_allowed_fields(load_json_object(path), PUBLISH_OPTION_FIELDS, "Instagram publish option")
    conflicts = set(payload) & set(options)
    if conflicts:
        raise MetaGraphError(f"publish options cannot replace base fields: {', '.join(sorted(conflicts))}")
    payload.update(options)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Instagram professional-account content")
    parser.add_argument("--ig-user-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="read Instagram account information")
    listing = commands.add_parser("list", help="list recent Instagram media")
    listing.add_argument("--limit", type=int, default=10)
    commands.add_parser("publishing-limit", help="read the content publishing limit")
    status = commands.add_parser("status", help="read an Instagram creation container")
    status.add_argument("creation_id")
    media = commands.add_parser("media", help="read a published Instagram media object")
    media.add_argument("media_id")
    image = commands.add_parser("publish-image", help="publish one Instagram image")
    image.add_argument("--image-url", required=True)
    image.add_argument("--caption")
    add_publish_flags(image)
    carousel = commands.add_parser("publish-carousel", help="publish an Instagram image carousel")
    carousel.add_argument("--image-url", action="append", required=True)
    carousel.add_argument("--caption")
    add_publish_flags(carousel)
    reel = commands.add_parser("publish-reel", help="publish an Instagram Reel")
    video_source = reel.add_mutually_exclusive_group(required=True)
    video_source.add_argument("--video-url")
    video_source.add_argument("--video-file")
    reel.add_argument("--caption")
    reel.add_argument("--share-to-feed", action="store_true")
    cover_source = reel.add_mutually_exclusive_group()
    cover_source.add_argument("--cover-url")
    cover_source.add_argument("--cover-file")
    add_publish_flags(reel)
    args = parser.parse_args()

    try:
        ig_user_id = require_object_id(args.ig_user_id, "META_BUSINESS_IG_USER_ID", "Instagram user ID")
        client = None if getattr(args, "dry_run", False) else GraphClient()
        if args.command == "info":
            assert client is not None
            print_json(client.get(ig_user_id, {"fields": ACCOUNT_FIELDS}))
        elif args.command == "list":
            assert client is not None
            if not 1 <= args.limit <= 100:
                parser.error("--limit must be between 1 and 100")
            print_json(client.get(f"{ig_user_id}/media", {"fields": MEDIA_FIELDS, "limit": args.limit}))
        elif args.command == "publishing-limit":
            assert client is not None
            print_json(client.get(f"{ig_user_id}/content_publishing_limit", {"fields": "config,quota_usage"}))
        elif args.command == "status":
            assert client is not None
            creation_id = require_object_id(args.creation_id, "META_UNUSED", "creation ID")
            print_json(client.get(creation_id, {"fields": STATUS_FIELDS}))
        elif args.command == "media":
            assert client is not None
            media_id = require_object_id(args.media_id, "META_UNUSED", "media ID")
            print_json(client.get(media_id, {"fields": MEDIA_FIELDS}))
        elif args.command == "publish-image":
            require_publish_confirmation(args.confirm_publish, args.dry_run)
            payload = apply_publish_options(image_payload(args.image_url, args.caption), args.options_file)
            if args.dry_run:
                print_json(dry_run_result("instagram_image", ig_user_id, f"{ig_user_id}/media", payload))
            else:
                assert client is not None
                created = client.post(f"{ig_user_id}/media", payload)
                creation_id = require_object_id(created.get("id"), "META_UNUSED", "creation ID")
                wait_for_container(client, creation_id, args.interval, args.timeout)
                print_json(publish_creation(client, ig_user_id, creation_id))
        elif args.command == "publish-carousel":
            require_publish_confirmation(args.confirm_publish, args.dry_run)
            if not 2 <= len(args.image_url) <= 10:
                raise MetaGraphError("Instagram carousel requires between 2 and 10 --image-url values")
            for url in args.image_url:
                require_public_https_url(url, "--image-url")
            if args.dry_run:
                result = dry_run_carousel(ig_user_id, args.image_url, args.caption)
                result["parent_payload"] = apply_publish_options(result["parent_payload"], args.options_file)
                print_json(result)
            else:
                assert client is not None
                child_ids = []
                for url in args.image_url:
                    child = client.post(f"{ig_user_id}/media", image_payload(url, carousel_item=True))
                    child_id = require_object_id(child.get("id"), "META_UNUSED", "child creation ID")
                    wait_for_container(client, child_id, args.interval, args.timeout)
                    child_ids.append(child_id)
                parent_payload = {"media_type": "CAROUSEL", "children": ",".join(child_ids)}
                if args.caption:
                    parent_payload["caption"] = require_instagram_caption(args.caption)
                parent_payload = apply_publish_options(parent_payload, args.options_file)
                parent = client.post(f"{ig_user_id}/media", parent_payload)
                parent_id = require_object_id(parent.get("id"), "META_UNUSED", "carousel creation ID")
                wait_for_container(client, parent_id, args.interval, args.timeout)
                result = publish_creation(client, ig_user_id, parent_id)
                result["child_creation_ids"] = child_ids
                print_json(result)
        else:
            require_publish_confirmation(args.confirm_publish, args.dry_run)
            live = not args.dry_run
            video_input = args.video_url or args.video_file
            video_url, video_info = resolve_public_media(video_input, "video", live)
            payload = apply_publish_options(
                reel_payload(video_url or "https://staged.example.invalid/video.mp4", args.caption, args.share_to_feed),
                args.options_file,
            )
            cover_url = args.cover_url
            cover_info = preflight_url(cover_url, "cover") if cover_url else None
            if args.cover_file:
                cover_url, cover_info = resolve_public_media(args.cover_file, "cover", live)
            if cover_url:
                if "cover_url" in payload:
                    raise MetaGraphError("set the Reel cover with either --cover-url or --options-file, not both")
                payload["cover_url"] = require_public_https_url(cover_url, "Reel cover URL")
            if args.dry_run:
                if args.video_file:
                    payload["video_url"] = "<public URL created during confirmed publishing>"
                if args.cover_file:
                    payload["cover_url"] = "<public URL created during confirmed publishing>"
                result = dry_run_result("instagram_reel", ig_user_id, f"{ig_user_id}/media", payload)
                result["video_preflight"] = video_info
                if cover_info:
                    result["cover_preflight"] = cover_info
                print_json(result)
            else:
                assert client is not None
                print_json(publish_instagram_reel(client, ig_user_id, payload, args.interval, args.timeout))
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
