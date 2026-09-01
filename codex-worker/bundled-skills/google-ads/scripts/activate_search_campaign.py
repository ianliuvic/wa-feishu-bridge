#!/usr/bin/env python3
"""Validate or atomically enable a complete paused Search campaign stack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from google.protobuf.field_mask_pb2 import FieldMask

import google_ads_ops as common


def require(condition: bool, message: str) -> None:
    if not condition:
        common.fail(message)


def load_spec(path: str) -> dict[str, Any]:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        common.fail(f"Unable to read activation spec: {type(exc).__name__}: {exc}")
    require(isinstance(spec, dict), "Spec must be a JSON object")
    spec["customer_id"] = common.normalized_customer_id(str(spec.get("customer_id", "")))
    required = {"campaign_resource_name", "ad_group_resource_name", "ad_group_ad_resource_name", "keyword_resource_names", "expected_daily_budget_micros"}
    missing = sorted(required - set(spec))
    require(not missing, "Missing spec fields: " + ", ".join(missing))
    prefix = f"customers/{spec['customer_id']}/"
    for key in ("campaign_resource_name", "ad_group_resource_name", "ad_group_ad_resource_name"):
        require(str(spec[key]).startswith(prefix), f"{key} must belong to customer {spec['customer_id']}")
    keywords = spec["keyword_resource_names"]
    require(isinstance(keywords, list) and keywords, "keyword_resource_names must be a non-empty array")
    require(len(set(keywords)) == len(keywords), "keyword_resource_names must be unique")
    require(all(str(name).startswith(prefix) for name in keywords), "Every keyword resource must belong to the customer")
    require(int(spec["expected_daily_budget_micros"]) > 0, "expected_daily_budget_micros must be positive")
    return spec


def search(client: Any, customer_id: str, query: str) -> list[Any]:
    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = customer_id
    request.query = query
    return list(client.get_service("GoogleAdsService").search(request=request))


def verify_current_state(client: Any, spec: dict[str, Any]) -> dict[str, Any]:
    customer_id = spec["customer_id"]
    campaign_id = spec["campaign_resource_name"].rsplit("/", 1)[-1]
    ad_group_id = spec["ad_group_resource_name"].rsplit("/", 1)[-1]
    ad_id = spec["ad_group_ad_resource_name"].rsplit("~", 1)[-1]
    campaign_rows = search(client, customer_id, (
        "SELECT campaign.status, campaign_budget.amount_micros "
        f"FROM campaign WHERE campaign.id = {campaign_id} LIMIT 1"
    ))
    require(len(campaign_rows) == 1, "Campaign was not found")
    campaign = campaign_rows[0]
    require(campaign.campaign.status.name == "PAUSED", "Campaign is not PAUSED")
    actual_budget = int(campaign.campaign_budget.amount_micros)
    require(actual_budget == int(spec["expected_daily_budget_micros"]), f"Daily budget changed unexpectedly: {actual_budget} micros")

    ad_group_rows = search(client, customer_id, (
        "SELECT ad_group.status FROM ad_group "
        f"WHERE ad_group.id = {ad_group_id} LIMIT 1"
    ))
    require(len(ad_group_rows) == 1 and ad_group_rows[0].ad_group.status.name == "PAUSED", "Ad group is not PAUSED")

    ad_rows = search(client, customer_id, (
        "SELECT ad_group_ad.status, ad_group_ad.policy_summary.approval_status "
        "FROM ad_group_ad "
        f"WHERE ad_group_ad.ad.id = {ad_id} LIMIT 1"
    ))
    require(len(ad_rows) == 1, "Responsive Search Ad was not found")
    require(ad_rows[0].ad_group_ad.status.name == "PAUSED", "Responsive Search Ad is not PAUSED")
    require(ad_rows[0].ad_group_ad.policy_summary.approval_status.name == "APPROVED", "Responsive Search Ad is not approved")

    keyword_rows = search(client, customer_id, (
        "SELECT ad_group_criterion.resource_name, ad_group_criterion.status, ad_group_criterion.negative "
        "FROM ad_group_criterion "
        f"WHERE ad_group.id = {ad_group_id} AND ad_group_criterion.type = 'KEYWORD' "
        "AND ad_group_criterion.negative = FALSE"
    ))
    current = {row.ad_group_criterion.resource_name: row for row in keyword_rows}
    require(set(current) == set(spec["keyword_resource_names"]), "Positive keyword set differs from the reviewed activation spec")
    require(all(row.ad_group_criterion.status.name == "PAUSED" for row in current.values()), "Not all positive keywords are PAUSED")
    return {"daily_budget_micros": actual_budget, "keyword_count": len(current)}


def add_operation(client: Any, operations: list[Any], field: str, operation: Any) -> None:
    mutate_operation = client.get_type("MutateOperation")
    client.copy_from(getattr(mutate_operation, field), operation)
    operations.append(mutate_operation)


def build_operations(client: Any, spec: dict[str, Any]) -> list[Any]:
    operations: list[Any] = []
    campaign_operation = client.get_type("CampaignOperation")
    campaign_operation.update.resource_name = spec["campaign_resource_name"]
    campaign_operation.update.status = client.enums.CampaignStatusEnum.ENABLED
    campaign_operation.update_mask = FieldMask(paths=["status"])
    add_operation(client, operations, "campaign_operation", campaign_operation)

    ad_group_operation = client.get_type("AdGroupOperation")
    ad_group_operation.update.resource_name = spec["ad_group_resource_name"]
    ad_group_operation.update.status = client.enums.AdGroupStatusEnum.ENABLED
    ad_group_operation.update_mask = FieldMask(paths=["status"])
    add_operation(client, operations, "ad_group_operation", ad_group_operation)

    for resource_name in spec["keyword_resource_names"]:
        criterion_operation = client.get_type("AdGroupCriterionOperation")
        criterion_operation.update.resource_name = resource_name
        criterion_operation.update.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        criterion_operation.update_mask = FieldMask(paths=["status"])
        add_operation(client, operations, "ad_group_criterion_operation", criterion_operation)

    ad_operation = client.get_type("AdGroupAdOperation")
    ad_operation.update.resource_name = spec["ad_group_ad_resource_name"]
    ad_operation.update.status = client.enums.AdGroupAdStatusEnum.ENABLED
    ad_operation.update_mask = FieldMask(paths=["status"])
    add_operation(client, operations, "ad_group_ad_operation", ad_operation)
    return operations


def run(spec_path: str, confirm_write: bool, confirm_live_change: bool) -> None:
    spec = load_spec(spec_path)
    require(not confirm_write or confirm_live_change, "Activation requires --confirm-live-change")
    client = common.get_client()
    state = verify_current_state(client, spec)
    operations = build_operations(client, spec)
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = spec["customer_id"]
    request.mutate_operations.extend(operations)
    request.partial_failure = False
    request.validate_only = not confirm_write
    response = client.get_service("GoogleAdsService").mutate(request=request)
    results = [common.protobuf_to_dict(item) for item in response.mutate_operation_responses]
    if confirm_write:
        common.write_audit(spec["customer_id"], spec_path, [{
            "type": "search_campaign_stack.activate",
            "daily_budget_micros": state["daily_budget_micros"],
            "keyword_count": state["keyword_count"],
            "results": results,
        }])
    print(json.dumps({
        "success": True,
        "mode": "validate_only" if not confirm_write else "applied",
        "customer_id": spec["customer_id"],
        "daily_budget_micros": state["daily_budget_micros"],
        "keyword_count": state["keyword_count"],
        "operation_count": len(operations),
        "results": results,
        "audit_path": str(common.AUDIT_PATH) if confirm_write else None,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--confirm-live-change", action="store_true")
    args = parser.parse_args()
    try:
        run(args.spec, args.confirm_write, args.confirm_live_change)
    except SystemExit:
        raise
    except Exception as exc:
        common.fail(f"Search campaign activation failed: {type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    main()
