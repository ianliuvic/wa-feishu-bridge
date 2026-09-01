---
name: google-ads
description: Read, audit, report on, and safely modify Google Ads accounts through the configured Google Ads API connection. Use for account discovery, GAQL reporting, campaign performance analysis, status or budget changes, and other Google Ads account operations; use the ads skill instead for platform-agnostic advertising strategy or copy ideation.
---

# Google Ads operations

Use the configured `google-ads-mcp` connection for discovery and ordinary reads. This environment currently uses manager account `5959461756`; never assume it is the advertising customer being changed. Discover accessible accounts and resolve the exact client customer ID first.

## Read workflow

1. Use `customers_list_accessible_customers` when the target customer is unknown.
2. Before composing GAQL with `search_search`, call `metadata_get_resource_metadata` for the resource. Do not guess fields.
3. Use finite date ranges, explicit limits, and customer IDs without hyphens.
4. Separate observed facts from recommendations. Include the account, date range, currency, attribution caveat, and requested business metric in reports.

For command-line fallback or exporting raw query results, use `scripts/google_ads_ops.py`. Read [references/operations.md](references/operations.md) for its commands and supported writes.

## Write workflow

Treat creating, enabling, pausing, deleting, changing bids/budgets, targeting, ads, keywords, or conversion goals as external mutations.

1. Read the exact target resource and its current values immediately before proposing a change.
2. State the intended account, resource, before/after values, monetary units, currency, timezone, schedule, and expected effect.
3. Obtain explicit user authorization for the concrete mutation. Authorization to create this skill is not authorization to modify an ad account.
4. Validate first. For the helper, omit `--confirm-write`; for the MCP conversion-goal tool, set `validate_only=true`.
5. Apply once only after validation succeeds. Use `--confirm-write`; additionally use `--confirm-live-change` for budget, bid, or `ENABLED` changes.
6. Read the resource back and report the result. Do not automatically retry an ambiguous or timed-out mutation because it may already have succeeded.

Default newly created campaigns and ads to `PAUSED`. Never increase spend, broaden targeting, enable delivery, or remove exclusions without explicit approval for those exact effects. Never print, copy, or commit developer tokens, OAuth material, or service-account contents.

Use a file-based JSON spec for nontrivial changes so the proposed payload can be reviewed without secrets. Keep identifiers and resource names in the spec, but no credentials.

## Search campaign lifecycle

Read [references/operations.md](references/operations.md) before creating, editing assets for, evaluating, or activating a Search campaign. Prefer the matching bundled script over composing one-off mutation code:

- `create_search_campaign.py`: atomically create a complete paused Search campaign.
- `upload_campaign_images.py`: upload Search image assets and link them with `AD_IMAGE`.
- `update_rsa_headlines.py`: replace all 15 RSA headlines without changing descriptions or URLs.
- `activate_search_campaign.py`: atomically enable the reviewed campaign stack.
- `google_ads_ops.py`: read-only GAQL fallback and narrowly scoped common updates.

Treat a user request to save a campaign as a draft as a complete campaign whose campaign, ad group, positive keywords, and RSA are `PAUSED`; do not rely on the legacy Campaign Draft product. Keep campaign-level asset links `ENABLED` so sitelinks, callouts, structured snippets, and images remain visible in the paused campaign. Enabled asset links cannot serve while the campaign itself is paused.

For Search campaigns, image assets use `AssetFieldType.AD_IMAGE`; do not use Performance Max field types such as `SQUARE_MARKETING_IMAGE`. Google derives the supported aspect treatment from the uploaded image dimensions.

When evaluating an RSA, read `ad_group_ad.ad_strength`, `ad_group_ad.action_items`, relevant `recommendation` rows, and `ad_group_ad_asset_view.performance_label`. The web editor may calculate suggestions before the API synchronizes: report `PENDING` as pending, not as poor. Asset performance labels remain `NOT_APPLICABLE` until the ad has enough delivery data.

Google Ads budgets are average daily budgets. Before activation, state that an individual day may spend above the average while Google applies its monthly charging limit, and preserve the account currency and timezone in the handoff.

## Scope boundaries

- Use `$ads` for channel strategy and `$ad-creative` for bulk creative writing.
- Use `$analytics` when the requested source of truth is GA4 rather than Google Ads.
- The bundled helper currently supports safe reads and common updates. For an unsupported mutation, add a narrowly scoped operation with validation support instead of sending an improvised generic API payload.
