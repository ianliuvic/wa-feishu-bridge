#!/usr/bin/env python3
"""Inspect and manage authorized Meta ad accounts and Marketing API objects."""

import argparse
import mimetypes
from pathlib import Path

from meta_graph import (
    GraphClient,
    MetaGraphError,
    load_json_object,
    print_json,
    require_allowed_fields,
    require_object_id,
    require_public_https_url,
    require_write_confirmation,
)


ACCOUNT_FIELDS = "id,account_id,name,account_status,currency,timezone_name,amount_spent,balance,business{id,name}"
CAMPAIGN_FIELDS = "id,name,objective,status,effective_status,buying_type,daily_budget,lifetime_budget,budget_remaining,created_time,updated_time"
ADSET_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,bid_strategy,billing_event,optimization_goal,start_time,end_time,targeting,created_time,updated_time"
AD_FIELDS = "id,name,adset_id,campaign_id,status,effective_status,creative{id,name},created_time,updated_time"
CREATIVE_FIELDS = "id,name,title,body,status,object_story_id,object_story_spec,asset_feed_spec,thumbnail_url,image_url,video_id"
AUDIENCE_FIELDS = "id,name,subtype,description,approximate_count_lower_bound,approximate_count_upper_bound,delivery_status,operation_status,time_created,time_updated"
DEFAULT_INSIGHT_FIELDS = "account_id,account_name,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,reach,frequency,clicks,inline_link_clicks,ctr,cpc,cpm,actions,cost_per_action_type"
IMAGE_FIELDS = "hash,name,url,url_128,width,height,status,created_time,updated_time,original_width,original_height"
VIDEO_FIELDS = "id,title,description,created_time,updated_time,status,picture,permalink_url,length"
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mpeg", ".mpg", ".webm"}
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024

CAMPAIGN_WRITE_FIELDS = {
    "name", "objective", "status", "special_ad_categories", "buying_type",
    "daily_budget", "lifetime_budget", "bid_strategy", "spend_cap",
    "promoted_object", "is_skadnetwork_attribution",
}
ADSET_WRITE_FIELDS = {
    "name", "campaign_id", "status", "daily_budget", "lifetime_budget",
    "start_time", "end_time", "billing_event", "optimization_goal", "bid_amount",
    "bid_strategy", "targeting", "promoted_object", "attribution_spec",
    "destination_type", "is_dynamic_creative", "frequency_control_specs",
}
AD_WRITE_FIELDS = {"name", "adset_id", "creative", "status", "tracking_specs", "conversion_domain"}
CREATIVE_WRITE_FIELDS = {
    "name", "object_story_spec", "asset_feed_spec", "degrees_of_freedom_spec",
    "url_tags", "authorization_category", "categorization_criteria",
}
AUDIENCE_WRITE_FIELDS = {
    "name", "subtype", "description", "customer_file_source", "retention_days",
    "rule", "prefill", "lookalike_spec", "origin_audience_id", "opt_out_link",
}
WRITE_FIELDS = {
    "campaign": CAMPAIGN_WRITE_FIELDS,
    "adset": ADSET_WRITE_FIELDS,
    "ad": AD_WRITE_FIELDS,
    "creative": CREATIVE_WRITE_FIELDS,
    "audience": AUDIENCE_WRITE_FIELDS,
}
EDGES = {"campaign": "campaigns", "adset": "adsets", "ad": "ads", "creative": "adcreatives", "audience": "customaudiences"}
READ_FIELDS = {"campaign": CAMPAIGN_FIELDS, "adset": ADSET_FIELDS, "ad": AD_FIELDS, "creative": CREATIVE_FIELDS, "audience": AUDIENCE_FIELDS}


def add_limit(parser: argparse.ArgumentParser, default: int = 25) -> None:
    parser.add_argument("--limit", type=int, default=default)


def add_write_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def account_id(args: argparse.Namespace) -> str:
    value = require_object_id(args.ad_account_id, "META_BUSINESS_AD_ACCOUNT_ID", "ad account ID")
    return value if value.startswith("act_") else f"act_{value}"


def checked_limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise MetaGraphError("--limit must be between 1 and 100")
    return value


def media_file_info(value: str, kind: str) -> tuple[Path, str, int]:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise MetaGraphError(f"--file is not a readable file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise MetaGraphError("--file must not be empty")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if kind == "image":
        if mime_type not in IMAGE_MIME_TYPES:
            raise MetaGraphError("ad image upload supports JPEG or PNG files")
        if size > MAX_IMAGE_BYTES:
            raise MetaGraphError("ad image file exceeds the 30 MB safety limit")
    else:
        if path.suffix.lower() not in VIDEO_EXTENSIONS or not mime_type.startswith("video/"):
            raise MetaGraphError("ad video upload requires a supported video file extension")
        if size > MAX_VIDEO_BYTES:
            raise MetaGraphError("ad video file exceeds the 4 GB safety limit")
    return path, mime_type, size


def upload_preview(action: str, endpoint: str, path: Path | None, mime_type: str | None, size: int | None, payload: dict) -> dict:
    result = {"dry_run": True, "action": action, "endpoint": endpoint, "payload": payload}
    if path is not None:
        result["file"] = {"path": str(path), "name": path.name, "mime_type": mime_type, "size_bytes": size}
    return result


def write_object(args: argparse.Namespace, client: GraphClient | None, action: str) -> dict:
    require_write_confirmation(args.confirm_write, args.dry_run, "ad object change")
    payload = require_allowed_fields(load_json_object(args.spec_file), WRITE_FIELDS[args.object_type], args.object_type)
    if action == "create":
        endpoint = f"{account_id(args)}/{EDGES[args.object_type]}"
    else:
        endpoint = require_object_id(args.object_id, "META_UNUSED", f"{args.object_type} ID")
    if args.dry_run:
        return {"dry_run": True, "action": f"{action}_{args.object_type}", "endpoint": endpoint, "payload": payload}
    assert client is not None
    return {"changed": True, "action": f"{action}_{args.object_type}", "result": client.post(endpoint, payload)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Meta advertising objects")
    parser.add_argument("--ad-account-id")
    commands = parser.add_subparsers(dest="command", required=True)
    add_limit(commands.add_parser("accounts", help="list visible ad accounts"), 100)
    commands.add_parser("account", help="read the configured ad account")
    for command, label in (("campaigns", "campaigns"), ("adsets", "ad sets"), ("ads", "ads"), ("creatives", "ad creatives"), ("audiences", "custom audiences"), ("images", "ad images"), ("videos", "ad videos")):
        add_limit(commands.add_parser(command, help=f"list {label}"))
    pixels = commands.add_parser("pixels", help="list ad-account pixels")
    add_limit(pixels)
    search = commands.add_parser("targeting-search", help="search Meta targeting options")
    search.add_argument("--type", required=True, choices=("adinterest", "adinterestsuggestion", "adlocale", "adcountry", "adgeolocation", "adeducationschool", "adworkemployer", "adworkposition"))
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=25)
    estimate = commands.add_parser("delivery-estimate", help="estimate delivery for a targeting spec")
    estimate.add_argument("--spec-file", required=True)
    obj = commands.add_parser("object", help="read one campaign, ad set, ad, or creative")
    obj.add_argument("object_type", choices=sorted(READ_FIELDS))
    obj.add_argument("object_id")
    insights = commands.add_parser("insights", help="read ad performance insights")
    insights.add_argument("--level", choices=("account", "campaign", "adset", "ad"), default="account")
    insights.add_argument("--fields", default=DEFAULT_INSIGHT_FIELDS)
    insights.add_argument("--date-preset", default="last_30d")
    insights.add_argument("--since")
    insights.add_argument("--until")
    insights.add_argument("--breakdowns")
    add_limit(insights, 100)
    previews = commands.add_parser("previews", help="read ad previews")
    previews.add_argument("creative_id")
    previews.add_argument("--format", default="DESKTOP_FEED_STANDARD")
    video_status = commands.add_parser("video-status", help="read an uploaded ad video's processing status")
    video_status.add_argument("video_id")
    upload_image = commands.add_parser("upload-image", help="upload a local JPEG or PNG to the ad account")
    upload_image.add_argument("--file", required=True)
    upload_image.add_argument("--name")
    add_write_flags(upload_image)
    upload_video = commands.add_parser("upload-video", help="upload a local video or import one from a public URL")
    video_source = upload_video.add_mutually_exclusive_group(required=True)
    video_source.add_argument("--file")
    video_source.add_argument("--url")
    upload_video.add_argument("--name")
    upload_video.add_argument("--title")
    upload_video.add_argument("--description")
    add_write_flags(upload_video)
    delete_image = commands.add_parser("delete-image", help="delete an uploaded ad image by hash")
    delete_image.add_argument("image_hash")
    add_write_flags(delete_image)
    delete_video = commands.add_parser("delete-video", help="delete an uploaded ad video")
    delete_video.add_argument("video_id")
    add_write_flags(delete_video)
    for action in ("create", "update"):
        change = commands.add_parser(action, help=f"{action} a typed advertising object")
        change.add_argument("object_type", choices=sorted(WRITE_FIELDS))
        if action == "update":
            change.add_argument("object_id")
        change.add_argument("--spec-file", required=True)
        add_write_flags(change)
    archive = commands.add_parser("archive", help="archive a campaign, ad set, or ad")
    archive.add_argument("object_type", choices=("campaign", "adset", "ad"))
    archive.add_argument("object_id")
    add_write_flags(archive)
    args = parser.parse_args()

    try:
        dry_run = getattr(args, "dry_run", False)
        client = None if dry_run else GraphClient()
        if args.command == "accounts":
            assert client is not None
            print_json(client.get("me/adaccounts", {"fields": ACCOUNT_FIELDS, "limit": checked_limit(args.limit)}))
        elif args.command == "account":
            assert client is not None
            print_json(client.get(account_id(args), {"fields": ACCOUNT_FIELDS}))
        elif args.command in {"campaigns", "adsets", "ads", "creatives", "audiences"}:
            assert client is not None
            object_type = {"creatives": "creative", "audiences": "audience"}.get(args.command, args.command.rstrip("s"))
            print_json(client.get(f"{account_id(args)}/{EDGES[object_type]}", {"fields": READ_FIELDS[object_type], "limit": checked_limit(args.limit)}))
        elif args.command in {"images", "videos"}:
            assert client is not None
            edge, fields = ("adimages", IMAGE_FIELDS) if args.command == "images" else ("advideos", VIDEO_FIELDS)
            print_json(client.get(f"{account_id(args)}/{edge}", {"fields": fields, "limit": checked_limit(args.limit)}))
        elif args.command == "pixels":
            assert client is not None
            print_json(client.get(f"{account_id(args)}/adspixels", {"fields": "id,name,last_fired_time,is_unavailable", "limit": checked_limit(args.limit)}))
        elif args.command == "targeting-search":
            assert client is not None
            print_json(client.get("search", {"type": args.type, "q": args.query, "limit": checked_limit(args.limit)}))
        elif args.command == "delivery-estimate":
            assert client is not None
            payload = load_json_object(args.spec_file)
            print_json(client.get(f"{account_id(args)}/delivery_estimate", payload))
        elif args.command == "object":
            assert client is not None
            object_id = require_object_id(args.object_id, "META_UNUSED", f"{args.object_type} ID")
            print_json(client.get(object_id, {"fields": READ_FIELDS[args.object_type]}))
        elif args.command == "insights":
            assert client is not None
            params = {"level": args.level, "fields": args.fields, "limit": checked_limit(args.limit)}
            if args.since or args.until:
                if not args.since or not args.until:
                    raise MetaGraphError("use --since and --until together")
                params["time_range"] = {"since": args.since, "until": args.until}
            else:
                params["date_preset"] = args.date_preset
            if args.breakdowns:
                params["breakdowns"] = args.breakdowns
            print_json(client.get(f"{account_id(args)}/insights", params))
        elif args.command == "previews":
            assert client is not None
            creative_id = require_object_id(args.creative_id, "META_UNUSED", "creative ID")
            print_json(client.get(f"{creative_id}/previews", {"ad_format": args.format}))
        elif args.command == "video-status":
            assert client is not None
            video_id = require_object_id(args.video_id, "META_UNUSED", "video ID")
            print_json(client.get(video_id, {"fields": VIDEO_FIELDS}))
        elif args.command in {"upload-image", "upload-video"}:
            require_write_confirmation(args.confirm_write, args.dry_run, "ad media upload")
            endpoint = f"{account_id(args)}/{'adimages' if args.command == 'upload-image' else 'advideos'}"
            payload = {key: value for key, value in {
                "name": getattr(args, "name", None),
                "title": getattr(args, "title", None),
                "description": getattr(args, "description", None),
            }.items() if value}
            path = None
            mime_type = None
            size = None
            if args.command == "upload-image":
                path, mime_type, size = media_file_info(args.file, "image")
                file_field, host = "filename", "graph.facebook.com"
            elif args.file:
                path, mime_type, size = media_file_info(args.file, "video")
                file_field, host = "source", "graph-video.facebook.com"
            else:
                payload["file_url"] = require_public_https_url(args.url, "--url")
                file_field, host = "", "graph-video.facebook.com"
            if args.dry_run:
                print_json(upload_preview(args.command, endpoint, path, mime_type, size, payload))
            else:
                assert client is not None
                if path is not None:
                    result = client.upload_file(endpoint, path, file_field, mime_type or "application/octet-stream", payload, host=host)
                else:
                    result = client.post(endpoint, payload)
                print_json({"uploaded": True, "action": args.command, "ad_account_id": account_id(args), "result": result})
        elif args.command in {"delete-image", "delete-video"}:
            require_write_confirmation(args.confirm_write, args.dry_run, "ad media deletion")
            if args.command == "delete-image":
                endpoint = f"{account_id(args)}/adimages"
                payload = {"hash": require_object_id(args.image_hash, "META_UNUSED", "image hash")}
            else:
                endpoint = require_object_id(args.video_id, "META_UNUSED", "video ID")
                payload = {}
            if args.dry_run:
                print_json({"dry_run": True, "action": args.command, "method": "DELETE", "endpoint": endpoint, "payload": payload})
            else:
                assert client is not None
                print_json({"deleted": True, "action": args.command, "result": client.delete(endpoint, payload)})
        elif args.command in {"create", "update"}:
            print_json(write_object(args, client, args.command))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "ad archive")
            object_id = require_object_id(args.object_id, "META_UNUSED", f"{args.object_type} ID")
            payload = {"status": "ARCHIVED"}
            if args.dry_run:
                print_json({"dry_run": True, "action": f"archive_{args.object_type}", "endpoint": object_id, "payload": payload})
            else:
                assert client is not None
                print_json({"changed": True, "action": f"archive_{args.object_type}", "result": client.post(object_id, payload)})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
