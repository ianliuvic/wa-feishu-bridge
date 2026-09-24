---
name: wearhongxiu-wp
description: Inspect and manage wearhongxiu.com WordPress, correct wrong or unreadable product variant names, export selected Wearhongxiu products as PDF catalogs, operate website RAG and Google Search Console, and run SEO or end-to-end publishing workflows. Use only for Wearhongxiu; do not use for paintsand.com, Shopify Admin, the shared POD backend, or the separate product RAG.
---

# Wearhongxiu WordPress

Use the bundled scripts from this skill directory. They load only this skill's
configuration and refuse to send credentials to hosts other than
`wearhongxiu.com` or `www.wearhongxiu.com`.

## Boundaries

- This skill operates the Wearhongxiu **WordPress** site. It is separate from
  the generic `wp-rest` skill configured for paintsand.com.
- Do not modify Shopify data or `pod-api.wearhongxiu.com` with this skill. The
  POD backend is shared with Shopify; treat it as read-only context unless the
  user separately and explicitly authorizes backend work.
- Website RAG operations cover only published WordPress posts/pages plus
  `llms.txt`. `robots.txt`, WooCommerce/POD products, and the separate product
  RAG are explicitly out of scope.
- Inspect before changing. Keep unrelated live-site behavior intact.
- Creating, updating, publishing, deleting, activating snippets, sending
  inquiries, creating payments, emailing customers, and purging all cache are
  live mutations. Do them only when the user's request authorizes that action.
- GSC performance reads, URL Inspection, and indexing audits are read-only but
  consume API quota. Sitemap submission changes GSC state and requires explicit
  authorization immediately before running `submit-sitemap --yes`.
- SEO inventory, audit, keyword maps, schema validation, plans, and Core Web
  Vitals checks are read-only. `seo.py apply-plan` changes live WordPress data;
  require approval of the exact plan plus every scope flag reported by
  `plan-validate`.
- Prefer drafts for new editorial content unless the user requests publishing.
- Never print or expose passwords, tokens, Authorization headers, Stripe data,
  customer personal data, or full private inquiry payloads.

## Workflow

1. Run `python scripts/wp.py info` before the first operation in a task to
   verify the canonical site and authenticated account.
2. Discover live routes instead of assuming old local snippets are deployed:
   `python scripts/wp.py routes --namespace hx/v1`.
3. Use `get` for inspection, `post` for creates/updates, `delete` for removals,
   and `media upload` for media. Use JSON files for large payloads.
4. After a mutation, read the affected resource back and report the observable
   result. If frontend output changed and the user requested immediate rollout,
   purge cache with `python scripts/hostinger.py cache-purge`.

## Product catalog export

When the user asks to export selected Wearhongxiu products as a catalog, use
`scripts/catalog_export.py`. Accept any mixture of exact style numbers,
Wearhongxiu product URLs, slugs, and WordPress post IDs. For a long selection,
write the identifiers to a JSON array or newline-delimited text file and pass
it with `--input`; do not substitute title search or silently omit unresolved
products.

The exporter reads current product data through the authenticated exact
resolver and produces the established A4 landscape catalog: cover, production
process, category contents, up to three products per page, and contact page.
It groups products by their primary category and includes the featured image,
style number, English title, material, MOQ, sizes, variants, lowest bulk price,
and live product link. Use the bundled template, cover, and logo assets; do not
depend on the retired local `products.json` project data.

Write final files under `output/pdf/` unless the user provides another output
path. Follow the PDF skill's authoring and QA contract: run its artifact marker
once before the first export in the task, inspect the PDF with `pdfinfo`, render
all pages with `pdftoppm`, and visually check the cover, contents, every product
page, and the contact page before delivery. Report any identifier that cannot
be resolved rather than presenting an incomplete catalog as complete. Exact
command syntax is in [references/commands.md](references/commands.md).

## Product variant labels

When a Wearhongxiu product shows a wrong or unreadable colour variant name — a
bare 1688 merchant code such as `9007`, or source-language text — use
`scripts/variant_rename.py`. Correcting the WordPress product alone is not
durable: the label is regenerated from the captured 1688 SKU options on every
capture, translation refresh, and swatch repair. The command stores a display
override on the collector product (which every publication path then applies)
and writes the corrected label into the live WordPress product without
re-uploading media. `inspect` is read-only; `apply` and `revert` write only with
`--apply`. Read
[references/variant-rename.md](references/variant-rename.md) before using it.

## Website knowledge base

Use `python scripts/rag_sync.py inventory` to inspect the canonical source set.
Use `query "..." --debug` for website-only retrieval/timing diagnosis and
`homepage-check "..."` for the public WordPress proxy used by Ask Hongxiu. Use
`sync` for routine idempotent updates. `rebuild --yes` is destructive: it backs
up the current Hongxiu RAG document payloads, deletes that knowledge base, and
rebuilds it from the website. Never use `rebuild --yes` without explicit
authorization to clear the Hongxiu website RAG.

The sync identity is `wearhongxiu:wordpress:{post_type}:{wp_id}` (or
`wearhongxiu:file:{filename}`). Every document is public/external and stores its
canonical URL, content hash, WordPress identifiers, publish/modified dates, and
source type. Product knowledge remains separate and must not be copied into this
knowledge base.

## Google Search Console

Use `node scripts/gsc.mjs performance` for clicks, impressions, CTR, and
position; `inspect URL` for a live page-level coverage result; and `audit` for a
cached sitemap-wide indexing analysis. Use `cache` when only the last saved
audit is needed and clearly report its timestamp. Never claim cached coverage
is current.

## Google Analytics 4

Use `node scripts/ga4.mjs` for read-only GA4 reporting and read
[references/ga4.md](references/ga4.md) before interpreting results. This
integration is hard-locked to property `512505206`. Always separate
`wearhongxiu.com` (WordPress) from `shop.wearhongxiu.com` (Shopify) by
`hostName`; never blend their traffic, engagement or conversion conclusions.
Use `full-report` for the management dataset and generic `report` for any
compatible standard or custom dimension/metric exposed by GA4 metadata.

## SEO workflow

Use `python scripts/seo.py capabilities` to confirm live Yoast/content/media
fields, `inventory` and `audit` for read-only evidence, `keyword-map-template`
for the site-wide framework, and `plan-validate` before proposing any mutation.
`apply-plan` must save a backup, reject stale `expected_modified` values, limit
changes to authorized scopes, read values back, and append the result to SEO
history. It never implies approval to change URLs, menus, robots, cache, or GSC.

## Post creation and publishing

For a request to turn a topic into a complete normal post, read
[references/post-publishing.md](references/post-publishing.md). Research the
site first to avoid keyword cannibalization, use the `crun-imagen` skill for the
article image, and load the `humanizer` skill after the first complete
draft. Apply its draft-audit-final loop to the title, excerpt, article, SEO
metadata, and social copy before building the final package. A package without
an applied humanizer record must not pass validation. Generate one
content-specific 16:9 image after the final article is ready, write descriptive
alt text, place the image marker at the most relevant point in the body, and
reuse the uploaded media as the post's featured image. Then build a validated
post-package JSON and use
`scripts/post_workflow.py`. The workflow supports verified draft creation or
publishing plus targeted website-RAG ingestion. WordPress publishing, RAG
ingestion, and social distribution are distinct mutations; perform only the
ones authorized by the user. For direct distribution to the authorized
LinkedIn personal account, read
[references/linkedin.md](references/linkedin.md); this workflow does not use
Postiz. Normal article distribution must use LinkedIn's native article preview
card with the WordPress featured image, title, excerpt, and tracked canonical
URL. Never fall back silently to a bare URL in the commentary; the publisher
fails closed when preview inputs are missing.

When a standalone `humanizer` skill is unavailable, read and apply the bundled
[references/humanizer.md](references/humanizer.md) instead. The bundled
`scripts/hongxiu_rag.py` client makes targeted RAG sync self-contained; do not
require a separately installed `hongxiu-rag` skill.

## Industry News creation and publishing

For a request to create or publish Industry News, read
[references/industry-news.md](references/industry-news.md), then use
`scripts/industry_news.py`. A direct instruction such as `创建 industry news
文章` authorizes the complete Industry News run: 14-day multi-query discovery
across swimwear business, recycling, apparel supply chains/tariffs, performance
textiles and resortwear/beachwear markets; a broad title/summary noise gate;
duplicate checks and Jina full-source reading for up to eight candidates;
strict LLM body evaluation requiring two supported facts and a specific
swimwear decision signal; Wearhongxiu-focused analysis carrying facts and risks,
the `humanizer` pass, internal links, Yoast metadata, BlogPosting schema, image
selection, publication in the `industry-news` category, readback, and targeted
website-RAG sync. It authorizes at most one Crun image task only when no
suitable source image with an explicit CC0, CC BY, CC BY-SA, public-domain or
publisher-authorized license and evidence is available; ordinary NewsAPI images
are license unknown. Adjacent technology must be framed as something for
swimwear teams to watch, not proven commercial swimwear use. Audit counts
separate raw results, per-query rows, broad candidates, evaluated bodies and
qualified stories. Both commands support explicit source metadata and
`--body-limit`; `--keyword` optionally replaces the query groups. Retry the
evaluator once only for incomplete structure, never for rejection. It never authorizes social
publishing. If no qualified recent non-duplicate story exists, report
`skipped` and publish nothing.

Command syntax is in [references/commands.md](references/commands.md). Read
[references/rag-operations.md](references/rag-operations.md) when diagnosing
Ask Hongxiu correctness/latency, changing retrieval behavior, or preparing a
RAG Backend deployment. Read
[references/gsc.md](references/gsc.md) when checking Google indexing, organic
performance, sitemap coverage, or preparing a site-wide SEO audit. Read
[references/ga4.md](references/ga4.md) when analyzing traffic, engagement,
content performance, acquisition, lead actions, ecommerce or cross-site flow.
Read
[references/seo.md](references/seo.md) when building keyword architecture,
auditing or changing metadata/content/media/schema, checking Core Web Vitals,
or preparing batch SEO work. Read
[references/post-publishing.md](references/post-publishing.md) when creating,
illustrating, publishing, or distributing a WordPress post from a topic. Read
[references/linkedin.md](references/linkedin.md) when authorizing or publishing
the matching article link to the LinkedIn personal account. Read
[references/industry-news.md](references/industry-news.md) when discovering,
writing, illustrating, publishing, or diagnosing an Industry News post. Read
[references/wearhongxiu-routes.md](references/wearhongxiu-routes.md) when the
request concerns POD products, inquiries, Stripe/sample ordering, WPCode, or
menus/templates. Also read it when resolving a product from a style number,
WordPress product URL, slug, or post ID; use the exact authenticated resolver
documented there instead of title search. Read
[references/variant-rename.md](references/variant-rename.md) when a product's
colour variant name is wrong or unreadable and has to be corrected durably.

## Deployment notes

- REST can update only routes exposed by the live site and permitted for the
  configured WordPress user.
- WPCode management uses the live `/wpcode-remote-api/v1/snippets` bridge.
  Discover it with `routes --namespace wpcode-remote-api/v1`, read and back up
  a snippet before updating it, and read the result back after every mutation.
- Elementor headers and menus may be stored as template post meta rather than
  ordinary menu resources. Inspect live routes and the rendered page before
  deciding how to update them.
- Hostinger cache purge clears server-side cache and CDN cache and is broader
  than an individual-page purge.
