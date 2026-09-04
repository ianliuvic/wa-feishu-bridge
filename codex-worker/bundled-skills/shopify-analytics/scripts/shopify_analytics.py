#!/usr/bin/env python3
"""Collect ShopifyQL data for a decision-ready Wearhongxiu business report."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_SHOP = "w4ik1r-x5.myshopify.com"
DEFAULT_API_VERSION = "2026-07"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_POD_API_URL = "https://pod-api.wearhongxiu.com"
DEFAULT_ARTIFACT_DIR = "/workspace/codex-artifacts"
POD_PAGE_SIZE = 500
SENSITIVE_KEY = re.compile(
    r"^(?:email|phone|telephone|first_?name|last_?name|full_?name|customer_?id|address|address[12]|"
    r"city|province|state|postal_?code|zip|country_?code|ip|ip_?address)$",
    re.IGNORECASE,
)


def fail(message: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    raise SystemExit(code)


def request_json(request: Request, timeout: int = 60) -> dict:
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc


def redact_personal_data(value: object) -> tuple[object, int]:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        redactions = 0
        for key, item in value.items():
            if SENSITIVE_KEY.match(str(key)):
                result[str(key)] = "[REDACTED]"
                redactions += 1
            else:
                cleaned, count = redact_personal_data(item)
                result[str(key)] = cleaned
                redactions += count
        return result, redactions
    if isinstance(value, list):
        result_list = []
        redactions = 0
        for item in value:
            cleaned, count = redact_personal_data(item)
            result_list.append(cleaned)
            redactions += count
        return result_list, redactions
    return value, 0


def pod_config() -> tuple[str, str]:
    api_url = os.getenv("POD_API_URL", DEFAULT_POD_API_URL).strip().rstrip("/")
    token = os.getenv("POD_MONITORING_TOKEN", "").strip()
    if not token:
        raise RuntimeError("missing environment variable: POD_MONITORING_TOKEN")
    return api_url, token


def pod_day_bounds(as_of: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    until = datetime.combine(as_of, datetime.min.time(), zone)
    return until - timedelta(days=1), until


def collect_pod_designs(as_of: date, artifact_dir: str, current_day: bool = False) -> dict:
    api_url, token = pod_config()
    if current_day:
        zone = ZoneInfo(DEFAULT_TIMEZONE)
        until = datetime.now(zone)
        since = datetime.combine(as_of, datetime.min.time(), zone)
    else:
        since, until = pod_day_bounds(as_of)
    records: list[dict] = []
    total: int | None = None
    offset = 0
    while total is None or offset < total:
        query = urlencode({
            "since": since.isoformat(),
            "until": until.isoformat(),
            "limit": POD_PAGE_SIZE,
            "offset": offset,
        })
        request = Request(
            f"{api_url}/internal/designs?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "Mozilla/5.0 (compatible; HongxiuAnalytics/1.0; +https://shop.wearhongxiu.com)",
            },
        )
        payload = request_json(request, timeout=90)
        page = payload.get("designs")
        if not isinstance(page, list) or not isinstance(payload.get("total"), int):
            raise RuntimeError("POD API returned an invalid design-list response")
        if total is None:
            total = payload["total"]
        elif total != payload["total"]:
            raise RuntimeError("POD design total changed during pagination; retry the report")
        records.extend(item for item in page if isinstance(item, dict))
        offset += len(page)
        if not page:
            break
    if total is None or len(records) != total:
        raise RuntimeError(f"POD API pagination incomplete: expected {total or 0}, received {len(records)}")

    cleaned_records, redactions = redact_personal_data(records)
    day = as_of.isoformat() if current_day else (as_of - timedelta(days=1)).isoformat()
    output_dir = Path(artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-through-{until.strftime('%H%M')}" if current_day else ""
    artifact = output_dir / f"pod-designs-{day}{suffix}.json"
    export = {
        "schema": "hongxiu-pod-design-export-v1",
        "period": {"since": since.isoformat(), "until": until.isoformat(), "timezone": DEFAULT_TIMEZONE},
        "count": len(records),
        "personal_data_redactions": redactions,
        "designs": cleaned_records,
    }
    artifact.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")

    items = []
    for record in records:
        design = record.get("design") if isinstance(record.get("design"), dict) else {}
        quantities = design.get("quantities") if isinstance(design.get("quantities"), dict) else {}
        items.append({
            "id": record.get("id"),
            "created_at": record.get("createdAt"),
            "updated_at": record.get("updatedAt"),
            "product_id": design.get("productId"),
            "mode": design.get("mode"),
            "layer_count": len(design.get("layers") or []),
            "preview_count": len(design.get("previews") or []),
            "surface_count": len(design.get("surfaces") or []),
            "quantity_total": sum(value for value in quantities.values() if isinstance(value, int)),
        })
    return {
        "ok": True,
        "period": export["period"],
        "count": len(records),
        "personal_data_redactions": redactions,
        "artifact": str(artifact),
        "items": items,
    }


def get_config() -> dict[str, str]:
    config = {
        "shop": os.getenv("SHOPIFY_SHOP", DEFAULT_SHOP).strip(),
        "client_id": os.getenv("SHOPIFY_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("SHOPIFY_CLIENT_SECRET", "").strip(),
        "api_version": os.getenv("SHOPIFY_API_VERSION", DEFAULT_API_VERSION).strip(),
    }
    missing = [key for key in ("client_id", "client_secret") if not config[key]]
    if missing:
        fail("missing environment variables: " + ", ".join(f"SHOPIFY_{key.upper()}" for key in missing))
    return config


def fetch_token(config: dict[str, str]) -> tuple[str, set[str]]:
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        }
    ).encode("utf-8")
    request = Request(
        f"https://{config['shop']}/admin/oauth/access_token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    payload = request_json(request)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Shopify token response did not include an access token")
    scopes = {scope.strip() for scope in str(payload.get("scope", "")).split(",") if scope.strip()}
    return str(token), scopes


def graphql(config: dict[str, str], token: str, query: str) -> dict:
    request = Request(
        f"https://{config['shop']}/admin/api/{config['api_version']}/graphql.json",
        data=json.dumps({"query": query}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shopify-Access-Token": token,
        },
    )
    return request_json(request, timeout=90)


def ql_field(alias: str, query: str) -> str:
    encoded = json.dumps(query, ensure_ascii=False)
    return (
        f"{alias}: shopifyqlQuery(query: {encoded}) {{ "
        "tableData { columns { name dataType displayName } rows } parseErrors }"
    )


def period(start: date, end: date) -> str:
    return f"SINCE {start.isoformat()} UNTIL {end.isoformat()}"


def build_queries(as_of: date, include_current_day: bool = False) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    end = as_of - timedelta(days=1)
    periods = {
        "yesterday": {"start": end, "end": end},
        "previous_day": {"start": end - timedelta(days=1), "end": end - timedelta(days=1)},
        "last_7_days": {"start": end - timedelta(days=6), "end": end},
        "previous_7_days": {"start": end - timedelta(days=13), "end": end - timedelta(days=7)},
        "last_30_days": {"start": end - timedelta(days=29), "end": end},
        "previous_30_days": {"start": end - timedelta(days=59), "end": end - timedelta(days=30)},
    }
    if include_current_day:
        periods["today_to_now"] = {"start": as_of, "end": as_of}
    serialized = {
        name: {"start": value["start"].isoformat(), "end": value["end"].isoformat()}
        for name, value in periods.items()
    }
    queries: dict[str, str] = {}
    sales_metrics = "total_sales, net_sales, orders, average_order_value"
    session_metrics = (
        "sessions, online_store_visitors, pageviews, average_session_duration, bounce_rate, "
        "sessions_with_cart_additions, sessions_that_reached_checkout, "
        "sessions_that_completed_checkout, conversion_rate"
    )
    for name, value in periods.items():
        window = period(value["start"], value["end"])
        queries[f"sales_{name}"] = f"FROM sales SHOW {sales_metrics} {window}"
        queries[f"sessions_{name}"] = (
            f"FROM sessions SHOW {session_metrics} "
            f"WHERE human_or_bot_session = 'human' {window}"
        )

    window_30 = period(periods["last_30_days"]["start"], periods["last_30_days"]["end"])
    previous_30 = period(periods["previous_30_days"]["start"], periods["previous_30_days"]["end"])
    queries.update(
        {
            "campaigns_30d": (
                "FROM campaign_sessions SHOW campaign_sessions, campaign_online_store_visitors, "
                "campaign_pageviews, campaign_sessions_with_cart_additions, "
                "campaign_sessions_that_reached_checkout, campaign_sessions_that_completed_checkout, "
                f"campaign_conversion_rate {window_30}"
            ),
            "campaigns_previous_30d": (
                "FROM campaign_sessions SHOW campaign_sessions, campaign_online_store_visitors, "
                "campaign_pageviews, campaign_sessions_with_cart_additions, "
                "campaign_sessions_that_reached_checkout, campaign_sessions_that_completed_checkout, "
                f"campaign_conversion_rate {previous_30}"
            ),
            "customers_30d": (
                "FROM customers SHOW new_customer_records, total_number_of_orders, total_amount_spent "
                f"{window_30}"
            ),
            "customers_previous_30d": (
                "FROM customers SHOW new_customer_records, total_number_of_orders, total_amount_spent "
                f"{previous_30}"
            ),
            "payments_30d": (
                "FROM payments SHOW gross_payments, refunded_payments, net_payments, "
                f"orders_with_transactions, transactions {window_30}"
            ),
            "payments_previous_30d": (
                "FROM payments SHOW gross_payments, refunded_payments, net_payments, "
                f"orders_with_transactions, transactions {previous_30}"
            ),
            "inventory_30d": (
                "FROM inventory SHOW ending_inventory_units, ending_inventory_value, "
                f"inventory_units_sold, sell_through_rate, days_out_of_stock {window_30}"
            ),
            "profitability_30d": (
                "FROM profitability SHOW average_cost_of_goods_sold, average_revenue_before_returns, "
                f"average_profit_at_delivery_before_returns {window_30}"
            ),
            "landing_pages_30d": (
                "FROM sessions SHOW sessions, pageviews, average_session_duration, conversion_rate "
                "WHERE human_or_bot_session = 'human' GROUP BY landing_page_path "
                f"{window_30} ORDER BY sessions DESC LIMIT 15"
            ),
            "referrers_30d": (
                "FROM sessions SHOW sessions, online_store_visitors, conversion_rate "
                "WHERE human_or_bot_session = 'human' GROUP BY referrer_domain "
                f"{window_30} ORDER BY sessions DESC LIMIT 15"
            ),
            "products_30d": (
                "FROM sales SHOW net_sales, orders, quantity_ordered GROUP BY product_title "
                f"{window_30} ORDER BY net_sales DESC LIMIT 15"
            ),
            "channels_30d": (
                "FROM sales SHOW total_sales, net_sales, orders GROUP BY sales_channel "
                f"{window_30} ORDER BY total_sales DESC LIMIT 10"
            ),
        }
    )
    return serialized, queries


def run_report(
    config: dict[str, str], token: str, scopes: set[str], as_of: date,
    include_current_day: bool = False,
) -> dict:
    if "read_reports" not in scopes:
        raise RuntimeError("the refreshed token does not include read_reports")
    periods, queries = build_queries(as_of, include_current_day)
    fields = " ".join(ql_field(alias, query) for alias, query in queries.items())
    payload = graphql(config, token, f"query ShopifyDailyAnalytics {{ {fields} }}")
    if payload.get("errors"):
        raise RuntimeError("GraphQL errors: " + json.dumps(payload["errors"], ensure_ascii=False)[:1600])
    data = payload.get("data") or {}
    sections = {}
    for alias, original_query in queries.items():
        value = data.get(alias) or {}
        table = value.get("tableData") or {}
        sections[alias] = {
            "query": original_query,
            "columns": table.get("columns") or [],
            "rows": table.get("rows") or [],
            "parse_errors": value.get("parseErrors") or [],
        }
    return {
        "ok": True,
        "generated_at": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(),
        "store": config["shop"],
        "store_timezone_basis": DEFAULT_TIMEZONE,
        "api_version": config["api_version"],
        "read_reports_granted": True,
        "periods": periods,
        "current_day_is_partial": include_current_day,
        "current_day_cutoff": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat() if include_current_day else None,
        "sections": sections,
    }


def parse_as_of(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report", help="collect complete-day Shopify analytics")
    report.add_argument("--as-of", help="report run date in YYYY-MM-DD; defaults to Asia/Shanghai today")
    daily = subparsers.add_parser("daily", help="collect Shopify analytics and the previous day's POD designs")
    daily.add_argument("--as-of", help="report run date in YYYY-MM-DD; defaults to Asia/Shanghai today")
    daily.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR)
    daily.add_argument(
        "--include-current-day", action="store_true",
        help="also collect today's live data from 00:00 through execution time",
    )
    pod = subparsers.add_parser("pod-designs", help="export the previous complete day's POD design JSON")
    pod.add_argument("--as-of", help="report run date in YYYY-MM-DD; defaults to Asia/Shanghai today")
    pod.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR)
    subparsers.add_parser("probe", help="verify authentication and read_reports")
    args = parser.parse_args()

    try:
        as_of = parse_as_of(getattr(args, "as_of", None))
        if args.command == "pod-designs":
            result = collect_pod_designs(as_of, args.artifact_dir)
        else:
            config = get_config()
            token, scopes = fetch_token(config)
        if args.command == "probe":
            result = {
                "ok": True,
                "store": config["shop"],
                "api_version": config["api_version"],
                "read_reports_granted": "read_reports" in scopes,
            }
        elif args.command == "report":
            result = run_report(config, token, scopes, as_of)
        elif args.command == "daily":
            result = run_report(config, token, scopes, as_of, args.include_current_day)
            result["pod_designs"] = collect_pod_designs(as_of, args.artifact_dir)
            if args.include_current_day:
                result["pod_designs_today_to_now"] = collect_pod_designs(
                    as_of, args.artifact_dir, current_day=True
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError) as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
