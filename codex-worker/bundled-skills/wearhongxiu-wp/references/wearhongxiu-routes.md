# Wearhongxiu route notes

The live route index is authoritative. Always discover it with
`python scripts/wp.py routes --namespace hx/v1` before relying on this list.

## POD products

- `GET /hx/v1/pod-products/schema`
- `POST /hx/v1/pod-products/sync`
- `GET /hx/v1/pod-products/by-external-id/{external_id}`

The manifest URLs returned by POD records can point to
`https://pod-api.wearhongxiu.com`. Do not mutate that shared service as part of
a WordPress task because Shopify uses it too.

## Other custom business routes seen in the local source history

- Inquiry routes: `contact-inquiry`, `selection-inquiry`, `quote-inquiry`,
  `bulk-quote`, and `wholesale-inquiry`.
- Product routes: product sync, media ensure, taxonomies, lookup by external ID,
  sample options, and style search.
- Stripe routes: sample checkout and webhook handling.

Some inquiry and PI administration uses authenticated `admin-ajax.php` actions
rather than REST. Do not treat public submission endpoints as administrative
read APIs, and do not trigger customer email or payment creation during an
inspection request.

## WPCode, menus, and Elementor

### Exact product resolver

For administrative lookup by style number, WordPress post ID, slug, or product URL, call the authenticated endpoint `GET /wp-json/hx/v1/products/resolve` with exactly one of `style_no`, `post_id`, `slug`, or `url`. It requires `edit_posts`, queries the `sku` meta field directly for style numbers, rejects off-site URLs, and returns HTTP 409 when an identifier has multiple matches. Prefer this route whenever the user supplies a style number; do not use the public `/hx/v1/products/style-search`, title search, or a full product listing. The resolver is implemented by active WPCode snippet `35802` (`Hongxiu Product Resolver REST API`).

Category-based allocation is implemented by active WPCode snippet `4977`. Girl's Swim (category 40) uses `SKG` with three digits; Boys Swim (category 323) uses `SKB` with three digits. Allocation scans the live `sku` meta values for the category's prefix, so the next Boys Swim product continues after the largest existing `SKB` number. Do not reuse `SKG` for Boys Swim. For a collector-managed style-only correction, use the collector's `POST /api/product-details/{id}/wordpress/style-number` endpoint so WordPress, the collector mapping, and product RAG stay synchronized.

- If `/wpcode-remote-api/v1/snippets` is present, it can be accessed through
  the generic `get` and `post` commands. Read a snippet and preserve a backup
  before an authorized code update. The live bridge plugin is named `WPCode
  Remote Snippet API`; do not confuse its namespace with `wpcode/v1`.
- A navigation menu may come from WordPress menu routes, a theme template part,
  or Elementor template metadata. Inspect the rendered header and live route
  inventory before choosing the storage layer.
