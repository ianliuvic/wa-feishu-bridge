#!/usr/bin/env python3
"""Collect ShopifyQL data for a decision-ready Wearhongxiu business report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_SHOP = "w4ik1r-x5.myshopify.com"
DEFAULT_API_VERSION = "2026-07"
DEFAULT_TIMEZONE = "Asia/Shanghai"


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


def build_queries(as_of: date) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    end = as_of - timedelta(days=1)
    periods = {
        "yesterday": {"start": end, "end": end},
        "previous_day": {"start": end - timedelta(days=1), "end": end - timedelta(days=1)},
        "last_7_days": {"start": end - timedelta(days=6), "end": end},
        "previous_7_days": {"start": end - timedelta(days=13), "end": end - timedelta(days=7)},
        "last_30_days": {"start": end - timedelta(days=29), "end": end},
        "previous_30_days": {"start": end - timedelta(days=59), "end": end - timedelta(days=30)},
    }
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


def run_report(config: dict[str, str], token: str, scopes: set[str], as_of: date) -> dict:
    if "read_reports" not in scopes:
        raise RuntimeError("the refreshed token does not include read_reports")
    periods, queries = build_queries(as_of)
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
    subparsers.add_parser("probe", help="verify authentication and read_reports")
    args = parser.parse_args()

    try:
        config = get_config()
        token, scopes = fetch_token(config)
        if args.command == "probe":
            result = {
                "ok": True,
                "store": config["shop"],
                "api_version": config["api_version"],
                "read_reports_granted": "read_reports" in scopes,
            }
        else:
            result = run_report(config, token, scopes, parse_as_of(args.as_of))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError) as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
