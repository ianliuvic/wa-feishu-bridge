# Wearhongxiu SEO operations

Read this reference for site-wide SEO audits, keyword mapping, on-page plans,
schema work, Core Web Vitals, image metadata, or batch optimization.

## Capability map

- `inventory`: published WordPress pages/posts, Yoast fields, and media metadata.
- `audit`: raw rendered HTML, titles/descriptions/canonicals/robots, H1-H6,
  semantic landmarks, content depth, internal links, possible orphans,
  duplicate metadata, images, JSON-LD, robots crawler rules, and sitemap health.
- `keyword-map-template` and `keyword-map-validate`: one owner page per primary
  keyword, page roles, intent, buyer stage, hierarchy, pillar, and cluster.
- `plan-template` and `plan-validate`: pre-publication validation and explicit
  change scope before any WordPress mutation.
- `schema-build` and `schema-validate`: Organization, Article/BlogPosting,
  Product, FAQPage, and BreadcrumbList JSON-LD.
- `cwv`: PageSpeed Insights field data when available plus mobile/desktop
  Lighthouse lab data.
- `apply-plan`: backup, concurrency check, scoped batch mutation, REST readback,
  and append-only history.
- `gsc.mjs`: Google performance, live URL Inspection, sitemap coverage, and
  cached indexing audits.

## Standard workflow

1. Inspect capabilities and export an inventory.
2. Run the read-only audit and combine it with 90-day GSC page/query data.
3. Build the keyword map before writing page-level metadata. Assign exactly one
   owner URL to each primary keyword; related pages use distinct intent or
   supporting secondary keywords.
4. Create and validate a change plan. Review warnings manually; character
   ranges are review heuristics, not hard ranking rules.
5. Obtain explicit authorization for the exact plan and apply flags.
6. Apply, inspect the backup/readback verification, then run rendered audit and
   GSC checks. Cache purge and sitemap submission remain separate approvals.

Do not optimize live pages merely because an audit was requested.

## Keyword and architecture rules

The map records `level`, `parent_url`, `pillar`, `cluster`, `page_role`,
`search_intent`, `buyer_stage`, `primary_keyword`, and secondary keywords.
Derive it from business services, existing site content, GSC queries/pages, and
real buyer questions. Avoid creating one thin page per keyword variation.

Keep important commercial/service pages within roughly three clicks, connect
hub and spoke content with descriptive anchors, and review pages with no inbound
links from the audited content set. Header menus, Elementor templates, slugs,
redirects, and breadcrumb UI use separate WordPress storage layers; inspect the
live layer before implementing an approved architecture change. The SEO plan
does not silently rename URLs or menus.

## Plan format and mutation boundaries

Each page plan is keyed by `wp_type`, `wp_id`, `url`, and `expected_modified`.
The last field prevents overwriting newer user edits.

Supported `seo` fields are:

- `title`, `description`, `focus_keyword`
- `canonical`, `noindex`, `nofollow`
- `schema_page_type`, `schema_article_type`

Supported `wp` fields are `title`, `excerpt`, and `content`. Media updates accept
`id`, `title`, `alt_text`, `caption`, and `description`.

Applying requires `--yes` and whichever additional scopes the validator lists:

- `--allow-content`
- `--allow-index-control`
- `--allow-media`
- `--allow-schema`

The tool saves a complete backup before its first write and aborts the whole
plan before writing if any page's `expected_modified` value changed.

## Content, headings, and images

- Use one descriptive H1 and logical H2/H3 nesting. Headings describe sections;
  they are not styling hooks or a place to repeat keywords.
- Match search intent and answer the buyer's real question. Do not optimize by
  keyword density or mass-generate near-duplicate pages.
- Image alt text describes visible information for accessibility. Empty alt is
  valid for decorative images. Do not add keywords or Hongxiu unless factual and
  useful. File renaming requires a separate safe media/redirect workflow.
- Check image dimensions, responsive sources, compression/lazy loading, and
  layout stability in addition to alt text.

## Schema and AI/crawler visibility

Schema must match visible page content. Never add FAQ, review, rating, price, or
product availability data that visitors cannot see. Raw-HTML audit detects
server-rendered JSON-LD, but a missing result must be confirmed with a rendered
Rich Results test before acting.

Yoast page/article schema types can be applied through the plan. Arbitrary
`schema_jsonld` is validation-only: choose an authorized Yoast block, WPCode
snippet, or template integration first so it has an owned storage and removal
path. Organization schema is normally global; do not duplicate Yoast's graph.

For AI visibility, use the same helpful public content as human visitors. Favor
semantic HTML, direct section answers, attributed facts, dates, authors,
sources, lists/tables where natural, and consistent entity information. Do not
write a separate AI-only version or fragment pages into citation bait.

The audit reports matching rules for Googlebot, Bingbot, GPTBot,
ChatGPT-User, PerplexityBot, ClaudeBot, anthropic-ai, and Google-Extended.
Training and citation access are business decisions; do not change robots rules
without explicit authorization.

## Measurement and validation

- GSC data is delayed and indexing changes are not immediate. Do not promise a
  ranking or indexation outcome.
- PageSpeed field metrics and Lighthouse lab metrics are different datasets.
  Treat a single lab run as diagnostic, not as historical performance.
- Audit priorities are: crawl/index blockers, technical foundation, on-page
  clarity, content usefulness/E-E-A-T, then authority and links.
- Preserve before/after evidence in the SEO history and GSC cache timestamps.
