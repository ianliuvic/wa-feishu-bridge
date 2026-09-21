#!/usr/bin/env python3
"""Inspect and manage authorized Meta commerce catalogs."""

import argparse

from meta_graph import (
    GraphClient,
    MetaGraphError,
    load_json_object,
    print_json,
    require_allowed_fields,
    require_object_id,
    require_write_confirmation,
)


CATALOG_FIELDS = "id,name,vertical,product_count,business{id,name},feed_count"
PRODUCT_FIELDS = "id,retailer_id,name,description,availability,condition,price,currency,brand,url,image_url,inventory"
SET_FIELDS = "id,name,filter,product_count,retailer_id"
FEED_FIELDS = "id,name,schedule,created_time,update_schedule,latest_upload{id,start_time,end_time,num_detected_items,num_invalid_items}"
SET_WRITE_FIELDS = {"name", "filter", "retailer_id"}


def add_limit(parser: argparse.ArgumentParser, default: int = 50) -> None:
    parser.add_argument("--limit", type=int, default=default)


def add_write_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def checked_limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise MetaGraphError("--limit must be between 1 and 100")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Meta commerce catalogs")
    parser.add_argument("--business-id")
    parser.add_argument("--catalog-id")
    commands = parser.add_subparsers(dest="command", required=True)
    add_limit(commands.add_parser("catalogs", help="list business-owned catalogs"), 100)
    commands.add_parser("catalog", help="read one catalog")
    for command in ("products", "product-sets", "feeds"):
        add_limit(commands.add_parser(command, help=f"list catalog {command}"))
    product = commands.add_parser("product", help="read one catalog product")
    product.add_argument("product_id")
    batch = commands.add_parser("batch-items", help="create, update, or delete catalog items with an items_batch request")
    batch.add_argument("--spec-file", required=True)
    add_write_flags(batch)
    create_set = commands.add_parser("create-product-set", help="create a product set")
    create_set.add_argument("--spec-file", required=True)
    add_write_flags(create_set)
    update_set = commands.add_parser("update-product-set", help="update a product set")
    update_set.add_argument("product_set_id")
    update_set.add_argument("--spec-file", required=True)
    add_write_flags(update_set)
    delete_set = commands.add_parser("delete-product-set", help="delete a product set")
    delete_set.add_argument("product_set_id")
    add_write_flags(delete_set)
    args = parser.parse_args()

    try:
        dry_run = getattr(args, "dry_run", False)
        client = None if dry_run else GraphClient()
        if args.command == "catalogs":
            assert client is not None
            business_id = require_object_id(args.business_id, "META_BUSINESS_BUSINESS_ID", "business ID")
            print_json(client.get(f"{business_id}/owned_product_catalogs", {"fields": CATALOG_FIELDS, "limit": checked_limit(args.limit)}))
            return
        catalog_id = require_object_id(args.catalog_id, "META_BUSINESS_CATALOG_ID", "catalog ID")
        if args.command == "catalog":
            assert client is not None
            print_json(client.get(catalog_id, {"fields": CATALOG_FIELDS}))
        elif args.command in {"products", "product-sets", "feeds"}:
            assert client is not None
            edge, fields = {
                "products": ("products", PRODUCT_FIELDS),
                "product-sets": ("product_sets", SET_FIELDS),
                "feeds": ("product_feeds", FEED_FIELDS),
            }[args.command]
            print_json(client.get(f"{catalog_id}/{edge}", {"fields": fields, "limit": checked_limit(args.limit)}))
        elif args.command == "product":
            assert client is not None
            product_id = require_object_id(args.product_id, "META_UNUSED", "product ID")
            print_json(client.get(product_id, {"fields": PRODUCT_FIELDS}))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "catalog change")
            method = "POST"
            if args.command == "batch-items":
                payload = load_json_object(args.spec_file)
                if not isinstance(payload.get("requests"), list) or not payload["requests"]:
                    raise MetaGraphError("batch item spec must contain a non-empty requests array")
                endpoint = f"{catalog_id}/items_batch"
            elif args.command in {"create-product-set", "update-product-set"}:
                payload = require_allowed_fields(load_json_object(args.spec_file), SET_WRITE_FIELDS, "product set")
                endpoint = f"{catalog_id}/product_sets" if args.command == "create-product-set" else require_object_id(args.product_set_id, "META_UNUSED", "product set ID")
            else:
                payload = {}
                endpoint = require_object_id(args.product_set_id, "META_UNUSED", "product set ID")
                method = "DELETE"
            if args.dry_run:
                print_json({"dry_run": True, "action": args.command, "method": method, "endpoint": endpoint, "payload": payload})
            else:
                assert client is not None
                result = client.post(endpoint, payload) if method == "POST" else client.delete(endpoint)
                print_json({"changed": True, "action": args.command, "result": result})
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
