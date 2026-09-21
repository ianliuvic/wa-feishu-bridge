# Industry News publishing

Use this workflow when the user asks to create or publish an Industry News
article. A direct request such as `创建 industry news 文章` authorizes one
end-to-end live run, including publication, targeted website-RAG sync, and at
most one paid Crun image task when a suitable source image cannot be used. It
does not authorize social publishing.

## Editorial gates

- Search English-language news from the last 14 days by default (range 1–14).
  Query groups cover swimwear business; recycled nylon/polyester/elastane/
  spandex and textile recycling; apparel/garment supply chains and tariffs;
  performance/stretch/UV/chlorine-resistant/waterproof-breathable textiles;
  and resortwear/beachwear markets. `--keyword` overrides these with one query.
- Deduplicate raw results across queries by normalized URL. The inexpensive
  title/summary gate removes obvious celebrity, gossip, deals and promotions;
  it does not require swimwear wording or establish publication eligibility.
- For up to eight top broad candidates, check local and WordPress duplicates,
  read the full source through Jina Reader, recheck the resolved URL, and use
  the configured LLM for strict body evaluation. Its JSON contains `qualified`,
  `swimwear_relevance`, `industry_value`, `supported_facts`, `risks`, and `reason`.
  Require reliable reporting, two distinct supported facts, and a specific,
  non-forced swimwear decision signal for materials, sourcing, manufacturing,
  quality, cost, compliance, retail or collection planning. Generic textile
  relevance is insufficient. Stop at the first qualified story. Retry once
  only for incomplete evaluator structure, never for a complete rejection.
- Audit fields are `raw_fetched` (all returned rows before deduplication),
  `query_counts` (returned rows per query), `broad_candidates` (all unique
  fresh broad survivors before `--limit`), `body_evaluated` (candidate bodies
  submitted, excluding retry calls), `qualified`, and concise `rejected` reasons.
  Explicit sources bypass NewsAPI, so raw/query counts remain zero.
- Compare the normalized source URL and event/title similarity with both the
  local completed-publication history and existing WordPress posts in the
  `industry-news` category.
- Never reuse the same source article or substantially the same news event.
- If no recent, relevant, non-duplicate candidate exists, return `skipped` and
  publish nothing. Do not loosen the gates merely to fill a schedule.
- If discovery alone was requested, run `discover`; it must not draft, generate
  an image, upload media, publish, or update RAG.

## Article construction

1. Read the selected source through Jina Reader and treat it as factual input,
   not as prose to paraphrase. Attribute and link the original publisher.
2. Read current Wearhongxiu context and inspect live pages/posts for relevant
   internal-link candidates.
3. Produce an original English B2B analysis for swimwear brand owners and
   sourcing teams. Explain practical implications for product decisions,
   materials, manufacturing, cost, quality or collection planning without
   inventing claims. Pass strict evaluation facts and risks into drafting and
   preserve them through Humanizer. For adjacent technology explicitly say it
   is a technology for swimwear teams to watch, not already proven or
   commercially used in swimwear. Store the evaluation in the post package.
4. Apply the `humanizer` draft-audit-final loop after the full first draft.
5. Include 3 to 5 contextual links to canonical Wearhongxiu pages, one source
   attribution link, Yoast title/description/focus keyword, and BlogPosting
   schema settings. Do not add an H1 inside post content.
6. Force the WordPress category slug `industry-news`; do not publish into the
   normal Blog category as a substitute.

## Image policy

- Before inspecting a source image, require an explicit license of CC0,
  CC BY, CC BY-SA, public domain, or publisher-authorized, with recorded
  evidence in `image_license_evidence` and the label in `image_license`.
  Ordinary commercial-media and NewsAPI images have unknown licenses and
  must use the existing Crun fallback. Attribution alone is insufficient.
  Licensed images must also pass download and visual relevance checks.
  Store license evidence, source URL and publisher credit in the package.
- If the image is missing, inaccessible, irrelevant, dominated by a logo/text,
  or unsuitable for reuse, call the `crun-imagen` workflow once to create a
  content-specific 16:9 editorial image.
- Write factual image alt text. Upload the chosen image once, insert it at the
  package marker in the body, and reuse that media item as the featured image.

## Publish and recovery

Validate the local package and live WordPress dependencies before mutation.
Publishing is checkpointed after media upload and draft creation so a timeout
can resume without duplicating media or posts. Publish, read the result back,
sync only that post into the website RAG, then record the source in Industry
News history. History is written only after both publication and RAG sync
complete.

```powershell
python scripts/industry_news.py discover
python scripts/industry_news.py publish --yes
```

Optional discovery controls:

```powershell
python scripts/industry_news.py discover --max-age-days 14 --limit 20 --body-limit 8
python scripts/industry_news.py publish --max-age-days 14 --limit 20 --body-limit 8 --yes
python scripts/industry_news.py discover --keyword 'recycled polyester'
python scripts/industry_news.py discover --source-url https://example.com/report --source-title "Report title" --source-publisher "Publisher" --source-published-at 2026-09-10
```

Both `discover` and `publish` support all four `--source-*` options. An explicit
URL requires title, publisher and a recent ISO publication date; it bypasses
search but retains the broad, freshness, duplicate and strict body gates.
`--limit` caps ranked broad candidates retained (default 20); `--body-limit`
caps candidates checked for body review (default/max 8, minimum 1).
Discovery uses Jina and LLM quota but never drafts or mutates WordPress/RAG.
