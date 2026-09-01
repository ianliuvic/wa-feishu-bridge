#!/usr/bin/env python3
"""Validate or update the 15 headlines of a paused responsive search ad."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from google.protobuf.field_mask_pb2 import FieldMask

import google_ads_ops as common


B2B_TERMS = {
    "wholesale", "manufacturer", "factory", "supplier", "private label",
    "dropshipping", "bulk", "no moq", "print provider",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        common.fail(message)


def load_spec(path: str) -> dict[str, Any]:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        common.fail(f"Unable to read RSA update spec: {type(exc).__name__}: {exc}")
    require(isinstance(spec, dict), "Spec must be a JSON object")
    spec["customer_id"] = common.normalized_customer_id(str(spec.get("customer_id", "")))
    require(str(spec.get("campaign_id", "")).isdigit(), "campaign_id must be numeric")
    require(str(spec.get("ad_id", "")).isdigit(), "ad_id must be numeric")
    headlines = spec.get("headlines")
    require(isinstance(headlines, list) and len(headlines) == 15, "RSA requires exactly 15 headlines")
    require(all(isinstance(text, str) and 1 <= len(text) <= 30 for text in headlines), "Each headline must be 1-30 characters")
    require(len(set(headlines)) == 15, "All 15 headlines must be unique")
    normalized = " ".join(headlines).lower()
    present = sorted(term for term in B2B_TERMS if term in normalized)
    require(not present, "B2B terms are not allowed: " + ", ".join(present))
    return spec


def ensure_paused(client: Any, spec: dict[str, Any]) -> None:
    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = spec["customer_id"]
    request.query = (
        "SELECT campaign.id, campaign.status, ad_group.status, ad_group_ad.status, ad_group_ad.ad.id "
        "FROM ad_group_ad "
        f"WHERE ad_group_ad.ad.id = {spec['ad_id']} LIMIT 1"
    )
    rows = list(client.get_service("GoogleAdsService").search(request=request))
    require(len(rows) == 1, f"Ad {spec['ad_id']} was not found")
    row = rows[0]
    require(str(row.campaign.id) == str(spec["campaign_id"]), "Ad does not belong to the specified campaign")
    require(row.campaign.status.name == "PAUSED", "Campaign must remain PAUSED")
    require(row.ad_group.status.name == "PAUSED", "Ad group must remain PAUSED")
    require(row.ad_group_ad.status.name == "PAUSED", "Ad must remain PAUSED")


def text_asset(client: Any, text: str) -> Any:
    asset = client.get_type("AdTextAsset")
    asset.text = text
    return asset


def run(spec_path: str, confirm_write: bool) -> None:
    spec = load_spec(spec_path)
    client = common.get_client()
    ensure_paused(client, spec)
    operation = client.get_type("AdOperation")
    ad = operation.update
    ad.resource_name = client.get_service("AdService").ad_path(spec["customer_id"], spec["ad_id"])
    ad.responsive_search_ad.headlines.extend([text_asset(client, text) for text in spec["headlines"]])
    operation.update_mask = FieldMask(paths=["responsive_search_ad.headlines"])
    request = client.get_type("MutateAdsRequest")
    request.customer_id = spec["customer_id"]
    request.operations.append(operation)
    request.validate_only = not confirm_write
    response = client.get_service("AdService").mutate_ads(request=request)
    results = [common.protobuf_to_dict(item) for item in response.results]
    if confirm_write:
        common.write_audit(spec["customer_id"], spec_path, [{
            "type": "responsive_search_ad.headlines.update",
            "campaign_id": spec["campaign_id"],
            "ad_id": spec["ad_id"],
            "headlines": spec["headlines"],
            "results": results,
        }])
    print(json.dumps({
        "success": True,
        "mode": "validate_only" if not confirm_write else "applied",
        "customer_id": spec["customer_id"],
        "campaign_id": spec["campaign_id"],
        "ad_id": spec["ad_id"],
        "headlines": spec["headlines"],
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
        common.fail(f"RSA headline update failed: {type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    main()
