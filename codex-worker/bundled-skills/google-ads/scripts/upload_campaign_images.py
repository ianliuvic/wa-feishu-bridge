#!/usr/bin/env python3
"""Validate or atomically upload and link campaign image assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import google_ads_ops as common


ALLOWED_FIELD_TYPES = {"AD_IMAGE"}
MAX_FILE_SIZE = 5 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        common.fail(message)


def load_spec(path: str) -> dict[str, Any]:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        common.fail(f"Unable to read image asset spec: {type(exc).__name__}: {exc}")
    require(isinstance(spec, dict), "Spec must be a JSON object")
    spec["customer_id"] = common.normalized_customer_id(str(spec.get("customer_id", "")))
    campaign_id = str(spec.get("campaign_id", ""))
    require(campaign_id.isdigit(), "campaign_id must be numeric")
    images = spec.get("images")
    require(isinstance(images, list) and images, "Spec requires a non-empty images array")
    for item in images:
        require(isinstance(item, dict), "Each image must be an object")
        image_path = Path(item.get("path", ""))
        require(image_path.is_file(), f"Image file not found: {image_path}")
        require(image_path.suffix.lower() in {".jpg", ".jpeg", ".png"}, f"Unsupported image type: {image_path.suffix}")
        require(0 < image_path.stat().st_size <= MAX_FILE_SIZE, f"Image must be 1 byte to 5 MB: {image_path}")
        require(1 <= len(item.get("name", "")) <= 128, "Image asset name must be 1-128 characters")
        require(item.get("field_type") in ALLOWED_FIELD_TYPES, "Unsupported image asset field_type")
    return spec


def add_mutate_operation(client: Any, operations: list[Any], field: str, operation: Any) -> None:
    mutate_operation = client.get_type("MutateOperation")
    client.copy_from(getattr(mutate_operation, field), operation)
    operations.append(mutate_operation)


def ensure_campaign_is_paused(client: Any, customer_id: str, campaign_id: str) -> None:
    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = customer_id
    request.query = (
        "SELECT campaign.id, campaign.status FROM campaign "
        f"WHERE campaign.id = {campaign_id} LIMIT 1"
    )
    rows = list(client.get_service("GoogleAdsService").search(request=request))
    require(len(rows) == 1, f"Campaign {campaign_id} was not found")
    status = rows[0].campaign.status.name
    require(status == "PAUSED", f"Campaign must be PAUSED before adding draft images; current status is {status}")


def build_operations(client: Any, spec: dict[str, Any]) -> tuple[list[Any], list[dict[str, str]]]:
    customer_id = spec["customer_id"]
    campaign_name = client.get_service("CampaignService").campaign_path(customer_id, spec["campaign_id"])
    asset_service = client.get_service("AssetService")
    operations: list[Any] = []
    manifests: list[dict[str, str]] = []
    temporary_id = -1
    for item in spec["images"]:
        path = Path(item["path"])
        data = path.read_bytes()
        asset_operation = client.get_type("AssetOperation")
        asset = asset_operation.create
        asset.resource_name = asset_service.asset_path(customer_id, temporary_id)
        temporary_id -= 1
        asset.name = item["name"]
        asset.image_asset.data = data
        add_mutate_operation(client, operations, "asset_operation", asset_operation)

        link_operation = client.get_type("CampaignAssetOperation")
        link = link_operation.create
        link.campaign = campaign_name
        link.asset = asset.resource_name
        link.field_type = getattr(client.enums.AssetFieldTypeEnum, item["field_type"])
        link.status = client.enums.AssetLinkStatusEnum.ENABLED
        add_mutate_operation(client, operations, "campaign_asset_operation", link_operation)
        manifests.append({
            "path": str(path.resolve()),
            "name": item["name"],
            "field_type": item["field_type"],
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return operations, manifests


def run(spec_path: str, confirm_write: bool) -> None:
    spec = load_spec(spec_path)
    client = common.get_client()
    ensure_campaign_is_paused(client, spec["customer_id"], str(spec["campaign_id"]))
    operations, manifests = build_operations(client, spec)
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = spec["customer_id"]
    request.mutate_operations.extend(operations)
    request.partial_failure = False
    request.validate_only = not confirm_write
    response = client.get_service("GoogleAdsService").mutate(request=request)
    results = [common.protobuf_to_dict(item) for item in response.mutate_operation_responses]
    if confirm_write:
        common.write_audit(spec["customer_id"], spec_path, [{
            "type": "campaign_images.create_and_link",
            "campaign_id": spec["campaign_id"],
            "images": manifests,
            "results": results,
        }])
    print(json.dumps({
        "success": True,
        "mode": "validate_only" if not confirm_write else "applied",
        "customer_id": spec["customer_id"],
        "campaign_id": spec["campaign_id"],
        "image_count": len(spec["images"]),
        "operation_count": len(operations),
        "images": manifests,
        "results": results,
        "audit_path": str(common.AUDIT_PATH) if confirm_write else None,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--confirm-write", action="store_true")
    args = parser.parse_args()
    try:
        run(args.spec, args.confirm_write)
    except SystemExit:
        raise
    except Exception as exc:
        common.fail(f"Campaign image upload failed: {type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    main()
