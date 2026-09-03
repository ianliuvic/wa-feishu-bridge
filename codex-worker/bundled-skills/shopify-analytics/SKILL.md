---
name: shopify-analytics
description: Analyze the Wearhongxiu Shopify store through ShopifyQL, including sales, traffic, conversion funnels, acquisition, customers, inventory, payments, and profitability. Use for Shopify performance reports, daily business reviews, anomaly detection, and evidence-based growth recommendations; do not use for changing store data.
---

# Shopify Analytics

Produce decision-ready reporting for `shop.wearhongxiu.com`, not a raw metric dump.

## Data collection

Run the deterministic collector before analyzing:

```bash
python3 /root/.codex/skills/shopify-analytics/scripts/shopify_analytics.py report
```

It requests a fresh client-credentials token, verifies `read_reports`, and returns complete-day comparisons plus sales, sessions, funnel, landing pages, referrers, campaign attribution, customers, inventory, payments, profitability, products, and channels. Credentials come from `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, and `SHOPIFY_CLIENT_SECRET`; `SHOPIFY_API_VERSION` defaults to `2026-07`. Never print tokens or secrets.

Use `probe` only for a connectivity check:

```bash
python3 /root/.codex/skills/shopify-analytics/scripts/shopify_analytics.py probe
```

## Daily report standard

Base daily reporting on complete store-calendar days. Compare:

- yesterday versus the preceding day;
- the latest 7 complete days versus the previous 7;
- the latest 30 complete days versus the previous 30.

Write the report in Chinese and include:

1. **Executive verdict:** three to five sentences stating what changed, why it matters, and the most important next move.
2. **KPI scorecard:** sales, orders, AOV, visitors, sessions, pageviews, bounce rate, average session duration, add-to-cart, checkout, purchase, and conversion rate. Show absolute values and period-over-period changes; label division-by-zero changes as `新增` or `无可比基数` rather than inventing a percentage.
3. **Funnel diagnosis:** calculate rates between session → add to cart → checkout → purchase and identify the largest economically meaningful leak.
4. **Acquisition and content:** interpret referrer domains, landing pages, sales channels, and Shopify-recognized campaign sessions. Separate observed correlation from attribution.
5. **Commerce quality:** product sales, refunds/net payments, customer acquisition, inventory, and profitability.
6. **Data-quality warnings:** explicitly flag missing unit costs, zero campaign attribution despite paid referrers, refunds that make sales/net sales diverge, sparse samples, or unavailable schemas. Never treat missing data as zero unless Shopify returned zero.
7. **Prioritized actions:** give at most five actions, ordered by expected impact. Each action must cite the metric that motivates it, an owner or workstream, and what to check in the next report.

Use percentages correctly: ShopifyQL returns ratios such as `0.577` for 57.7%. Convert durations from seconds to a readable form. Do not expose customer names, emails, addresses, IDs, access tokens, or other personal data in reports.

If one section returns a parse or access error, report that limitation and continue with valid sections. If the collector itself fails or `read_reports` is absent, stop and return a concise diagnostic instead of fabricating analysis.
