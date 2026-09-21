#!/usr/bin/env python3
"""Retrieve authorized Meta lead forms and lead records."""

import argparse

from meta_graph import GraphClient, MetaGraphError, print_json, require_object_id


FORM_FIELDS = "id,name,status,created_time,locale,leads_count,follow_up_action_url,privacy_policy_url"
LEAD_FIELDS = "id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,field_data,custom_disclaimer_responses,platform,is_organic"


def checked_limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise MetaGraphError("--limit must be between 1 and 100")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve Meta lead-generation data")
    parser.add_argument("--page-id")
    commands = parser.add_subparsers(dest="command", required=True)
    forms = commands.add_parser("forms", help="list Page lead forms")
    forms.add_argument("--limit", type=int, default=50)
    form = commands.add_parser("form", help="read one lead form")
    form.add_argument("form_id")
    leads = commands.add_parser("leads", help="list leads from a form")
    leads.add_argument("form_id")
    leads.add_argument("--limit", type=int, default=100)
    leads.add_argument("--since")
    leads.add_argument("--until")
    lead = commands.add_parser("lead", help="read one lead")
    lead.add_argument("lead_id")
    args = parser.parse_args()

    try:
        page_id = require_object_id(args.page_id, "META_BUSINESS_PAGE_ID", "Page ID")
        client = GraphClient().page_client(page_id)
        if args.command == "forms":
            print_json(client.get(f"{page_id}/leadgen_forms", {"fields": FORM_FIELDS, "limit": checked_limit(args.limit)}))
        elif args.command == "form":
            form_id = require_object_id(args.form_id, "META_UNUSED", "form ID")
            print_json(client.get(form_id, {"fields": FORM_FIELDS}))
        elif args.command == "leads":
            form_id = require_object_id(args.form_id, "META_UNUSED", "form ID")
            if bool(args.since) != bool(args.until):
                raise MetaGraphError("use --since and --until together")
            params = {"fields": LEAD_FIELDS, "limit": checked_limit(args.limit)}
            if args.since:
                params["filtering"] = [{"field": "time_created", "operator": "GREATER_THAN", "value": args.since}, {"field": "time_created", "operator": "LESS_THAN", "value": args.until}]
            print_json(client.get(f"{form_id}/leads", params))
        else:
            lead_id = require_object_id(args.lead_id, "META_UNUSED", "lead ID")
            print_json(client.get(lead_id, {"fields": LEAD_FIELDS}))
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
