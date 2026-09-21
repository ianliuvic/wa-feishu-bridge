#!/usr/bin/env python3
"""Read Facebook Page, post, and Instagram professional-account insights."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id


DEFAULTS = {
    "page": "page_views_total,page_post_engagements,page_fans",
    "post": "post_impressions,post_engaged_users,post_clicks",
    "instagram-account": "reach,total_interactions,profile_views",
    "instagram-media": "views,reach,likes,comments,saved,shares,total_interactions",
}


def metrics(value: str | None, kind: str) -> str:
    resolved = value or DEFAULTS[kind]
    names = [item.strip() for item in resolved.split(",") if item.strip()]
    if not names or any(not item.replace("_", "").isalnum() for item in names):
        raise MetaGraphError("--metrics must be a comma-separated list of metric names")
    return ",".join(names)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read Meta organic-content insights")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("page", "post", "instagram-account", "instagram-media"):
        item = commands.add_parser(command)
        if command == "page":
            item.add_argument("--page-id")
            item.add_argument("--period", default="day")
        elif command == "post":
            item.add_argument("object_id")
            item.add_argument("--period", default="lifetime")
        elif command == "instagram-account":
            item.add_argument("--ig-user-id")
            item.add_argument("--period", default="day")
            item.add_argument("--metric-type", choices=("time_series", "total_value"), default="total_value")
        else:
            item.add_argument("media_id")
            item.add_argument("--period", default="lifetime")
        item.add_argument("--metrics")
        item.add_argument("--since")
        item.add_argument("--until")
    args = parser.parse_args()

    try:
        client = GraphClient()
        if bool(args.since) != bool(args.until):
            raise MetaGraphError("use --since and --until together")
        params = {"metric": metrics(args.metrics, args.command), "period": args.period}
        if args.since:
            params.update({"since": args.since, "until": args.until})
        if args.command == "page":
            object_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
            client = client.page_client(object_id)
        elif args.command == "post":
            object_id = require_object_id(args.object_id, "META_UNUSED", "post ID")
            page_id = require_object_id(None, "META_BUSINESS_PAGE_ID", "Page ID")
            client = client.page_client(page_id)
        elif args.command == "instagram-account":
            object_id = require_object_id(args.ig_user_id, "META_BUSINESS_IG_USER_ID", "Instagram user ID")
            params["metric_type"] = args.metric_type
        else:
            object_id = require_object_id(args.media_id, "META_UNUSED", "Instagram media ID")
        print_json(client.get(f"{object_id}/insights", params))
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
