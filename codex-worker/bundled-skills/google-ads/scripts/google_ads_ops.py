#!/usr/bin/env python3
"""Read Google Ads and perform a small, validated set of common updates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


CONFIG_PATH = Path(os.environ.get("CODEX_CONFIG", Path.home() / ".codex" / "config.toml"))
AUDIT_PATH = Path.home() / ".codex" / "google-ads" / "mutation-audit.jsonl"
CUSTOMER_RE = re.compile(r"^\d{10}$")

UPDATE_TYPES = {
    "campaign.update": {
        "service": "CampaignService",
        "method": "mutate_campaigns",
        "operation": "CampaignOperation",
        "request": "MutateCampaignsRequest",
        "allowed": {
            "name", "status", "start_date", "end_date",
            "tracking_url_template", "final_url_suffix",
        },
    },
    "campaign_budget.update": {
        "service": "CampaignBudgetService",
        "method": "mutate_campaign_budgets",
        "operation": "CampaignBudgetOperation",
        "request": "MutateCampaignBudgetsRequest",
        "allowed": {"name", "amount_micros", "delivery_method"},
    },
    "campaign_asset.update": {
        "service": "CampaignAssetService",
        "method": "mutate_campaign_assets",
        "operation": "CampaignAssetOperation",
        "request": "MutateCampaignAssetsRequest",
        "allowed": {"status"},
    },
    "ad_group.update": {
        "service": "AdGroupService",
        "method": "mutate_ad_groups",
        "operation": "AdGroupOperation",
        "request": "MutateAdGroupsRequest",
        "allowed": {
            "name", "status", "cpc_bid_micros",
            "tracking_url_template", "final_url_suffix",
        },
    },
    "ad_group_ad.update": {
        "service": "AdGroupAdService",
        "method": "mutate_ad_group_ads",
        "operation": "AdGroupAdOperation",
        "request": "MutateAdGroupAdsRequest",
        "allowed": {"status"},
    },
    "ad_group_criterion.update": {
        "service": "AdGroupCriterionService",
        "method": "mutate_ad_group_criteria",
        "operation": "AdGroupCriterionOperation",
        "request": "MutateAdGroupCriteriaRequest",
        "allowed": {"status", "cpc_bid_micros", "negative"},
    },
    "customer_conversion_goal.update": {
        "service": "CustomerConversionGoalService",
        "method": "mutate_customer_conversion_goals",
        "operation": "CustomerConversionGoalOperation",
        "request": "MutateCustomerConversionGoalsRequest",
        "allowed": {"biddable"},
    },
}


def fail(message: str, code: int = 2) -> None:
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def normalized_customer_id(value: str) -> str:
    value = value.replace("-", "").strip()
    if not CUSTOMER_RE.fullmatch(value):
        fail("customer_id must contain exactly 10 digits")
    return value


def load_mcp_environment() -> dict[str, str]:
    if not CONFIG_PATH.is_file():
        fail(f"Codex config not found: {CONFIG_PATH}")
    try:
        import tomllib

        config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        env = config["mcp_servers"]["google-ads-mcp"]["env"]
    except Exception as exc:
        fail(f"Unable to read google-ads-mcp configuration: {type(exc).__name__}")
    required = {"GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS"}
    missing = sorted(key for key in required if not env.get(key))
    if missing:
        fail("Missing configured values: " + ", ".join(missing))
    for key, value in env.items():
        if key.startswith("GOOGLE_") or key in {"HTTP_PROXY", "HTTPS_PROXY"}:
            os.environ.setdefault(key, str(value))
    return {"login_customer_id": str(env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", ""))}


def get_client() -> Any:
    load_mcp_environment()
    try:
        import google.auth
        from google.ads.googleads.client import GoogleAdsClient

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/adwords"]
        )
        kwargs: dict[str, Any] = {
            "credentials": credentials,
            "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
            "use_proto_plus": True,
        }
        login_customer_id = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
        if login_customer_id:
            kwargs["login_customer_id"] = login_customer_id
        return GoogleAdsClient(**kwargs)
    except Exception as exc:
        fail(f"Unable to initialize Google Ads client: {type(exc).__name__}: {exc}")


def protobuf_to_dict(message: Any) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    raw = message._pb if hasattr(message, "_pb") else message
    return MessageToDict(raw, preserving_proto_field_name=True, use_integers_for_enums=False)


def command_check(_: argparse.Namespace) -> None:
    meta = load_mcp_environment()
    credential_path = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    print(json.dumps({
        "success": True,
        "config_path": str(CONFIG_PATH),
        "credential_file_exists": credential_path.is_file(),
        "login_customer_id": meta["login_customer_id"],
        "secrets_displayed": False,
    }, ensure_ascii=False, indent=2))


def command_accounts(_: argparse.Namespace) -> None:
    client = get_client()
    response = client.get_service("CustomerService").list_accessible_customers()
    ids = [name.rsplit("/", 1)[-1] for name in response.resource_names]
    print(json.dumps({"success": True, "customer_ids": ids}, indent=2))


def command_query(args: argparse.Namespace) -> None:
    customer_id = normalized_customer_id(args.customer_id)
    query = args.gaql.strip().rstrip(";")
    if not query.lower().startswith("select "):
        fail("GAQL must start with SELECT")
    client = get_client()
    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = customer_id
    request.query = query
    rows = [protobuf_to_dict(row) for row in client.get_service("GoogleAdsService").search(request=request)]
    print(json.dumps({"success": True, "customer_id": customer_id, "rows": rows}, ensure_ascii=False, indent=2))


def load_spec(path: str) -> dict[str, Any]:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Unable to read spec: {type(exc).__name__}: {exc}")
    if not isinstance(spec, dict) or not isinstance(spec.get("operations"), list):
        fail("Spec must be an object containing an operations array")
    return spec


def is_live_change(operation: dict[str, Any]) -> bool:
    fields = operation.get("fields", {})
    if fields.get("status") == "ENABLED":
        return True
    return any(name in fields for name in ("amount_micros", "cpc_bid_micros"))


def set_field(entity: Any, name: str, value: Any) -> None:
    try:
        setattr(entity, name, value)
    except Exception as exc:
        fail(f"Invalid value for field {name}: {type(exc).__name__}: {exc}")


def validate_operation(customer_id: str, operation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(operation, dict):
        fail("Each operation must be an object")
    kind = operation.get("type")
    if kind not in UPDATE_TYPES:
        fail(f"Unsupported operation type: {kind}")
    resource_name = str(operation.get("resource_name", ""))
    if not resource_name.startswith(f"customers/{customer_id}/"):
        fail(f"resource_name must belong to customer {customer_id}")
    fields = operation.get("fields")
    if not isinstance(fields, dict) or not fields:
        fail("Each operation requires a non-empty fields object")
    unknown = sorted(set(fields) - UPDATE_TYPES[kind]["allowed"])
    if unknown:
        fail(f"Unsupported fields for {kind}: {', '.join(unknown)}")
    return str(kind), fields


def mutate_one(client: Any, customer_id: str, operation: dict[str, Any], validate_only: bool) -> dict[str, Any]:
    kind, fields = validate_operation(customer_id, operation)
    cfg = UPDATE_TYPES[kind]
    api_operation = client.get_type(cfg["operation"])
    entity = api_operation.update
    entity.resource_name = operation["resource_name"]
    for name, value in fields.items():
        set_field(entity, name, value)
        api_operation.update_mask.paths.append(name)
    request = client.get_type(cfg["request"])
    request.customer_id = customer_id
    request.operations.append(api_operation)
    request.validate_only = validate_only
    service = client.get_service(cfg["service"])
    response = getattr(service, cfg["method"])(request=request)
    return {
        "type": kind,
        "resource_name": operation["resource_name"],
        "fields": fields,
        "validate_only": validate_only,
        "results": [protobuf_to_dict(item) for item in response.results],
    }


def write_audit(customer_id: str, spec_path: str, results: list[dict[str, Any]]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "customer_id": customer_id,
        "spec_path": str(Path(spec_path).resolve()),
        "results": results,
    }
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def command_mutate(args: argparse.Namespace) -> None:
    spec = load_spec(args.spec)
    customer_id = normalized_customer_id(str(spec.get("customer_id", "")))
    operations = spec["operations"]
    if not operations:
        fail("Spec contains no operations")
    for operation in operations:
        validate_operation(customer_id, operation)
    if args.confirm_write and any(is_live_change(op) for op in operations) and not args.confirm_live_change:
        fail("Budget, bid, or ENABLED changes require --confirm-live-change")
    validate_only = not args.confirm_write
    client = get_client()
    results: list[dict[str, Any]] = []
    for operation in operations:
        # No automatic retry: a transport failure may follow a successful mutation.
        results.append(mutate_one(client, customer_id, operation, validate_only))
    if not validate_only:
        write_audit(customer_id, args.spec, results)
    print(json.dumps({
        "success": True,
        "mode": "validate_only" if validate_only else "applied",
        "customer_id": customer_id,
        "results": results,
        "audit_path": str(AUDIT_PATH) if not validate_only else None,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Check local configuration without showing secrets")
    check.set_defaults(func=command_check)
    accounts = sub.add_parser("accounts", help="List directly accessible customer IDs")
    accounts.set_defaults(func=command_accounts)
    query = sub.add_parser("query", help="Run a read-only GAQL query")
    query.add_argument("--customer-id", required=True)
    query.add_argument("--gaql", required=True)
    query.set_defaults(func=command_query)
    mutate = sub.add_parser("mutate", help="Validate or apply a reviewed update spec")
    mutate.add_argument("--spec", required=True)
    mutate.add_argument("--confirm-write", action="store_true")
    mutate.add_argument("--confirm-live-change", action="store_true")
    mutate.set_defaults(func=command_mutate)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"Google Ads operation failed: {type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    main()
