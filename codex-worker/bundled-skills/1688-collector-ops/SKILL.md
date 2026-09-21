---
name: 1688-collector-ops
description: Collect 1688 product or shop URLs and operate the Coolify-hosted collector. Use for Chinese requests such as “帮我采集”, “采集并发布”, “同步到 wearhongxiu”, 1688 login recovery, shop scans, product capture, audits, translation, RAG indexing, and wearhongxiu publishing.
---

# 1688 Collector Operations

Use this skill whenever a task involves the remote 1688 collector, its Chromium/noVNC login application, full-shop scans, or extracting a concise overview from a 1688 shop homepage.

## Remote Codex runtime

On the Coolify Codex worker, use `python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py` for collector API calls. It reads `COLLECTOR_API_URL` and `COLLECTOR_API_KEY` from the inherited environment; never print either value. Run `status` before a mutating workflow.

Natural-language routing:

- “采集这个商品” plus a detail URL or offer ID means save the product detail and let the collector enqueue its automatic image/SKU audits and inactive RAG record. Do not publish unless the user also asks to publish or synchronize to wearhongxiu.
- “采集并发布到 wearhongxiu” means run the product pipeline: capture, wait for completion, translate to English, publish with the requested WordPress status, then verify publication and the automatically queued RAG sync. Default to `draft` when the user does not explicitly request a public product; use `publish` only when they clearly ask to上线、发布 or synchronize live.
- A shop homepage request means capture the homepage only. A shop “全部商品” request means run the official-plugin full-shop scan. Do not infer that every listed item should be detail-captured or published unless the user asks for that scope.
- If a request supplies several products or a dated shop subset, use bounded concurrency and report progress in the active conversation. Respect collector queue limits and stop on the model-provider circuit breaker rather than cascading failures.

Useful commands:

```text
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py status
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py logout-login
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py product-capture <URL-or-offer-id>
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py product-pipeline <URL-or-offer-id> --status draft
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py product-resolve <style-no-or-WP-URL>
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py product-style-set <product-detail-id> <style-no>
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py shop-home <shop-homepage-URL>
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py shop-scan <shop-offer-list-URL>
```

The helper emits JSON and returns a nonzero exit code on terminal failures. It does not expose credentials. Use `request` only for endpoints not covered by a dedicated command.

## Application and URLs

- Collector: `1688-dom-collector`, UUID `kc1izz5i6mnhs9z29ckl1ajp`
- Collector URL: https://collector.yiswim.cloud
- Integrated noVNC URL: https://collector.yiswim.cloud/login/vnc.html?autoconnect=1&resize=remote&path=login/websockify

Before any Coolify mutation, read and follow the `coolify` skill, inspect the current application status, and act directly when the user has authorized the operation.

## Persistent storage invariant

Collector and login modes share `/app/storage/browser-profile`. They are mutually exclusive inside the collector application. Never start another Chromium against this profile or bypass the browser-mode API; doing so risks profile locking, corrupted state, and apparent login loss.

## Login and browser switching

Use the existing Bearer API authentication:

- `GET /api/browser-mode` returns the current mode, transition, worker state, and integrated login URL.
- `POST /api/browser-mode/login` pauses new collection work, waits for active browser work, closes the headless collector, and starts visual Chromium plus noVNC.
- `POST /api/browser-mode/collector` saves browser state, closes visual Chromium/noVNC, restarts the headless collector, and resumes the worker.
- `POST /api/browser-mode/logout-login` safely enters login mode, logs the current 1688 account out through the official logout endpoint, clears Alibaba-ecosystem cookies only inside the isolated collector profile to prevent automatic SSO re-login, opens the exact 1688 sign-in URL through Playwright, persists the logged-out state, and returns the integrated noVNC URL.

The noVNC path has separate Basic Auth. After the user finishes login, always switch back to collector mode, verify `/health`, call `POST /api/plugin-session/check`, and poll the returned job. Collection endpoints return `409 collector_unavailable` while login mode or a transition is active.

When the user asks to exit the current 1688 account and receive a fresh login page, run `collector_api.py logout-login`. Require `logout.loggedOut: true`, `logout.signinReady: true`, login mode `ready: true`, and a non-empty `loginUrl` before returning the URL. Do not type URLs through VNC keyboard events or paste through the VNC clipboard: remote key and clipboard delivery can drop or replace characters. Leave the collector in login mode until the user confirms the new login is complete, then switch to collector mode and verify the session with `login-check`.

Do not infer login state only from a visible browser page. Check Coolify status, `/api/browser-mode`, container health, HTTP response, and the login-status job. Do not restart or redeploy merely to switch modes, and do not ask the user to perform Coolify actions this skill can perform.

### Login-loss recovery

Use this workflow when the user says the 1688 login dropped, a capture returns `requires_auth`, or the plugin login job completes with `isLogin:false`:

1. Check Coolify application health and `GET /api/browser-mode`. If already in collector mode, call `POST /api/plugin-session/check` and poll the job to confirm that authentication is actually missing.
2. Call `POST /api/browser-mode/login` directly. Verify the response reports `mode: login`, `login.ready: true`, the worker is paused, and the collector browser is stopped.
3. Give the user the integrated noVNC URL returned by the mode API. The browser opens the 1688 sign-in page automatically; the noVNC Basic Auth credentials already live in the collector environment, so do not ask the user to edit Coolify or create another login application.
4. Wait for the user to confirm login. Do not treat the visible page alone as proof and do not run collection while login mode is active.
5. Call `POST /api/browser-mode/collector`. Verify `mode: collector`, `workerEnabled: true`, `collector.browser: running`, `login.state: stopped`, and `/health` is successful.
6. Call `POST /api/plugin-session/check`, poll `GET /api/jobs/{id}`, and require `status: completed` plus `extracted_data.isLogin: true`. If the task needs additional confidence, run one non-persisting product-detail test.

If verification still reports logged out, switch back to login mode once and tell the user that the 1688 session did not take effect. Do not deploy, restart the application, clear cookies, delete the profile, or loop mode switches as a login workaround.

## Full-shop scan

Endpoint:

```text
POST https://collector.yiswim.cloud/api/shop-scans/all
```

Body:

```json
{"url":"https://shop1442128638027.1688.com/page/offerlist.htm"}
```

The endpoint accepts a shop offer-list URL, resolves the member ID, refreshes the plugin heartbeat token, paginates in order, deduplicates offers, and applies a maximum-page safety limit. Use the existing Bearer API authentication.

When reporting a test, provide only status, total count, page count, request count, and elapsed time. Never output product IDs, cookies, tokens, `X-1688extension-Secret`, proxy credentials, or other secrets.

### Official plugin API implementation

The full-shop scan uses the same official 1688 browser-plugin request chain rather than scraping only the visible product cards:

- Login check: `mtop.1688.pc.plugin.user.login.get`
- Heartbeat/token refresh: `mtop.1688.pc.plugin.safe.heartbeat.key.get`
- Product list: `mtop.1688.pc.plugin.shop.offerList.query`

The collector resolves the shop `memberId`, refreshes the short-lived plugin token, creates the request-bound digest and dynamic extension secret, then requests pages in order (maximum `pageSize` 300). It stops at the reported total, deduplicates offers, and enforces a maximum-page safety limit. Tokens and extension secrets are generated at runtime and must never be hard-coded, logged, or returned to the user.

For a single page use `POST /api/shop-scans` with `allPages:false`; for the complete shop use `POST /api/shop-scans/all`. Verify completion through `GET /api/jobs/{id}` and report aggregate counts/timing only.

### Shop-specific product eligibility

The shop `shop478x140nz9144.1688.com` (汕头市澄海区伊品针织服装厂) is restricted to swimwear cover-ups. Only official-plugin product-list rows whose source category is exactly one of the following may enter detail capture or wearhongxiu publication:

- `沙滩防晒服`
- `沙滩裙、沙滩套装`

Treat every other source category from this shop—including `毛衣`, `女式针织衫`, and `大码毛衣`—as ineligible. Do not rely on a model-inferred wearhongxiu category or on product-title keywords to override this restriction. The collector enforces the rule at both `POST /api/product-details` and `POST /api/product-details/{id}/wordpress/publish`; a rejection returns `shop_product_policy_rejected` with reason `source_category_is_not_swim_coverup`.

Full-shop scans still store all source listings for lifecycle visibility, but `shop_products.ingestion_eligible`, `shop_products.ingestion_policy`, and `shop_products.ingestion_reason` record whether each listing may proceed. Bulk and scheduled synchronization must select only rows with `ingestion_eligible=true`. Historical ineligible publications must be changed to WordPress `draft` through the normal unpublish endpoint and verified with a completed RAG sync whose active state is false; do not delete their source rows or media merely to enforce the restriction.

## Standalone product detail capture

Use this flow when the user supplies a product detail URL or only an `offer_id`. The product is intentionally independent of `shop_profiles` and `shop_products`; do not create or infer a shop relationship unless explicitly requested.

```text
POST https://collector.yiswim.cloud/api/product-details
```

Request either a detail URL or an offer ID:

```json
{"url":"https://detail.1688.com/offer/1068935307931.html"}
```

```json
{"offerId":"1068935307931"}
```

The collector opens the rendered detail page, parses title, description, prices and tiers, MOQ, images, videos, SKU dimensions/options, attributes, seller fields, and page metadata. It downloads available product, gallery, and SKU images into the Persistent Storage directory `product-images/{offer_id}/`, while retaining each original URL and local path. Missing fields remain null/empty; do not fabricate listing times, tiers, or descriptions.

Gallery capture has strict completeness invariants. Read product Gallery images only from `.od-gallery-list img.preview-img`; never scan arbitrary large page images because reviews, recommendations, placeholders, and unrelated content can be mistaken for product media. Scroll the Gallery and hover its thumbnails to trigger lazy loading, normalize and deduplicate the resolved source URLs, and wait until the unique URL count stabilizes. A complete capture requires `raw_data.gallery.source == "exact_dom_gallery"`, `complete == true`, `stable == true`, `unresolvedSlotCount == 0`, and a downloaded main/Gallery count equal to the verified unique source count. If any condition fails, record the Gallery as incomplete and retry; do not publish a supposed full Gallery to WordPress.

Parse variations only from the real controls inside `#skuSelection`: dimensions from `.feature-item`, option buttons from `.transverse-filter > .sku-filter-button`, option text from `.label-name`, images from the same button, and expanded options from `.expand-view-list > .expand-view-item`. Never use broad selectors such as `[class*=sku]` or `[class*=item]`. Preserve each dimension name, option index/order, exact source text, and source image URL. Parent groups, labels, nested image elements, price fragments, and stock fragments are not standalone options. Store variation images as `image_type=sku`; they remain separate from the product Gallery.

The result is stored independently in `product_details`, with child tables `product_detail_images`, `product_detail_skus`, `product_detail_attributes`, and `product_detail_price_tiers`. The original URL, canonical URL, raw parsed JSON, and local image paths are retained. This workflow does not populate `shop_profiles`, `shop_products`, or shop scan snapshots.

Every successful saved product-detail capture automatically queues both the Gallery image audit and the SKU/size audit after the database write. Saved-product audits run in their own bounded queue without changing the captured product and persist independent historical records in `product_image_audits` and `product_sku_audits`. Each record retains its trigger (`capture` or `manual`), queued/running/completed/failed state, model and schema version, source hash, audit status, summary, complete result, error, and timestamps. A failed model call must leave a failed SQL record; it must not roll back or alter the source capture.

Saved-product audits use the bounded `SAVED_AUDIT_CONCURRENCY` worker pool (default 3, maximum 5). Live audits remain serialized because they navigate the shared Chromium page. On collector startup, saved image/SKU audit records left in `queued` or `running` state are reset safely and re-enqueued, so a deploy must not strand the audit backlog. Verify the backlog and active worker count through `GET /health` under `queues.savedAudits` before declaring a bulk synchronization complete.

Bulk synchronization treats model-provider authentication, billing, and entitlement failures (HTTP 401/402/403, including insufficient balance) as an external model-access circuit breaker. Pause the run instead of cascading failures. After billing/model eligibility is restored, resume the same progress file; failed translation/publication items caused by those codes are recoverable, and collector startup requeues image/SKU audit rows failed for the same provider-access reason. Verify a small direct model call before restarting a large run.
The saved-audit queue also pauses itself after the first provider-access 403; `GET /health` reports `queues.savedAudits.paused=true`. A restart after access restoration reopens the queue and recovers provider-access failures. Do not repeatedly restart while the account is still blocked.

Verify a saved detail with `GET https://collector.yiswim.cloud/api/product-details/{id}` using the Bearer credential; the response includes the detail row and its image, SKU, attribute, and price-tier children.

### Duplicate detection during capture

Every saved product-detail capture runs duplicate analysis after the exact Gallery has stabilized and its files have downloaded, but before `product_details` is written. Read the result from `duplicate_analysis`; the normalized summary is also exposed through `duplicate_status` and `duplicate_checked_at` on the product row.

The detector uses two evidence levels:

- A certain duplicate requires a different offer ID, two or more verified complete Gallery images, no unresolved Gallery slots, and an identical full multiset of SHA-256 image-content hashes against an existing verified product. The capture job ends as `rejected_duplicate`, does not create or update a product row, removes only the newly downloaded duplicate files, and returns the matched product and evidence in the job's `extracted_data.duplicateAnalysis`.
- Exact overlap in only part of the Gallery, a one-image match, or multimodal RAG similarity is never an automatic rejection. Save the product with `duplicate_status=similar_candidates`, `duplicate_analysis.decision=manual_review`, candidate IDs/scores and evidence so a person can decide later. No match uses `duplicate_status=no_similar_products`.

The multimodal candidate lookup searches active and inactive collector-managed RAG entities using up to three ordered Gallery images plus the source title. A RAG outage must not turn an advisory signal into a rejection: record `checks.multimodalEmbedding=failed`, retain the completed exact-hash result, and save the product. Never treat a model or embedding score alone as proof that two offers are the same product.

Re-capturing the same offer ID remains an update/refresh path and is excluded from cross-offer duplicate rejection. Do not override or bypass a `rejected_duplicate` result unless the user explicitly authorizes a revised duplicate policy or a manual recovery after evidence has been inspected.

## Saved product translation

Translate an already saved product detail to English with `deepseek-v4-flash-vision-exp`:

```text
POST https://collector.yiswim.cloud/api/product-details/{id}/translations
```

```json
{"targetLanguage":"en"}
```

If the internal product detail ID is unknown, resolve it with `GET /api/product-details?offerId={offer_id}`; use `GET /api/product-details?limit=100` to list recent saved details.

The POST returns HTTP 202. Poll `GET /api/translation-jobs/{job_id}` until `status` is `completed` or `failed`. Read stored translations with:

```text
GET https://collector.yiswim.cloud/api/product-details/{id}/translations?targetLanguage=en
```

Translations are stored in `product_detail_translations` and linked only through the `product_detail_id` foreign key. Never overwrite the Chinese source fields in `product_details` or its child tables. The English title is a visual rewrite, not a literal translation of the 1688 title: ignore years, marketplaces, export/hot-sale language, and keyword stuffing, then name the stable product visible across the images. Do not create a rewritten Chinese title.

Use up to six ordered main/Gallery images, excluding SKU and description images, to write one 35-120 word English product-level description. Describe stable visible construction and silhouette only; omit colors, prints, patterns, individual SKU details, sales language, unverifiable material/function claims, and SEO filler. Each record retains the image sources/count, `visual_rewrite` strategy, source snapshot and hash, rewritten English title/description, translated seller name, attributes, SKU dimensions/options/rows, price text, model, and usage. Numeric price, stock, MOQ, currency, URLs, image identity, SKU keys, array counts, and ordering remain unchanged.

The implementation uses two sequential `deepseek-v4-flash-vision-exp` stages: a focused multi-image call generates only the English title and description, then a text-only call translates the structured attributes and SKU data. Merge and validate both outputs before writing SQL. This separation avoids long multimodal JSON requests timing out and keeps visual copy independent from literal title translation.

Translation must preserve SKU identity: keep `dimensionName`, option `index`, `imageUrl`, ordering, and source-to-translated-option correspondence unchanged. When refreshing only corrected SKU data for an already translated/published product, request:

```json
{"targetLanguage":"en","preserveCatalogCopy":true}
```

This refreshes structured SKU translations while retaining the existing English product title and description.

The unique identity is `(product_detail_id, target_language, source_hash, model)`: the hash includes the translation source and selected image identities. Rerunning the same source/model updates that translation record, while changed source/images or a changed model creates a new version. Do not fabricate missing details, rewrite for SEO, infer materials, or convert units during this translation stage.

## Product gallery image audit

Use the image-audit flow when the user wants to inspect a product's main/Gallery images for suitability. This is an audit, not a destructive cleaning operation: do not delete, filter, reorder, or copy images, and do not create a `clean-gallery` directory.

For a live detail-page test that must not save product data, images, or SQL rows:

```text
POST https://collector.yiswim.cloud/api/image-audit/live
```

```json
{"urls":["https://detail.1688.com/offer/1069771570442.html"]}
```

The collector opens each detail page with the logged-in Chromium context and reads the ordered main/Gallery images into memory. It uses `deepseek-v4-flash-vision-exp` for both visible per-image checks and cross-image reasoning. It excludes SKU and detail-description images. Report the audit result without implying any image was removed or retained.

The live POST returns HTTP 202 with an audit job ID. Poll `GET /api/image-audit/jobs/{id}` until `status` is `completed` or `failed`. Image and SKU audit jobs share a single queue so they cannot navigate the shared Chromium page concurrently.

The response uses `schemaVersion: 4` and `mode: audit_only`. A single combined `deepseek-v4-flash-vision-exp` multimodal call handles per-image visible facts and cross-image orientation/duplicate reasoning, avoiding a redundant second pass over the same Gallery while preserving all audit fields. Preserve and report:

- whether the first image is a single front-facing product image
- per-image watermark and Chinese-text findings
- per-image front/back-or-reverse and collage findings
- duplicate groups, member indices, confidence, and evidence
- product-level summary flags and `auditStatus`

`back/reverse` is a warning condition. Duplicate findings describe relationships only; the audit never selects a winner or removes a member. For already saved product details, `POST /api/product-details/{id}/image-audit` analyzes existing local images and persists the result without copying or modifying images. Read its history with `GET /api/product-details/{id}/image-audits`. Saved-product Gallery audits must include only `main` and `gallery` image types; never include SKU or description images.

## SKU and size audit

Use the live SKU audit when the user wants to identify irregular variation names, image/name conflicts, multiple products or bundle/single-item options mixed on one detail page, or nonstandard size labels:

```text
POST https://collector.yiswim.cloud/api/sku-audit/live
```

Supply up to five detail URLs or offer IDs:

```json
{"urls":["https://detail.1688.com/offer/1069771570442.html"]}
```

```json
{"offerIds":["1069771570442"]}
```

The POST returns HTTP 202 with an audit job ID. Poll `GET /api/sku-audit/jobs/{id}` until `status` is `completed` or `failed`; this avoids Cloudflare timeouts during image download and complex-model inference. Audit jobs run one at a time so the shared Chromium page is not used concurrently.

This is a non-destructive audit. It reads the rendered product data and SKU images into memory, calls `deepseek-v4-flash-vision-exp` for multimodal recognition and reasoning, and does not modify names, normalize values in place, filter variants, or reorder rows. Live tests remain ephemeral. For an already saved product, use `POST /api/product-details/{id}/sku-audit`; it uses saved SKU/Gallery files and persists the result. Read history with `GET /api/product-details/{id}/sku-audits`.

The response preserves the original SKU dimensions, option names, image URLs, price, stock, and ordering. Report rule-based and model-based warnings separately when useful, including missing images, availability text used as a variant, possible model-code-plus-color labels, text/image mismatch, multiple distinct products, single/bundle mixing, nonstandard sizes, unusual size order, evidence, confidence, and whether human review is required. An absent image must be reported as `not_assessable`, never as a guessed match or mismatch.

`GET /api/product-details/{id}` exposes `latestImageAudit` and `latestSkuAudit` for quick downstream decisions. Audit conclusions are evidence, not transformations: preserve the source data and use `clear`, `issues_detected`, or `failed` to route later publication checks or human review. Do not silently repair, delete, merge, relabel, or reorder product images or variations from an audit result.

## wearhongxiu product publishing

### Shopify publication tracking

Ready to Sell publishing uses a bidirectional source mapping rather than a boolean flag.

- Resolve one exact Collector source with `GET /api/shopify/source-match?wpPostId={wordpress_post_id}&styleNo={style_no}&store={shopify_store}`. All three values are required, and an ambiguous match returns HTTP 409.
- Read a saved mapping with `GET /api/product-details/{id}/shopify?store=shop.wearhongxiu.com`.
- After Shopify and storefront verification succeed, upsert the mapping with `POST /api/product-details/{id}/shopify`. Send `shopifyStore`, `shopifyProductGid`, `shopifyHandle`, `shopifyUrl`, `productStatus`, `publicationStatus`, source WordPress/style identifiers, a sync hash, and `verified=true`.
- Preserve mapping history when a Shopify product is archived or unpublished: update its statuses instead of deleting the row.
- Never match by a similar title. Use the exact WordPress post ID plus style number, and use the saved Shopify Product GID as the primary duplicate guard.

### WordPress publication

Resolve a collector-managed Wearhongxiu product by exact identifier with `GET /api/wordpress/products/resolve`, supplying exactly one of `styleNo`, `wpPostId`, `slug`, or `url`. For a style number such as `SKG269`, use `collector_api.py product-resolve SKG269`. The common path uses the indexed `upper(style_no)` collector lookup and returns both the WordPress mapping and collector product-detail information in one request; URL/post-ID queries use exact identifiers, and an unmapped slug falls back to the authenticated WordPress resolver. HTTP 409 means the identifier is ambiguous and must not be used for mutation. Never search a style number as title text or scan all products.

For an exact unpublish request given only a style number or product URL, resolve first, require one result, then call `POST /api/product-details/{collector.product_detail_id}/wordpress/unpublish`. This avoids offer-list scans and prevents changing the wrong product.

To change only a collector-managed product's style number without re-uploading images or republishing its other content, call `POST /api/product-details/{id}/wordpress/style-number` with `{"styleNo":"SKB257"}`, or use `collector_api.py product-style-set`. The endpoint rejects conflicts, updates both the live WordPress `sku` and the collector publication payload/index, and queues a RAG refresh. Resolve and verify the new style number after the update.

Publish an already saved and translated product detail into the existing wearhongxiu custom `product` post type with:

```text
POST https://collector.yiswim.cloud/api/product-details/{id}/wordpress/publish
```

The request is asynchronous. Poll `GET /api/wordpress-jobs/{job_id}`. Use `POST /api/product-details/{id}/wordpress/preview` for a non-mutating payload and merchandising preview. Both use the existing Bearer authentication.

Bulk product publication uses the bounded `WORDPRESS_PUBLISH_CONCURRENCY` queue (default 3, maximum 5). Style-number allocation remains safe because WordPress reserves numbers under its database advisory lock. Publication-date backfills and price-repair maintenance jobs stay on a separate serialized queue. `GET /health` exposes `queues.wordpressPublish`; do not declare a bulk run complete while it still has active or pending publication tasks.

Every image uploaded while creating or updating a wearhongxiu product must pass a public-availability gate before the WordPress product sync is allowed to complete. Fetch the returned media URL with browser-compatible headers and require a successful HTTP status, an `image/*` content type, at least 1 KiB of data, and a recognized JPEG/PNG/GIF/WebP/AVIF file signature. Retry the public check once to allow brief propagation. If an attachment is missing, returns an HTML/error body (including HTTP 403), or is not a decodable image payload, force a new attachment from the collector's persistent-storage source file using a distinct repair source key, then validate the replacement. If the replacement also fails, fail the publication job instead of publishing a broken product. Preserve the per-image verification result and whether a replacement was required in the publication job result.

For an existing collector-managed product with a broken published image, run `scripts/repair-wordpress-product-image.mjs` inside the current collector container with `WP_POST_ID` and zero-based `WP_IMAGE_INDEX`. It recreates only the selected attachment from persistent storage, rewrites the saved publication payload and WordPress product, and leaves the old attachment recoverable. Resolve the current container ID first; never assume an ID from an earlier deployment. This repair helper does not apply to legacy products absent from `product_wordpress_publications`; repair those only from their separately retained original image file and verify the resulting public URL.

The publisher calls `deepseek-v4-flash-vision-exp` with up to four ordered main/Gallery images plus the translated product data. It matches only existing wearhongxiu category IDs, selects one specific primary category, may recommend additional genuine product-type categories, recommends reusable style/filter tags, and extracts an English material from explicit detail evidence. It must not infer fiber composition only from appearance; missing material falls back to `Polyester`.

Category assignment is configurable per product:

- `categoryMode: "auto"` uses all model-recommended categories and records the most specific one as the primary category.
- `categoryMode: "primary_only"` assigns only the primary category, even if the product could reasonably belong to others.
- `categoryMode: "manual"` uses the supplied `categoryIds`; supply `primaryCategoryId` to control the primary category shown under Product Details.

Tags support `tagMode: "auto"` or `tagMode: "manual"` with `tagIds`/`tags`. Auto mode prefers an exact existing tag for the same concept and proposes a new normalized tag only when no equivalent exists. Do not delete the site's old tags or bulk-unassign existing products unless the user separately authorizes that migration.

wearhongxiu pricing is deterministic and must not be delegated to the model. Only exact product-scoped evidence may establish the source price: exact DOM SKU prices take priority, followed by tiers scoped to the current offer; product JSON-LD is a fallback only when exact SKU prices are unavailable. Never use generic `price` or `maxPrice` fields found by scanning page-global embedded JSON because unrelated recommendations and other page modules contaminate those values. Saved product ranges and tiers may participate only when `raw_data.price.verified === true`; exact saved SKU prices remain trusted. If no reliable price can be verified, stop publishing instead of guessing.

- Source base is the maximum verified numeric CNY price across the trusted SKU rows and current-offer price tiers.
- `50-299 pcs`: `(source maximum + 20 CNY) / 6.5`
- `300-999 pcs`: `(source maximum + 15 CNY) / 6.5`
- `>=1000 pcs`: `(source maximum + 10 CNY) / 6.5`
- Round calculated USD prices to two decimal places; sample price is always `$50.00`; MOQ is `50 pcs`; bulk production is `~28 days`.

Retail availability is true only when at least one SKU exists and every captured SKU has numeric stock greater than zero. The existing wearhongxiu frontend then crosses out the `$50` sample fee and displays `2 ×` the `50-299 pcs` tier as the retail/sample checkout price. If any SKU is zero or unknown, keep the fixed `$50` made-to-order sample price and do not show the retail crossed-price treatment.

Set `fabric_weight` to `200gsm`, `customizable`/`customization` to Yes, and the Product Details category field to the primary category name. Preserve the existing product page templates and Size Guide popup; leave `size_chart` empty unless a real product-specific chart is available so the current category/general fallback remains active.

wearhongxiu collection sorting with `Newest first` uses the WordPress product `post_date`. For collector-managed products, set this date from the official plugin product-list `listing_time` (`gmtCreate`) matched by the globally unique offer ID. If no valid 1688 listing time was captured, fall back to the product detail's `first_seen_at`; never use `last_crawled_at`, `source_updated_at`, translation time, or sync time for collection ordering. The publisher updates WordPress `date_gmt` through the native `wp/v2/product/{post_id}` endpoint after the custom product sync, so future content refreshes preserve the source-based ordering date.

The dedicated wearhongxiu `New Arrivals` page is different from ordinary collection sorting: it includes only published products whose `arrival_date` custom field is non-empty. For collector-managed products, populate `meta.arrival_date` in `YYYYMMDD` format only when the product can be matched by offer ID to the official-plugin `shop_products.listing_time` (`gmtCreate`). Never substitute `first_seen_at`, crawl time, translation time, sync time, or a guessed date for `arrival_date`. If the official listing time is unavailable, omit the field so that product is intentionally skipped by New Arrivals.

To backfill existing collector-managed published products without re-uploading media or changing titles, prices, taxonomies, variations, post dates, or other content, start:

```text
POST /api/wordpress/arrival-dates/backfill
GET  /api/wordpress-arrival-date-jobs/{id}
```

The job processes only rows in `product_wordpress_publications` with `wp_status=publish`, calls the dedicated wearhongxiu `/hx/v1/products/arrival-date` route, confirms the returned value, and mirrors the date into the saved publication payload. Verify `totalPublished`, `eligible`, `updated`, `skippedMissingListingTime`, and `failed`, then verify the public New Arrivals count. Missing listing times are skips, not failures, and must remain unset.

To apply this rule to existing collector-managed WordPress publications without re-uploading media or overwriting product content, start `POST /api/wordpress/publication-dates/backfill` and poll `GET /api/wordpress-publication-date-jobs/{id}`. The backfill is limited to records in `product_wordpress_publications`, updates dates in batches of five, and reports totals split between 1688 listing time and first-seen fallback. Do not apply it to unrelated legacy WordPress products.

Publish SKU swatches with a strict one-to-one mapping. The WordPress color count must equal the true source color-option count. Every option that has a source SKU image must resolve, by normalized source URL, to its own uploaded media attachment; SKU media must never be appended to the product Gallery. Never map all swatches to the main image, and do not fabricate a swatch image when the source option has none. After publishing, verify the stored color count, number of unique mapped attachment IDs, and public rendered swatch-button count against the source options.

### Gallery and SKU repair audits

Use the deterministic repair scripts in `E:\cc\products\1688-dom-collector\scripts` when existing saved or published products may predate the strict parsers:

- `repair-verified-galleries.mjs` re-captures and publishes only verified complete Galleries.
- `repair-sku-swatches.mjs` refreshes exact SKU options/translations and restores one-to-one swatch media without replacing existing catalog copy.

Both workflows are resumable and should keep a progress file. Discover affected products from the data rather than maintaining a hard-coded style list: flag combined or multi-value option labels, source/published color-count mismatches, repeated swatch attachment IDs, and SKU source-image/mapped-image count mismatches. Re-check the public page after repair and report totals, repaired records, remaining failures, and retry status without exposing offer IDs or credentials.

### Price audit and repair

After changing price parsing, or when a published price appears abnormal, run the collector-wide deterministic audit:

```text
POST /api/wordpress/prices/audit-and-repair
GET /api/wordpress-price-repair-jobs/{id}
```

The audit rebuilds saved price ranges and tiers from trusted product-scoped evidence, marks the stored price as verified, and updates published wearhongxiu pricing with concurrency five. A price-only repair must update `bulk_pricing`, `meta.source_price_min/max`, and `source.price_min/max` together. It must not upload images or overwrite titles, descriptions, style numbers, categories, tags, variations, dates, or other product content. Verify `total`, `verified`, `unresolved`, `storedChanged`, `publishedAffected`, `wordpressUpdated`, and `failed`; retry only failed/unresolved records after correcting their cause. Finally inspect one known affected publication and confirm the database range, saved publication payload, and rendered tier prices agree.

### Category-based style numbers

New wearhongxiu products receive their style number from the primary category at publish time. Do not reuse the 1688 seller's item number. The WordPress endpoint `POST /wp-json/hx/v1/products/style-number` scans all existing `sku` meta values for the approved prefix, accounts for outstanding reservations, and uses a MySQL advisory lock before reserving the next number. Preview requests use `reserve:false`; a real publish uses `reserve:true`. Re-publishing an existing external ID preserves its current style number. An explicit `styleNo` is a manual override.

Approved mappings are:

- Bikini Set `SWBK` (3 digits); Bikini Top `SWBKT` (3); Bikini Bottom `SWBKB` (3)
- Tankini Set `SWT` (3); Tankini Top `SWTT` (3)
- One Piece `SWOP` (3); Plus Size Swim `SWPL` (3)
- Girl's Swim `SKG` (3); Boys Swim `SKB` (3); Boardshorts `SM` (6)
- Cover ups `CW` (3); yoga wear `YW` (3); Three-Piece Swim `ST` (3)
- Maternity Swimwear `SWMT` (3); Period Swimwear `SWPER` (3); Rashguard `SRG` (3)

Parent/navigation or merchandising categories such as `women swimwear`, `men swimwear`, `Best Sellers`, and `Factory Quality Choice` are not valid primary style-number categories. If the selected primary category has no approved mapping, stop with a clear error rather than inventing a prefix. Historical category mistakes do not define the numbering scheme; prefix definitions above are authoritative, while the next numeric value is determined globally from live WordPress data.

### Sales-weighted Best Sellers

The dedicated wearhongxiu Best Sellers page uses the WordPress product category with slug `best-sellers`, not a free-form product tag. The page shortcode currently renders at most 36 products, while its query randomizes their display order. Rebuild category membership with:

```text
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py best-sellers-preview --limit 36
python3 /root/.codex/skills/1688-collector-ops/scripts/collector_api.py best-sellers-rebuild --limit 36
```

Preview before mutating. Candidates must be collector-managed products that are still published on wearhongxiu and whose matched `shop_products` row is active and has `ingestion_eligible=true`. Allocate the requested total across shops in proportion to each shop's eligible published-product count, with at least one slot for every represented shop when the target permits. Within each shop, select by numeric 1688 `sale_quantity` descending; break equal or missing-sales ties by 1688 listing time descending and then WordPress post ID for deterministic output.

The rebuild replaces public `best-sellers` category membership while preserving every product's other categories. Verify the job reports the exact requested selected count and zero failures, then read the WordPress category count and the rendered Best Sellers page. For the current 36-product page, require category count 36, 36 rendered cards, and 36 unique product IDs. Purge the wearhongxiu page cache after a successful live rebuild.

## Shop homepage overview

For a shop homepage URL (for example `https://rongfeidi.1688.com/`), use the ordinary single-page DOM job endpoint, not `/api/shop-scans/all`:

```text
POST https://collector.yiswim.cloud/api/jobs
```

Body:

```json
{"url":"https://shop.example.1688.com/","paginate":false}
```

Wait for the job to complete, then inspect the parsed DOM data and page metadata. Report a structured overview, distinguishing fields that are present from fields that are unavailable or not exposed on the homepage:

- shop name and homepage title
- opening/registration time
- follower count
- total product count
- company name and business information
- address
- contact information
- Wangwang/contact link
- all-products or offer-list entry URL
- any other clearly labeled shop-level fields

To obtain the real Wangwang link without starting a chat, use:

```text
POST https://collector.yiswim.cloud/api/shop-contact-link
```

Body:

```json
{"url":"https://shop.example.1688.com/"}
```

This operation triggers the page's customer-service JavaScript in a protected browser context, intercepts `window.open`, blocks the web-IM page request, and returns only the generated URL. It must not open the IM page or send a message. Prefer this captured URL over reconstructing one manually.

Do not claim a field was found merely because it was requested; verify it in the returned DOM/parser data. This homepage operation is single-page only and must not automatically enumerate products. Unless the user asks to save artifacts, do not download or persist additional files beyond the collector's normal job record.

## PostgreSQL persistence

### Multimodal product search indexing

The collector is integrated with the independent `products_rag_api` application at the canonical URL `https://products-rag-api.yiswim.cloud`. This v2 index is sourced only from collector-managed 1688 products; do not migrate or read the retiring local `products` catalog. The former `products_rag_items` table may remain for rollback/history but public v2 search reads only `search_embeddings`.

Treat that canonical URL as configuration, not as a name inferred from the application identifier. DNS host labels must contain only letters, digits, and hyphens; never convert the application name `products_rag_api` directly into a hostname or introduce underscores. Before a production scan, validate the configured `PRODUCTS_RAG_API_URL` with the same strict TLS stack used by the collector (for example, a normal Python `requests.get(..., verify=True)` call to `/health`). A successful browser or `curl` request alone is not sufficient because TLS hostname-validation behavior differs between clients. Stop before scanning if the configured URL differs from the canonical URL or strict certificate verification fails, and report the exact configured hostname.

Each source product initially uses canonical ID `1688:{offer_id}` and creates separate entities in the shared `search_embeddings` table:

- one `product` entity with text, main-image visual, and fused image+text embeddings;
- one `gallery_image` entity per distinct ordered main/Gallery image, with a visual embedding;
- one `sku_image` entity per distinct SKU image, with visual, SKU-label text, and fused embeddings.

All vectors continue to use DashScope `qwen3-vl-embedding` at 1024 dimensions. DeepSeek is used only for generative multimodal analysis, translation, and merchandising; do not change the embedding provider or rebuild vectors as part of a DeepSeek model switch. The collector sends the original public image URLs; it does not copy or base64-encode images for RAG. `canonical_product_id` is deliberately separate from the 1688 offer ID so future same-style detection can associate several offers with one canonical product without rebuilding the source tables.

Collector lifecycle hooks enqueue RAG syncs automatically:

1. Detail capture writes the Chinese source and 1688 image entities with `active=false` unless that product already has a live WordPress publication.
2. English translation refreshes product/SKU text while preserving the current publication state.
3. A successful WordPress sync refreshes WordPress ID, style number, category, tags and URL; only `wp_status=publish` is publicly active.
4. Republishing as a non-public WordPress status makes all entities for that canonical product inactive. For a separate removal workflow, call the RAG deactivate endpoint for the canonical or source product ID.

RAG failure must not roll back a successful page capture, translation, or WordPress publication. Each trigger is recorded in `product_rag_syncs` as `queued`, `running`, `completed`, or `failed`, including attempt count, trigger type, active state, a non-secret response summary, and error. The RAG client retries transient HTTP failures three times and the collector queue has bounded concurrency.

Operate or verify a saved product with:

```text
POST /api/product-details/{id}/rag-sync
GET  /api/product-details/{id}/rag-syncs?limit=20
```

The manual POST is for repair/verification; normal capture, translation, and publication do not need it. On a completed sync, verify the expected entity counts and active state. Then inspect the RAG service with its own admin token:

```text
GET /api/products_rag/admin/stats
GET /api/products_rag/admin/entities?sourceProductId={offer_id}
```

Public wearhongxiu-compatible text, image, and combined search remains one endpoint:

```text
POST /api/products_rag/public/search
```

The RAG service searches visual, text, and fusion vector columns with mode-specific weights, aggregates image-level hits by `canonical_product_id`, and returns product-card fields plus up to four matched images. Do not expose image entities as separate product cards or create separate frontend entry points for product-level versus image-level search.

Required collector runtime variables are `PRODUCTS_RAG_API_URL`, `PRODUCTS_RAG_ADMIN_TOKEN`, and `PRODUCTS_RAG_SYNC_CONCURRENCY`. If manual sync returns `not_configured`, inspect the actual Coolify runtime environment and redeploy the collector after correcting it; a restart-only operation may continue using the previous generated environment. Never print either application's token.

The collector is connected to PostgreSQL through `DATABASE_URL`. Completed shop homepage captures are automatically upserted into the `shop_profiles` table, using `shop_url` as the unique key. The table stores normalized shop/company fields, follower and offer counts, service metrics, contact fields, Wangwang URL, offer-list links, navigation JSON, raw parsed data, and `first_seen_at`/`last_seen_at` timestamps.

Use `GET https://collector.yiswim.cloud/api/shops` with the existing Bearer authentication to verify stored profiles. Re-scanning the same shop updates its existing row rather than creating a duplicate. When verifying a write, check the capture status and then query `/api/shops`; do not report success based only on the job response.

### Product overview persistence

Full-shop results from the official plugin API are stored without translation in three related tables:

- `shop_products`: one current row per `(shop_id, offer_id)`, including title, category, price, currency, image/product URLs, sale quantity and original sale text, listing time when supplied, shipping information, status, raw offer JSON, and crawl timestamps.
- `shop_product_snapshots`: one historical row per product per scan run, used to detect new products, removals, price/title/sales changes, and listing-time changes.
- `shop_scan_runs`: one row per completed full-shop job, recording totals, fetched count, request count, truncation, and timing.

After a full-shop scan, verify both the job result and the database using `GET /api/shops/{shop_id}/products`. Do not invent listing times when the plugin response omits them; retain the original sale text alongside any numeric conversion. The product tables are shared by all shops and are separated by `shop_id`.

### Daily shop lifecycle reconciliation

For a scheduled new/delisted-product check, enumerate stored shop profiles that have a valid `offer_list_url`, then run `POST /api/shop-scans/all` for one shop at a time. Only a successful, non-truncated all-pages scan is allowed to reconcile removals. The completed job's `extracted_data.reconciliation` contains aggregate counts and internal offer-ID lists for `added`, `relisted`, and `removed` products. A partial or single-page scan never marks products as removed.

The current inventory state is stored on `shop_products.availability_status`. Products observed in the latest complete scan are `active` and have `last_seen_in_scan_at`; products absent from a later complete scan are `delisted` and retain `delisted_at`. A reappearing product is restored to `active` and clears `delisted_at`.

For each added or relisted product, run the normal complete product pipeline only when the schedule asks for publication: detail capture, automatic audits, duplicate analysis, English translation, wearhongxiu publication, and RAG verification. Preserve all normal trusted-price, Gallery-completeness, risk-control, login, duplicate-rejection, and provider-access circuit breakers.

For each removed product, resolve the saved detail by offer ID. If it has a live WordPress publication, call:

```text
POST /api/product-details/{id}/wordpress/unpublish
```

The endpoint changes the WordPress product to `draft`, updates `product_wordpress_publications.wp_status`, and queues a RAG sync whose inactive state follows the non-public WordPress status. If no WordPress publication exists, do not fabricate one and do not call WordPress. A scheduled report may include shop names and aggregate counts, audit summaries, and sanitized failure reasons, but must not expose offer IDs or credentials.

## Troubleshooting order

1. Inspect Coolify status and recent deployment logs.
2. Check `/api/browser-mode` and confirm only its expected Chromium is running.
3. Confirm `/app/storage` is mounted and writable.
4. Check the integrated noVNC path and health endpoint HTTP status.
5. Check 1688 login status.
6. Redeploy only if the preceding checks identify a deployment problem.
