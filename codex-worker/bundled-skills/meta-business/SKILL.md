---
name: meta-business
description: Inspect and manage authorized Meta business assets through the Graph and Marketing APIs, including Facebook Pages, Instagram professional accounts, organic publishing, insights, comments, messaging, leads, ad accounts, campaigns, ad sets, ads, creatives, local image/video uploads, catalogs, business assignments, Page subscriptions, and Threads reads. Use when Codex needs to query or change Meta Business, Facebook, Instagram, Threads, advertising assets, or ad media. Do not use for ordinary marketing drafting that does not require Meta account access.
---

# Meta Business

Use this skill as the unified entry point for Meta business operations. Keep domain logic modular so Page, Instagram, ads, messaging, leads, catalogs, and webhook capabilities can expand without creating separate top-level skills.

## Safety

- Read the token from the local credential file or `META_BUSINESS_ACCESS_TOKEN`. Never print it, place it in command arguments, persist it in generated artifacts, or commit it.
- Treat publishing, messaging, moderation, ads, catalog changes, assignments, and subscriptions as external writes. Run them only after the user explicitly authorizes the exact target and change.
- Require `--confirm-publish` for every live publishing command. Use `--dry-run` to inspect the target and payload without calling Meta.
- Require `--confirm-write` for every other live write or deletion command. Use `--dry-run` first for ads, messages, moderation, catalogs, assignments, and subscriptions.
- Do not infer authorization to publish from a request to draft, preview, review, schedule, or prepare content.
- Do not automatically retry a failed publishing request when the first request may have succeeded. Check the returned object or container before retrying.
- Use only assets assigned to the token. Prefer configured default IDs and identify the target account in the final response.
- Reference media URLs must be public HTTPS URLs that Meta can fetch. Do not upload local media without explicit authorization. Preview the resolved local path, MIME type, and size with `--dry-run` first.

## Configuration

For local use, store configuration in `%USERPROFILE%\\.meta-business\\credentials.json`:

```json
{
  "access_token": "<system-user-token>",
  "page_id": "<page-id>",
  "ig_user_id": "<instagram-professional-account-id>",
  "ad_account_id": "act_<ad-account-id>",
  "business_id": "<business-portfolio-id>",
  "catalog_id": "<catalog-id-if-assigned>",
  "threads_user_id": "<threads-user-id-if-configured>",
  "graph_api_version": "v26.0"
}
```

Keep this file outside the skill directory and every repository. Environment variables remain supported and take precedence over values in the file:

- `META_BUSINESS_ACCESS_TOKEN` — system-user access token.
- `META_BUSINESS_PAGE_ID` — default Facebook Page ID.
- `META_BUSINESS_IG_USER_ID` — default Instagram professional account ID.
- `META_BUSINESS_AD_ACCOUNT_ID` — default ad account ID, preferably with the `act_` prefix.
- `META_BUSINESS_BUSINESS_ID` — default business portfolio ID.
- `META_BUSINESS_CATALOG_ID` — optional default catalog ID.
- `META_THREADS_USER_ID` — optional Threads profile ID.
- `META_GRAPH_API_VERSION` — optional; defaults to `v26.0`.
- `META_BUSINESS_CONFIG_FILE` — optional path to a different credential file.

Read [authentication.md](references/authentication.md) when setting up or troubleshooting access.

## Route the task

### Assets and connection

Use `scripts/assets.py` for connection verification, token permissions, Page/Instagram discovery, and configured asset summaries.

```bash
python3 scripts/assets.py verify
python3 scripts/assets.py permissions
python3 scripts/assets.py list-pages
```

### Facebook Pages

Use `scripts/pages.py` for Page information, recent published posts, post lookup, and publishing text/link, photo, or hosted-video posts. Read [pages.md](references/pages.md) for exact commands and fields.

### Instagram professional accounts

Use `scripts/instagram.py` for account information, recent media, publishing limits, container status, and publishing single images, image carousels, or Reels. Read [instagram.md](references/instagram.md) before publishing.

### Local media and cross-platform Reels

Use `scripts/media_stage.py` to preflight local or hosted media and stage local files to configured R2 storage. Use `scripts/crosspost.py` for one idempotent Instagram + Page Reel operation with a persistent operation file. Read [publishing.md](references/publishing.md) before cross-posting or resuming.

### Ads and performance

Use `scripts/ads.py` for ad accounts, campaigns, ad sets, ads, creatives, local image/video uploads, previews, insights, creation, updates, and archiving. Use `scripts/insights.py` for Page, post, Instagram account, and Instagram media insights. Read [ads.md](references/ads.md) before any advertising write and [insights.md](references/insights.md) for organic metrics.

### Engagement, messaging, and leads

Use `scripts/comments.py` for Page/Instagram comments and moderation, `scripts/messaging.py` for Page/Instagram conversations and sends, and `scripts/leads.py` for lead forms and lead retrieval. Read [engagement-messaging.md](references/engagement-messaging.md) before writes or handling personal data.

### Business assets, catalogs, metadata, and Threads

Use `scripts/business.py` for portfolio assets and system-user assignments, `scripts/catalogs.py` for catalogs, `scripts/metadata.py` for Page app subscriptions, and `scripts/threads.py` for Threads reads. Read [data-assets.md](references/data-assets.md). The existing `meta-ads` MCP remains a separate read-only integration; do not overwrite or silently reuse its token.

## Publishing workflow

1. Verify the configured identity and target with `assets.py verify`.
2. Build and inspect the final caption, URLs, account ID, and media type.
3. Run the relevant command with `--dry-run` when any detail is uncertain.
4. After an explicit publish request, run the same command with `--confirm-publish`.
5. For Instagram, wait for the creation container to reach `FINISHED` before `media_publish`.
6. Return the created object ID, account, media type, and permalink when Meta supplies one.
7. If processing fails, report the returned error and container state without resubmitting automatically.
8. For local media or a Page + Instagram Reel, run `media_stage.py preflight` and `crosspost.py --dry-run`; preserve the operation file and use `--resume` instead of starting a new operation.
9. Write separate platform copy: never put a URL in an Instagram caption because it is not clickable; use “Link in bio” instead. Facebook Page copy may retain the clickable destination URL. The Instagram script rejects captions containing `http://`, `https://`, or `www.`.
10. Preserve paragraph breaks as real newline characters. Never publish visible literal `\\n` or `\\r\\n` sequences; the publishing scripts normalize these escaped sequences before dry-run, operation persistence, and API submission.

## Other write workflow

1. Read the target object and current state.
2. Build the smallest domain-specific change. Put complex ads/catalog payloads in a JSON spec file without secrets.
3. Run the exact command with `--dry-run` and verify IDs, amounts, currency, time zone, schedule, audience, and status.
4. Obtain explicit user authorization for the displayed change.
5. Repeat with `--confirm-write`; never add both flags.
6. Read the object again to verify the resulting state. Do not retry ambiguous writes automatically.

## API behavior

- Base URL: `https://graph.facebook.com/{version}`
- Authentication: `Authorization: Bearer <token>`
- Page text/link: `POST /{page-id}/feed`
- Page photo: `POST /{page-id}/photos`
- Page hosted video: `POST /{page-id}/videos`
- Instagram container: `POST /{ig-user-id}/media`
- Instagram publish: `POST /{ig-user-id}/media_publish`
- Instagram status: `GET /{container-id}?fields=status_code,status`

Use the generic client only through the domain scripts. Do not add a generic unrestricted POST escape hatch.
