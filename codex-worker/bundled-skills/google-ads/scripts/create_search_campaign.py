#!/usr/bin/env python3
"""Validate or atomically create a complete paused Google Search campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

import google_ads_ops as common


def require(condition: bool, message: str) -> None:
    if not condition:
        common.fail(message)


def load_and_validate_spec(path: str) -> dict[str, Any]:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        common.fail(f"Unable to read search campaign spec: {type(exc).__name__}: {exc}")
    required = {
        "customer_id", "campaign", "ad_group", "locations", "language_ids",
        "keywords", "negative_keywords", "ad", "sitelinks", "callouts",
        "structured_snippet",
    }
    require(isinstance(spec, dict), "Spec must be a JSON object")
    missing = sorted(required - set(spec))
    require(not missing, "Missing spec fields: " + ", ".join(missing))
    spec["customer_id"] = common.normalized_customer_id(str(spec["customer_id"]))

    campaign = spec["campaign"]
    ad_group = spec["ad_group"]
    ad = spec["ad"]
    require(campaign.get("status") == "PAUSED", "New campaign status must be PAUSED")
    require(ad_group.get("status") == "PAUSED", "New ad group status must be PAUSED")
    require(ad.get("status") == "PAUSED", "New ad status must be PAUSED")
    require(int(campaign.get("daily_budget_micros", 0)) > 0, "daily_budget_micros must be positive")
    require(int(ad_group.get("cpc_bid_micros", 0)) > 0, "cpc_bid_micros must be positive")
    require(len(spec["locations"]) > 0, "At least one location is required")
    require(len(spec["language_ids"]) > 0, "At least one language is required")
    require(len(spec["keywords"]) > 0, "At least one positive keyword is required")
    require(len(spec["negative_keywords"]) >= 8, "At least eight negative keywords are required")

    headlines = ad.get("headlines", [])
    descriptions = ad.get("descriptions", [])
    require(len(headlines) == 15, "RSA must contain exactly 15 headlines")
    require(len(descriptions) == 4, "RSA must contain exactly 4 descriptions")
    require(all(1 <= len(text) <= 30 for text in headlines), "Each headline must be 1-30 characters")
    require(all(1 <= len(text) <= 90 for text in descriptions), "Each description must be 1-90 characters")
    require(len(ad.get("path1", "")) <= 15 and len(ad.get("path2", "")) <= 15, "RSA paths must be <=15 characters")
    require(len(ad.get("final_urls", [])) == 1, "Exactly one final URL is required")
    parsed = urlparse(ad["final_urls"][0])
    require(parsed.scheme == "https" and bool(parsed.netloc), "Final URL must be an absolute HTTPS URL")

    require(len(spec["sitelinks"]) >= 4, "At least four sitelinks are required")
    require(len(spec["callouts"]) >= 4, "At least four callouts are required")
    for item in spec["sitelinks"]:
        require(1 <= len(item.get("text", "")) <= 25, "Sitelink text must be 1-25 characters")
        require(len(item.get("description1", "")) <= 35, "Sitelink description1 must be <=35 characters")
        require(len(item.get("description2", "")) <= 35, "Sitelink description2 must be <=35 characters")
        link = urlparse(item.get("final_url", ""))
        require(link.scheme == "https" and bool(link.netloc), "Each sitelink requires an HTTPS final URL")
    require(all(1 <= len(text) <= 25 for text in spec["callouts"]), "Each callout must be 1-25 characters")
    snippet = spec["structured_snippet"]
    require(bool(snippet.get("header")), "Structured snippet header is required")
    require(len(snippet.get("values", [])) >= 3, "Structured snippet requires at least three values")
    return spec


def add_mutate_operation(client: Any, operations: list[Any], field: str, operation: Any) -> None:
    mutate_operation = client.get_type("MutateOperation")
    client.copy_from(getattr(mutate_operation, field), operation)
    operations.append(mutate_operation)


def text_asset(client: Any, text: str) -> Any:
    asset = client.get_type("AdTextAsset")
    asset.text = text
    return asset


def ensure_campaign_absent(client: Any, customer_id: str, campaign_name: str) -> None:
    safe_name = campaign_name.replace("\\", "\\\\").replace("'", "\\'")
    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = customer_id
    request.query = (
        "SELECT campaign.id, campaign.name, campaign.status FROM campaign "
        f"WHERE campaign.name = '{safe_name}' AND campaign.status != 'REMOVED' LIMIT 1"
    )
    rows = list(client.get_service("GoogleAdsService").search(request=request))
    require(not rows, f"A non-removed campaign named '{campaign_name}' already exists")


def build_operations(client: Any, spec: dict[str, Any]) -> list[Any]:
    customer_id = spec["customer_id"]
    operations: list[Any] = []
    temporary_id = -1

    budget_operation = client.get_type("CampaignBudgetOperation")
    budget = budget_operation.create
    budget.resource_name = client.get_service("CampaignBudgetService").campaign_budget_path(customer_id, temporary_id)
    temporary_id -= 1
    budget.name = spec["campaign"]["budget_name"]
    budget.amount_micros = int(spec["campaign"]["daily_budget_micros"])
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    budget.explicitly_shared = False
    add_mutate_operation(client, operations, "campaign_budget_operation", budget_operation)

    campaign_operation = client.get_type("CampaignOperation")
    campaign = campaign_operation.create
    campaign.resource_name = client.get_service("CampaignService").campaign_path(customer_id, temporary_id)
    temporary_id -= 1
    campaign.name = spec["campaign"]["name"]
    campaign.status = client.enums.CampaignStatusEnum.PAUSED
    campaign.advertising_channel_type = client.enums.AdvertisingChannelTypeEnum.SEARCH
    campaign.campaign_budget = budget.resource_name
    client.copy_from(campaign.manual_cpc, client.get_type("ManualCpc"))
    campaign.manual_cpc.enhanced_cpc_enabled = False
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = False
    campaign.network_settings.target_partner_search_network = False
    campaign.network_settings.target_content_network = False
    campaign.geo_target_type_setting.positive_geo_target_type = client.enums.PositiveGeoTargetTypeEnum.PRESENCE
    campaign.geo_target_type_setting.negative_geo_target_type = client.enums.NegativeGeoTargetTypeEnum.PRESENCE
    campaign.contains_eu_political_advertising = (
        client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    add_mutate_operation(client, operations, "campaign_operation", campaign_operation)

    for location_id in spec["locations"]:
        criterion_operation = client.get_type("CampaignCriterionOperation")
        criterion = criterion_operation.create
        criterion.campaign = campaign.resource_name
        criterion.location.geo_target_constant = f"geoTargetConstants/{int(location_id)}"
        add_mutate_operation(client, operations, "campaign_criterion_operation", criterion_operation)

    for language_id in spec["language_ids"]:
        criterion_operation = client.get_type("CampaignCriterionOperation")
        criterion = criterion_operation.create
        criterion.campaign = campaign.resource_name
        criterion.language.language_constant = f"languageConstants/{int(language_id)}"
        add_mutate_operation(client, operations, "campaign_criterion_operation", criterion_operation)

    for item in spec["negative_keywords"]:
        criterion_operation = client.get_type("CampaignCriterionOperation")
        criterion = criterion_operation.create
        criterion.campaign = campaign.resource_name
        criterion.negative = True
        criterion.keyword.text = item["text"]
        criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, item["match_type"])
        add_mutate_operation(client, operations, "campaign_criterion_operation", criterion_operation)

    ad_group_operation = client.get_type("AdGroupOperation")
    ad_group = ad_group_operation.create
    ad_group.resource_name = client.get_service("AdGroupService").ad_group_path(customer_id, temporary_id)
    temporary_id -= 1
    ad_group.name = spec["ad_group"]["name"]
    ad_group.campaign = campaign.resource_name
    ad_group.status = client.enums.AdGroupStatusEnum.PAUSED
    ad_group.type_ = client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    ad_group.cpc_bid_micros = int(spec["ad_group"]["cpc_bid_micros"])
    add_mutate_operation(client, operations, "ad_group_operation", ad_group_operation)

    for item in spec["keywords"]:
        criterion_operation = client.get_type("AdGroupCriterionOperation")
        criterion = criterion_operation.create
        criterion.ad_group = ad_group.resource_name
        criterion.status = client.enums.AdGroupCriterionStatusEnum.PAUSED
        criterion.keyword.text = item["text"]
        criterion.keyword.match_type = getattr(client.enums.KeywordMatchTypeEnum, item["match_type"])
        add_mutate_operation(client, operations, "ad_group_criterion_operation", criterion_operation)

    ad_operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = ad_operation.create
    ad_group_ad.ad_group = ad_group.resource_name
    ad_group_ad.status = client.enums.AdGroupAdStatusEnum.PAUSED
    ad_group_ad.ad.final_urls.extend(spec["ad"]["final_urls"])
    ad_group_ad.ad.responsive_search_ad.headlines.extend(
        [text_asset(client, text) for text in spec["ad"]["headlines"]]
    )
    ad_group_ad.ad.responsive_search_ad.descriptions.extend(
        [text_asset(client, text) for text in spec["ad"]["descriptions"]]
    )
    ad_group_ad.ad.responsive_search_ad.path1 = spec["ad"]["path1"]
    ad_group_ad.ad.responsive_search_ad.path2 = spec["ad"]["path2"]
    add_mutate_operation(client, operations, "ad_group_ad_operation", ad_operation)

    asset_service = client.get_service("AssetService")
    for item in spec["sitelinks"]:
        asset_operation = client.get_type("AssetOperation")
        asset = asset_operation.create
        asset.resource_name = asset_service.asset_path(customer_id, temporary_id)
        temporary_id -= 1
        asset.final_urls.append(item["final_url"])
        asset.sitelink_asset.link_text = item["text"]
        asset.sitelink_asset.description1 = item["description1"]
        asset.sitelink_asset.description2 = item["description2"]
        add_mutate_operation(client, operations, "asset_operation", asset_operation)
        link_operation = client.get_type("CampaignAssetOperation")
        link = link_operation.create
        link.campaign = campaign.resource_name
        link.asset = asset.resource_name
        link.field_type = client.enums.AssetFieldTypeEnum.SITELINK
        # Keep assets visible in the paused campaign. They cannot serve while
        # the campaign, ad group, keywords, and RSA remain paused.
        link.status = client.enums.AssetLinkStatusEnum.ENABLED
        add_mutate_operation(client, operations, "campaign_asset_operation", link_operation)

    for text in spec["callouts"]:
        asset_operation = client.get_type("AssetOperation")
        asset = asset_operation.create
        asset.resource_name = asset_service.asset_path(customer_id, temporary_id)
        temporary_id -= 1
        asset.callout_asset.callout_text = text
        add_mutate_operation(client, operations, "asset_operation", asset_operation)
        link_operation = client.get_type("CampaignAssetOperation")
        link = link_operation.create
        link.campaign = campaign.resource_name
        link.asset = asset.resource_name
        link.field_type = client.enums.AssetFieldTypeEnum.CALLOUT
        link.status = client.enums.AssetLinkStatusEnum.ENABLED
        add_mutate_operation(client, operations, "campaign_asset_operation", link_operation)

    snippet = spec["structured_snippet"]
    asset_operation = client.get_type("AssetOperation")
    asset = asset_operation.create
    asset.resource_name = asset_service.asset_path(customer_id, temporary_id)
    asset.structured_snippet_asset.header = snippet["header"]
    asset.structured_snippet_asset.values.extend(snippet["values"])
    add_mutate_operation(client, operations, "asset_operation", asset_operation)
    link_operation = client.get_type("CampaignAssetOperation")
    link = link_operation.create
    link.campaign = campaign.resource_name
    link.asset = asset.resource_name
    link.field_type = client.enums.AssetFieldTypeEnum.STRUCTURED_SNIPPET
    link.status = client.enums.AssetLinkStatusEnum.ENABLED
    add_mutate_operation(client, operations, "campaign_asset_operation", link_operation)
    return operations


def run(spec_path: str, confirm_write: bool) -> None:
    spec = load_and_validate_spec(spec_path)
    client = common.get_client()
    ensure_campaign_absent(client, spec["customer_id"], spec["campaign"]["name"])
    request = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = spec["customer_id"]
    request.mutate_operations.extend(build_operations(client, spec))
    request.partial_failure = False
    request.validate_only = not confirm_write
    response = client.get_service("GoogleAdsService").mutate(request=request)
    results = [common.protobuf_to_dict(item) for item in response.mutate_operation_responses]
    if confirm_write:
        common.write_audit(spec["customer_id"], spec_path, [{
            "type": "search_campaign.create",
            "campaign_name": spec["campaign"]["name"],
            "daily_budget_micros": spec["campaign"]["daily_budget_micros"],
            "status": "PAUSED",
            "results": results,
        }])
    print(json.dumps({
        "success": True,
        "mode": "validate_only" if not confirm_write else "applied",
        "customer_id": spec["customer_id"],
        "campaign_name": spec["campaign"]["name"],
        "operation_count": len(request.mutate_operations),
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
        common.fail(f"Search campaign creation failed: {type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    main()
