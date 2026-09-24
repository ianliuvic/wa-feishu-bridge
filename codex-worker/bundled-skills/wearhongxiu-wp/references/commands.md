# Command reference

Run commands from the `wearhongxiu-wp` skill directory.

## Connection and discovery

```powershell
python scripts/wp.py info
python scripts/wp.py routes --namespace wp/v2
python scripts/wp.py routes --namespace hx/v1
python scripts/wp.py routes --namespace wpcode-remote-api/v1
```

## Read data

Routes are relative to `/wp-json`; both `/wp/v2/pages` and
`/wp-json/wp/v2/pages` are accepted.

```powershell
python scripts/wp.py get /wp/v2/pages --param per_page=20 --param search=POD
python scripts/wp.py get /wp/v2/pages/123 --param context=edit --fields id,status,link,title.rendered
python scripts/wp.py get /hx/v1/pod-products/by-external-id/EXTERNAL_ID
python scripts/wp.py get /wp/v2/media --all --param search=mockup --fields id,slug,source_url
```

Use `--all` only for list endpoints that expose WordPress pagination headers.

## Product variant labels

Correct a wrong or unreadable colour variant name. This writes a collector
override (so re-captures cannot revert it) and the live WordPress product:

```powershell
python scripts/variant_rename.py inspect SKG166
python scripts/variant_rename.py apply SKG166 --source 9007 --label "Tropical Palm Print"
python scripts/variant_rename.py apply SKG166 --source 9007 --label "Tropical Palm Print" --apply
python scripts/variant_rename.py apply SKG166 --source 9007 --label "Tropical Palm Print" --apply --purge-cache
python scripts/variant_rename.py revert SKG166 --source 9007 --apply
```

`inspect` is read-only. `apply` and `revert` are dry runs until `--apply` is
passed. Read [variant-rename.md](variant-rename.md) before using them.

## Create or update data

```powershell
python scripts/wp.py post /wp/v2/pages/123 --data-json '{"status":"draft"}'
python scripts/wp.py post /hx/v1/pod-products/sync --data-file C:\path\payload.json
```

Use `--data-file` for substantial JSON so shell quoting cannot corrupt it.

## WPCode snippets

```powershell
python scripts/wp.py get /wpcode-remote-api/v1/health
python scripts/wp.py get /wpcode-remote-api/v1/snippets --param per_page=100 --fields id,title,status,code_type,location
python scripts/wp.py get /wpcode-remote-api/v1/snippets/123
python scripts/wp.py post /wpcode-remote-api/v1/snippets --data-file C:\path\snippet.json
python scripts/wp.py post /wpcode-remote-api/v1/snippets/123 --data-file C:\path\snippet-update.json
python scripts/wp.py post /wpcode-remote-api/v1/snippets/123/activate
python scripts/wp.py post /wpcode-remote-api/v1/snippets/123/deactivate
```

The live namespace is `wpcode-remote-api/v1`. Read and preserve the existing
snippet before updating it. A code update can return the snippet to draft;
activate it explicitly only after checking the response.

## Delete data

```powershell
python scripts/wp.py delete /wp/v2/posts/123
python scripts/wp.py delete /wp/v2/posts/123 --param force=true
```

The second form is permanent and requires explicit user authorization.

## Media

```powershell
python scripts/wp.py media upload C:\path\mockup.png --title "POD mockup" --alt-text "POD product mockup"
```

## POD conveniences

```powershell
python scripts/wp.py pod schema
python scripts/wp.py pod get EXTERNAL_ID
python scripts/wp.py pod sync C:\path\pod-product.json
```

`pod sync` changes only the WordPress POD record through `hx/v1`; it does not
publish or mutate the shared POD backend.

## Hostinger cache

```powershell
python scripts/hostinger.py cache-purge
python scripts/hostinger.py cache-purge --directory public_html
```

This clears origin and CDN cache for the configured Wearhongxiu site.

## Google Search Console

```powershell
node scripts/gsc.mjs info
node scripts/gsc.mjs overview --days 28
node scripts/gsc.mjs performance --days 28 --dimensions page --limit 1000
node scripts/gsc.mjs performance --days 28 --dimensions query,page --limit 1000
node scripts/gsc.mjs performance --page https://wearhongxiu.com/example/ --dimensions query
node scripts/gsc.mjs inspect https://wearhongxiu.com/example/
node scripts/gsc.mjs sitemap
node scripts/gsc.mjs audit --max-age-days 7 --max-refresh 200 --workers 3
node scripts/gsc.mjs cache
```

These commands are read-only. `audit` refreshes only missing or expired URL
Inspection records. Add `--details` when individual URL records are needed.
Cached output must be reported with its update time.

Submitting the configured sitemap changes GSC state and requires explicit user
authorization:

```powershell
node scripts/gsc.mjs submit-sitemap --yes
```

Read [gsc.md](gsc.md) before interpreting coverage problems or running a large
inspection audit.

## Google Analytics 4

```powershell
node scripts/ga4.mjs info
node scripts/ga4.mjs metadata --output C:\path\ga4-metadata.json
node scripts/ga4.mjs report --dimensions hostName,sessionDefaultChannelGroup --metrics sessions,activeUsers,keyEvents --days 28
node scripts/ga4.mjs report --dimensions pagePathPlusQueryString --metrics screenPageViews,activeUsers,userEngagementDuration --hostname wearhongxiu.com --days 28
node scripts/ga4.mjs realtime --hostname wearhongxiu.com
node scripts/ga4.mjs full-report --days 28 --output C:\path\ga4-full-report.json
```

The script is read-only and locked to property `512505206`. Always separate
WordPress and Shopify with the `hostName` dimension or an exact hostname
filter. Read [ga4.md](ga4.md) before producing a management report.

## SEO audit and optimization planning

Read-only discovery and auditing:

```powershell
python scripts/seo.py capabilities
python scripts/seo.py inventory --output C:\path\wearhongxiu-seo-inventory.json
python scripts/seo.py audit --url https://wearhongxiu.com/
python scripts/seo.py audit --output C:\path\wearhongxiu-seo-audit.json
python scripts/seo.py cwv https://wearhongxiu.com/ --strategy mobile
```

Keyword framework and change plans:

```powershell
python scripts/seo.py keyword-map-template --output C:\path\keyword-map.json
python scripts/seo.py keyword-map-validate C:\path\keyword-map.json
python scripts/seo.py plan-template --keyword-map C:\path\keyword-map.json --output C:\path\seo-plan.json
python scripts/seo.py plan-validate C:\path\seo-plan.json
```

Schema generation and validation:

```powershell
python scripts/seo.py schema-build --type Article --data C:\path\article-data.json --output C:\path\article-schema.json
python scripts/seo.py schema-validate C:\path\article-schema.json
```

`apply-plan` changes live WordPress data. Use it only after explicit approval of
the exact plan and pass every scope flag reported by `plan-validate`:

```powershell
python scripts/seo.py apply-plan C:\path\seo-plan.json --yes --allow-content --allow-media --allow-schema --allow-index-control
python scripts/seo.py history
```

The apply command backs up current data, rejects stale plans, reads values back,
and records the result. It does not purge cache or submit a sitemap.

Read [seo.md](seo.md) before building a site-wide keyword map, interpreting an
audit, or preparing a live optimization plan.

## Wearhongxiu website RAG

```powershell
python scripts/rag_sync.py inventory
python scripts/rag_sync.py query "what is your moq" --debug
python scripts/rag_sync.py homepage-check "what is your moq" --repeat 2
python scripts/rag_sync.py sync --workers 4
python scripts/rag_sync.py sync-post POST_ID
python scripts/rag_sync.py rebuild --yes --workers 3
```

`inventory`, `query`, and `homepage-check` are read-only. `query` uses the
dedicated website RAG with the same external-only filter as the homepage;
`homepage-check` calls the same WordPress route as the homepage module. `sync`
creates, replaces, and removes only documents whose source key starts with
`wearhongxiu:`.
`rebuild --yes` clears the current Hongxiu website RAG after saving a local JSON
backup, then imports published WordPress posts/pages together with `llms.txt`.
It excludes `robots.txt`, never reads WooCommerce/POD product endpoints, and
never accesses the separate product RAG.

Read [rag-operations.md](rag-operations.md) for architecture, performance
diagnosis, optimization invariants, and the authorized Backend-only deployment
workflow.

## End-to-end post workflow

```powershell
python scripts/post_workflow.py template "TOPIC" --output C:\path\post-package.json
python scripts/post_workflow.py generate-image C:\path\post-package.json --yes
python scripts/post_workflow.py validate C:\path\post-package.json
python scripts/post_workflow.py validate C:\path\post-package.json --live
python scripts/post_workflow.py apply C:\path\post-package.json --yes
python scripts/post_workflow.py apply C:\path\post-package.json --yes --publish --sync-rag
```

`generate-image` consumes one paid Crun task and updates the local package;
`validate --live` is read-only. `apply` changes live WordPress and requires
authorization plus `--yes`; without `--publish` it creates a draft. Read
[post-publishing.md](post-publishing.md) before using this workflow.

## End-to-end Industry News workflow

Read-only discovery selects a recent, relevant, non-duplicate story and makes
no WordPress, image-generation or RAG mutation:

```powershell
python scripts/industry_news.py discover
python scripts/industry_news.py discover --max-age-days 14 --limit 20 --body-limit 8
python scripts/industry_news.py discover --keyword 'recycled polyester'
python scripts/industry_news.py discover --source-url https://example.com/report --source-title "Report title" --source-publisher "Publisher" --source-published-at 2026-09-10
```

An explicitly authorized Industry News creation request runs the complete
workflow. It publishes only when all relevance, freshness, duplicate, content,
image, metadata and live validation gates pass:

```powershell
python scripts/industry_news.py publish --yes
```

If no suitable story exists, the command returns `status: skipped` and changes
nothing. It tries a suitable explicitly licensed source image first and otherwise consumes
at most one Crun image task. The resulting post is forced into the
`industry-news` category, verified, and synced to the website RAG. Social
publishing is not part of this command. Read [industry-news.md](industry-news.md)
before running it.

Discovery defaults to 14 days and multiple business, recycling, supply-chain,
performance-textile and resortwear/beachwear queries. `--keyword` is an optional
single-query override. Both commands accept `--body-limit` (1–8, default 8)
and all four `--source-*` options above; explicit sources require a recent ISO
date and retain all editorial gates. Discovery reads full sources through Jina
and uses the configured LLM for strict body review. Audit output separates
`raw_fetched`, `query_counts`, `broad_candidates`, `body_evaluated`, `qualified`
and rejection reasons. Unknown-license media use Crun; only CC0, CC BY,
CC BY-SA, public domain or publisher-authorized images with evidence qualify.
