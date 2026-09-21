#!/usr/bin/env python3
"""Publish one Reel to Instagram and a Facebook Page with idempotent operation state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from instagram import publish_instagram_reel, reel_payload, require_instagram_caption
from media_stage import preflight, resolve_public_media
from meta_graph import (
    GraphClient, MetaGraphError, normalize_post_text, print_json, require_object_id, require_publish_confirmation,
)
from pages import publish_page_reel


DEFAULT_OPERATION_DIR = Path(os.environ.get(
    "META_BUSINESS_OPERATION_DIR", str(Path.home() / ".codex" / "state" / "meta-business" / "operations")
)).expanduser()


def _operation_id(video: str, cover: str | None, instagram_caption: str, page_caption: str, page_id: str, ig_user_id: str) -> str:
    parts: list[str] = []
    for value in (video, cover or ""):
        path = Path(value).expanduser()
        if value and path.is_file():
            parts.append(f"{path.resolve()}:{path.stat().st_size}:{path.stat().st_mtime_ns}")
        else:
            parts.append(value)
    parts.extend([instagram_caption, page_caption, page_id, ig_user_id])
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:24]


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _load_operation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaGraphError(f"could not read operation file: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise MetaGraphError("unsupported cross-post operation file")
    source = value.get("input") or {}
    if "caption" in source:
        source.setdefault("instagram_caption", source["caption"])
        source.setdefault("page_caption", source["caption"])
    source["instagram_caption"] = require_instagram_caption(source.get("instagram_caption")) or ""
    source["page_caption"] = normalize_post_text(source.get("page_caption")) or ""
    return value


def _platforms(value: str) -> list[str]:
    return ["instagram", "page"] if value == "both" else [value]


def _page_args(video_url: str, cover: str | None, caption: str, title: str | None, interval: int, timeout: int) -> argparse.Namespace:
    cover_is_url = bool(cover and cover.startswith("https://"))
    return argparse.Namespace(
        confirm_publish=True, dry_run=False, url=video_url, file=None,
        cover_file=None if cover_is_url else cover, cover_url=cover if cover_is_url else None,
        description=caption, title=title, interval=interval, timeout=timeout,
    )


def _build_new_operation(args: argparse.Namespace, page_id: str, ig_user_id: str) -> tuple[Path, dict[str, Any]]:
    instagram_caption = require_instagram_caption(
        args.instagram_caption if args.instagram_caption is not None else (args.caption or "")
    ) or ""
    page_caption = normalize_post_text(
        args.page_caption if args.page_caption is not None else (args.caption or "")
    ) or ""
    operation_id = _operation_id(args.video, args.cover, instagram_caption, page_caption, page_id, ig_user_id)
    path = Path(args.operation_file).expanduser().resolve() if args.operation_file else DEFAULT_OPERATION_DIR / f"reel-{operation_id}.json"
    requested = _platforms(args.platform)
    value = {
        "schema_version": 1, "operation_id": operation_id, "action": "meta_reel_crosspost",
        "input": {"video": str(Path(args.video).expanduser().resolve()) if Path(args.video).expanduser().is_file() else args.video,
                  "cover": str(Path(args.cover).expanduser().resolve()) if args.cover and Path(args.cover).expanduser().is_file() else args.cover,
                  "instagram_caption": instagram_caption, "page_caption": page_caption,
                  "title": args.title, "share_to_feed": args.share_to_feed},
        "targets": {"page_id": page_id, "ig_user_id": ig_user_id},
        "platforms": {name: {"status": "pending"} for name in requested},
    }
    return path, value


def _preflight_plan(operation: dict[str, Any], operation_path: Path) -> dict[str, Any]:
    source = operation["input"]
    result = {
        "dry_run": True, "action": operation["action"], "operation_id": operation["operation_id"],
        "operation_file": str(operation_path), "targets": operation["targets"],
        "platforms": list(operation["platforms"]),
        "instagram_caption": source.get("instagram_caption", source.get("caption", "")),
        "page_caption": source.get("page_caption", source.get("caption", "")),
        "video_preflight": preflight(source["video"], "video"),
    }
    if source.get("cover"):
        result["cover_preflight"] = preflight(source["cover"], "cover")
    return result


def run_operation(operation: dict[str, Any], path: Path, retry_unknown: bool, interval: int, timeout: int) -> dict[str, Any]:
    source = operation["input"]
    targets = operation["targets"]
    statuses = operation["platforms"]
    blocked = [name for name, state in statuses.items() if state.get("status") in {"running", "unknown"}]
    if blocked and not retry_unknown:
        raise MetaGraphError(
            "operation has an ambiguous platform state (" + ", ".join(blocked) +
            "); inspect stored IDs/results first, then pass --retry-unknown only if duplicate risk is acceptable"
        )
    video_url = operation.get("staging", {}).get("video_url")
    if not video_url:
        video_url, video_stage = resolve_public_media(source["video"], "video", True)
        operation.setdefault("staging", {})["video_url"] = video_url
        operation["staging"]["video"] = video_stage
        _atomic_write(path, operation)
    cover_url = operation.get("staging", {}).get("cover_url")
    if source.get("cover") and not cover_url:
        cover_url, cover_stage = resolve_public_media(source["cover"], "cover", True)
        operation.setdefault("staging", {})["cover_url"] = cover_url
        operation["staging"]["cover"] = cover_stage
        _atomic_write(path, operation)
    client = GraphClient()
    for name, state in statuses.items():
        if state.get("status") == "completed":
            continue
        if state.get("status") in {"running", "unknown"} and not retry_unknown:
            continue
        state.clear()
        state["status"] = "running"
        _atomic_write(path, operation)
        try:
            if name == "instagram":
                payload = reel_payload(
                    str(video_url), source.get("instagram_caption", source.get("caption")), bool(source.get("share_to_feed"))
                )
                if cover_url:
                    payload["cover_url"] = cover_url
                result = publish_instagram_reel(client, targets["ig_user_id"], payload, interval, timeout)
            else:
                page_client = client.page_client(targets["page_id"])
                page_cover = source.get("cover") or cover_url
                result = publish_page_reel(
                    page_client, targets["page_id"],
                    _page_args(
                        str(video_url), page_cover, source.get("page_caption", source.get("caption")) or "",
                        source.get("title"), interval, timeout,
                    ),
                )
            state.update({"status": "completed", "result": result})
        except MetaGraphError as exc:
            state.update({"status": "unknown", "error": str(exc)})
        _atomic_write(path, operation)
    overall = "completed" if all(item.get("status") == "completed" for item in statuses.values()) else "partial_or_unknown"
    operation["status"] = overall
    _atomic_write(path, operation)
    return operation


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely cross-post one Reel to Instagram and Facebook Page")
    parser.add_argument("--video", help="local video file or public HTTPS URL")
    parser.add_argument("--cover", help="local JPEG/PNG or public HTTPS URL")
    parser.add_argument("--caption")
    parser.add_argument("--instagram-caption", help="Instagram copy; URLs are rejected")
    parser.add_argument("--page-caption", help="Facebook Page copy; may contain a clickable URL")
    parser.add_argument("--title")
    parser.add_argument("--platform", choices=["both", "instagram", "page"], default="both")
    parser.add_argument("--share-to-feed", action="store_true")
    parser.add_argument("--page-id")
    parser.add_argument("--ig-user-id")
    parser.add_argument("--operation-file")
    parser.add_argument("--resume", help="resume an existing operation JSON file")
    parser.add_argument("--retry-unknown", action="store_true", help="explicitly accept duplicate risk for an ambiguous platform")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--confirm-publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        require_publish_confirmation(args.confirm_publish, args.dry_run)
        if args.interval <= 0 or args.timeout <= 0:
            raise MetaGraphError("--interval and --timeout must be positive")
        if args.resume:
            path = Path(args.resume).expanduser().resolve()
            operation = _load_operation(path)
        else:
            if not args.video:
                raise MetaGraphError("--video is required unless --resume is used")
            page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
            ig_user_id = require_object_id(args.ig_user_id, "META_BUSINESS_IG_USER_ID", "Instagram user ID")
            path, operation = _build_new_operation(args, page_id, ig_user_id)
            if path.exists():
                existing = _load_operation(path)
                if existing.get("operation_id") != operation["operation_id"]:
                    raise MetaGraphError(f"operation file already exists for different content: {path}")
                operation = existing
        if args.dry_run:
            print_json(_preflight_plan(operation, path))
            return
        if path.exists() and operation.get("status") == "completed":
            print_json({"idempotent": True, "message": "operation already completed; no duplicate publish was attempted", **operation})
            return
        _atomic_write(path, operation)
        result = run_operation(operation, path, args.retry_unknown, args.interval, args.timeout)
        print_json(result)
        if result.get("status") != "completed":
            parser.exit(2)
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
