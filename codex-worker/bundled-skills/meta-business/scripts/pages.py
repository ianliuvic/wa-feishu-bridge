#!/usr/bin/env python3
"""Read and publish Facebook Page content through Meta Graph API."""

import argparse
import mimetypes
import time
from pathlib import Path

from meta_graph import (
    GraphClient,
    MetaGraphError,
    dry_run_result,
    normalize_post_text,
    print_json,
    require_object_id,
    require_public_https_url,
    require_publish_confirmation,
    require_write_confirmation,
)
from media_stage import download_cover, resolve_public_media


PAGE_FIELDS = "id,name,username,link,fan_count,followers_count,instagram_business_account{id,username}"
POST_FIELDS = "id,message,created_time,updated_time,permalink_url,full_picture,status_type,is_published,scheduled_publish_time,attachments{media_type,url}"
VIDEO_STATUS_FIELDS = "id,status,permalink_url,description,title"
VIDEO_TERMINAL_SUCCESS = {"ready", "published"}
VIDEO_TERMINAL_FAILURE = {"error", "failed"}


def build_text_payload(message: str | None, link: str | None) -> dict:
    message = (normalize_post_text(message) or "").strip()
    if not message and not link:
        raise MetaGraphError("publish-text requires --message, --link, or both")
    payload = {}
    if message:
        payload["message"] = message
    if link:
        payload["link"] = require_public_https_url(link, "--link")
    return payload


def build_photo_payload(url: str, caption: str | None) -> dict:
    payload = {"url": require_public_https_url(url, "--url"), "published": "true"}
    if caption:
        payload["caption"] = normalize_post_text(caption)
    return payload


def build_video_payload(url: str, description: str | None, title: str | None) -> dict:
    payload = {"file_url": require_public_https_url(url, "--url"), "published": "true"}
    if description:
        payload["description"] = normalize_post_text(description)
    if title:
        payload["title"] = title
    return payload


def publish(
    client: GraphClient | None,
    page_id: str,
    action: str,
    edge: str,
    payload: dict,
    confirm: bool,
    dry_run: bool,
) -> dict:
    require_publish_confirmation(confirm, dry_run)
    endpoint = f"{page_id}/{edge}"
    if dry_run:
        return dry_run_result(action, page_id, endpoint, payload)
    if client is None:
        raise MetaGraphError("Graph client is unavailable for live publishing")
    created = client.post(endpoint, payload)
    result = {"published": True, "action": action, "page_id": page_id, "created": created}
    object_id = created.get("post_id") or created.get("id")
    if object_id:
        try:
            result["object"] = client.get(object_id, {"fields": POST_FIELDS})
        except MetaGraphError as exc:
            result["lookup_warning"] = str(exc)
    return result


def add_publish_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument("--confirm-publish", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--scheduled-publish-time", help="Unix timestamp or Meta-supported date string")
    command.add_argument("--unpublished", action="store_true", help="create an unpublished Page post")


def apply_publish_state(payload: dict, scheduled: str | None, unpublished: bool) -> dict:
    if scheduled and unpublished:
        raise MetaGraphError("use either --scheduled-publish-time or --unpublished, not both")
    if scheduled:
        payload.update({"published": "false", "scheduled_publish_time": scheduled})
    elif unpublished:
        payload["published"] = "false"
    return payload


def add_write_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument("--confirm-write", action="store_true")
    command.add_argument("--dry-run", action="store_true")


def cover_info(value: str) -> tuple[Path, str, int]:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise MetaGraphError(f"cover is not a readable file: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or ""
    if mime_type not in {"image/jpeg", "image/png"}:
        raise MetaGraphError("Page Reel cover must be a JPEG or PNG file")
    size = path.stat().st_size
    if size <= 0 or size > 30 * 1024 * 1024:
        raise MetaGraphError("Page Reel cover must be between 1 byte and 30 MB")
    return path, mime_type, size


def wait_for_video(client: GraphClient, video_id: str, interval: int, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = client.get(video_id, {"fields": VIDEO_STATUS_FIELDS})
        status = last.get("status") or {}
        state = str(status.get("video_status") or status.get("uploading_phase", {}).get("status") or "").lower()
        processing = str(status.get("processing_phase", {}).get("status") or "").lower()
        if state in VIDEO_TERMINAL_SUCCESS or processing == "complete":
            return last
        if state in VIDEO_TERMINAL_FAILURE or processing in VIDEO_TERMINAL_FAILURE:
            raise MetaGraphError(f"Page Reel processing failed: {status}")
        time.sleep(interval)
    raise MetaGraphError(f"timed out waiting for Page Reel {video_id}; last status: {last.get('status')}")


def publish_page_reel(client: GraphClient | None, page_id: str, args: argparse.Namespace) -> dict:
    require_publish_confirmation(args.confirm_publish, args.dry_run)
    video_input = args.url or args.file
    video_url, video_preflight = resolve_public_media(video_input, "video", not args.dry_run)
    effective_video_url = video_url or "https://staged.example.invalid/video.mp4"
    if args.cover_file and args.cover_url:
        raise MetaGraphError("set the Page Reel cover with either --cover-file or --cover-url, not both")
    cover_value = args.cover_file or args.cover_url
    cover_path = None
    cover_mime = None
    cover_size = None
    cover_temp = None
    if cover_value:
        cover_path, cover_temp = download_cover(cover_value)
        cover_path, cover_mime, cover_size = cover_info(str(cover_path))
    finish_payload = {
        "upload_phase": "finish",
        "video_id": "<video ID from start phase>",
        "video_state": "PUBLISHED",
        "description": normalize_post_text(args.description),
        "title": args.title,
    }
    finish_payload = {key: value for key, value in finish_payload.items() if value is not None}
    if args.dry_run:
        result = {
            "dry_run": True,
            "action": "page_reel",
            "start_endpoint": f"{page_id}/video_reels",
            "upload_url": video_url or "<public URL created during confirmed publishing>",
            "video_preflight": video_preflight,
            "finish_payload": finish_payload,
        }
        if cover_path:
            result["cover"] = {"path": str(cover_path), "mime_type": cover_mime, "size_bytes": cover_size}
        if cover_temp is not None:
            cover_temp.cleanup()
        return result
    assert client is not None
    try:
        started = client.post(f"{page_id}/video_reels", {"upload_phase": "start"})
        video_id = require_object_id(started.get("video_id"), "META_UNUSED", "Page Reel video ID")
        uploaded = client.upload_reel_from_url(video_id, effective_video_url)
        thumbnail = None
        if cover_path:
            thumbnail = client.upload_file(f"{video_id}/thumbnails", cover_path, "source", cover_mime or "image/jpeg", {"is_preferred": True})
        finish_payload["video_id"] = video_id
        finished = client.post(f"{page_id}/video_reels", finish_payload)
        processed = wait_for_video(client, video_id, args.interval, args.timeout)
        verified = client.get(video_id, {"fields": VIDEO_STATUS_FIELDS})
        return {
            "published": True,
            "action": "page_reel",
            "page_id": page_id,
            "video_id": video_id,
            "permalink": verified.get("permalink_url"),
            "upload": uploaded,
            "processing": processed,
            "verification": verified,
            "thumbnail": thumbnail,
            "cover_preferred": bool(isinstance(thumbnail, dict) and thumbnail.get("is_preferred")),
            "finish": finished,
        }
    finally:
        if cover_temp is not None:
            cover_temp.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Facebook Page content")
    parser.add_argument("--page-id")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="read Page information")
    listing = commands.add_parser("list", help="list recent published Page posts")
    listing.add_argument("--limit", type=int, default=10)
    scheduled = commands.add_parser("scheduled", help="list scheduled Page posts")
    scheduled.add_argument("--limit", type=int, default=25)
    visitor = commands.add_parser("visitor-posts", help="list Page and visitor feed posts")
    visitor.add_argument("--limit", type=int, default=25)
    post = commands.add_parser("post", help="read a post or video object")
    post.add_argument("object_id")
    text = commands.add_parser("publish-text", help="publish a Page text or link post")
    text.add_argument("--message")
    text.add_argument("--link")
    add_publish_flags(text)
    photo = commands.add_parser("publish-photo", help="publish a Page photo from a public URL")
    photo.add_argument("--url", required=True)
    photo.add_argument("--caption")
    add_publish_flags(photo)
    video = commands.add_parser("publish-video", help="publish a Page hosted video from a public URL")
    video.add_argument("--url", required=True)
    video.add_argument("--description")
    video.add_argument("--title")
    add_publish_flags(video)
    reel = commands.add_parser("publish-reel", help="publish a Facebook Page Reel from a public URL or local file")
    reel_video = reel.add_mutually_exclusive_group(required=True)
    reel_video.add_argument("--url")
    reel_video.add_argument("--file")
    reel.add_argument("--description")
    reel.add_argument("--title")
    reel.add_argument("--cover-file")
    reel.add_argument("--cover-url")
    reel.add_argument("--interval", type=int, default=5)
    reel.add_argument("--timeout", type=int, default=600)
    reel.add_argument("--confirm-publish", action="store_true")
    reel.add_argument("--dry-run", action="store_true")
    update = commands.add_parser("update-post", help="update a Page post message")
    update.add_argument("object_id")
    update.add_argument("--message", required=True)
    add_write_flags(update)
    delete = commands.add_parser("delete-post", help="delete a Page post")
    delete.add_argument("object_id")
    add_write_flags(delete)
    args = parser.parse_args()

    try:
        page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
        client = None if getattr(args, "dry_run", False) else GraphClient().page_client(page_id)
        if args.command == "info":
            assert client is not None
            print_json(client.get(page_id, {"fields": PAGE_FIELDS}))
        elif args.command in {"list", "scheduled", "visitor-posts"}:
            assert client is not None
            if not 1 <= args.limit <= 100:
                parser.error("--limit must be between 1 and 100")
            edge = {"list": "published_posts", "scheduled": "scheduled_posts", "visitor-posts": "feed"}[args.command]
            print_json(client.get(f"{page_id}/{edge}", {"fields": POST_FIELDS, "limit": args.limit}))
        elif args.command == "post":
            assert client is not None
            object_id = require_object_id(args.object_id, "META_UNUSED", "object ID")
            print_json(client.get(object_id, {"fields": POST_FIELDS}))
        elif args.command == "publish-text":
            payload = apply_publish_state(build_text_payload(args.message, args.link), args.scheduled_publish_time, args.unpublished)
            print_json(publish(client, page_id, "page_text", "feed", payload, args.confirm_publish, args.dry_run))
        elif args.command == "publish-photo":
            payload = apply_publish_state(build_photo_payload(args.url, args.caption), args.scheduled_publish_time, args.unpublished)
            print_json(publish(client, page_id, "page_photo", "photos", payload, args.confirm_publish, args.dry_run))
        elif args.command == "publish-video":
            payload = apply_publish_state(build_video_payload(args.url, args.description, args.title), args.scheduled_publish_time, args.unpublished)
            print_json(publish(client, page_id, "page_video", "videos", payload, args.confirm_publish, args.dry_run))
        elif args.command == "publish-reel":
            if args.interval <= 0 or args.timeout <= 0:
                raise MetaGraphError("--interval and --timeout must be positive")
            print_json(publish_page_reel(client, page_id, args))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "Page post change")
            object_id = require_object_id(args.object_id, "META_UNUSED", "post ID")
            method = "POST" if args.command == "update-post" else "DELETE"
            payload = {"message": normalize_post_text(args.message)} if method == "POST" else {}
            if args.dry_run:
                print_json({"dry_run": True, "action": args.command, "method": method, "endpoint": object_id, "payload": payload})
            else:
                assert client is not None
                result = client.post(object_id, payload) if method == "POST" else client.delete(object_id)
                print_json({"changed": True, "action": args.command, "result": result})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
