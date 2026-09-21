# Google Search Console

Read this reference when checking Wearhongxiu indexing, organic search
performance, sitemap status, or page-level Google coverage.

## Boundary and configuration

`scripts/gsc.mjs` only accepts `wearhongxiu.com` and `www.wearhongxiu.com` URLs.
It uses the existing Google service account without printing its private key or
access token. Optional settings in `config.env` are:

```text
WEARHONGXIU_GSC_CREDENTIALS=C:\path\service-account.json
WEARHONGXIU_GSC_SITE_URL=https://wearhongxiu.com/
WEARHONGXIU_GSC_SITEMAP_URL=https://wearhongxiu.com/sitemap_index.xml
```

Without an explicit credential path, the script discovers the existing service
account in the legacy Wearhongxiu connector. Do not copy credentials into
reports or commits.

Search Analytics, site/sitemap reads, URL Inspection, cache reads, and audits
are read-only. `submit-sitemap --yes` changes Search Console state and requires
explicit user authorization immediately before execution.

## Interpreting results

- Search Analytics reports clicks, impressions, CTR, and position. Google
  delays these figures, so commands exclude the latest two days.
- URL Inspection is the source of truth for an individual URL but consumes API
  quota. Do not repeatedly refresh the same page.
- `audit` reads the sitemap, reuses the local cache, and refreshes only missing
  or expired inspections. One run is capped at 200 live inspections by default.
- `cache` makes no Google API call. Always report `cache_updated_at`; never
  present cached coverage as current data.
- “Discovered - currently not indexed” and “Crawled - currently not indexed”
  require different SEO investigation. Do not treat every non-indexed URL as a
  technical error or submit it repeatedly.
- This skill does not use Google's Indexing API, which is not intended for
  ordinary article/page submission. Use sitemaps and normal crawling.

For a site-wide SEO audit, map GSC URLs back to live WordPress IDs, post types,
canonical URLs, titles, statuses, and modified dates before proposing edits.
