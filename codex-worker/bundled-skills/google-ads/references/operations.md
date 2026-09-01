# Google Ads operations helper

The helper reuses the credentials configured under `[mcp_servers.google-ads-mcp.env]` in `%USERPROFILE%\.codex\config.toml`. It never prints credential values.

Run it with the Python environment already used by the MCP server:

```powershell
& 'E:\cc\市场营销\google ads\.venv-google-ads-mcp\Scripts\python.exe' `
  'C:\Users\liuyi\.codex\skills\google-ads\scripts\google_ads_ops.py' check
```

## Read commands

List directly accessible customers:

```powershell
...\python.exe ...\google_ads_ops.py accounts
```

Run GAQL and return JSON:

```powershell
...\python.exe ...\google_ads_ops.py query `
  --customer-id 1234567890 `
  --gaql 'SELECT campaign.id, campaign.name, campaign.status FROM campaign LIMIT 50'
```

Prefer the MCP metadata tool before authoring GAQL. Query output may contain customer data; save it only when the user asks for an export.

## Update spec

Supported operation types:

- `campaign.update`: `name`, `status`, `start_date`, `end_date`, `tracking_url_template`, `final_url_suffix`
- `campaign_budget.update`: `name`, `amount_micros`, `delivery_method`
- `campaign_asset.update`: `status`
- `ad_group.update`: `name`, `status`, `cpc_bid_micros`, `tracking_url_template`, `final_url_suffix`
- `ad_group_ad.update`: `status`
- `ad_group_criterion.update`: `status`, `cpc_bid_micros`, `negative`
- `customer_conversion_goal.update`: `biddable`

Example `change.json`:

```json
{
  "customer_id": "1234567890",
  "operations": [
    {
      "type": "campaign.update",
      "resource_name": "customers/1234567890/campaigns/4567890123",
      "fields": {"status": "PAUSED"}
    }
  ]
}
```

Validate without changing the account:

```powershell
...\python.exe ...\google_ads_ops.py mutate --spec C:\path\change.json
```

Apply after explicit authorization:

```powershell
...\python.exe ...\google_ads_ops.py mutate --spec C:\path\change.json --confirm-write
```

Budget, bid, or `ENABLED` changes also require `--confirm-live-change`. Amounts ending in `_micros` use one million micros per account currency unit. Never infer the currency.

Successful writes append a credential-free JSON record to `%USERPROFILE%\.codex\google-ads\mutation-audit.jsonl`. A timeout or transport failure is ambiguous: read the resource before deciding whether any retry is appropriate.

## Complete paused Search campaigns

Use `scripts/create_search_campaign.py` with a reviewed JSON specification when a complete Search campaign must be created. It builds the budget, paused campaign, location and language criteria, campaign negatives, paused ad group, paused keywords, paused RSA, sitelinks, callouts, and structured snippet in one atomic `GoogleAdsService.mutate` request.

Omit `--confirm-write` for API validation. Add it only after the user explicitly authorizes the complete payload. The campaign, ad group, positive keywords, and RSA are required to be `PAUSED`. Campaign asset links are created as `ENABLED` so they appear in the campaign's Assets view; they cannot serve while the campaign is paused.

## Campaign image assets

Use `scripts/upload_campaign_images.py` with a reviewed JSON specification to upload local JPG/PNG files and link them to a paused Search campaign. Search image assets use the `AD_IMAGE` field type; the image dimensions determine whether Google treats each asset as square or landscape. The script refuses to run unless the target campaign is paused, validates atomically when `--confirm-write` is omitted, and links uploaded images as enabled assets so they remain visible while the campaign itself stays paused.

## Responsive Search Ad headlines

Use `scripts/update_rsa_headlines.py` with a reviewed JSON specification to replace all 15 RSA headlines while preserving descriptions, URLs, paths, status, and campaign assets. The script enforces Google headline limits, rejects duplicate headlines and common B2B terms, and refuses to update unless the campaign, ad group, and ad are all paused. Omit `--confirm-write` for API validation.

To inspect Google's current RSA assessment, query:

- `ad_group_ad.ad_strength` and `ad_group_ad.action_items`
- `recommendation.type`, including RSA improvement or asset recommendations when present
- `ad_group_ad_asset_view.performance_label` for individual headline/description performance

The Google Ads web editor and API do not always update simultaneously. A web editor score such as `POOR` may coexist briefly with API `PENDING`; report the API value exactly and identify the synchronization caveat. `NOT_APPLICABLE` asset labels are expected before delivery data accumulates.

## Activate a complete Search campaign

Use `scripts/activate_search_campaign.py` only after explicit authorization to begin delivery. It verifies the reviewed daily budget, approved RSA, paused campaign/ad group/ad, and exact positive-keyword set, then atomically enables the campaign, ad group, positive keywords, and RSA. Omit `--confirm-write` for validation; live activation requires both `--confirm-write` and `--confirm-live-change`.

Before activating, state the account currency, timezone, average daily budget, bid strategy/default bid, schedule, targeting, policy status, and the exact objects that will become enabled. Google may spend more than the average daily budget on an individual day while applying its monthly charging limit; do not describe the configured amount as a hard per-day cap.
