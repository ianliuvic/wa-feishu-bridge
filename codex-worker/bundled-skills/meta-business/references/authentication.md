# Authentication and assets

## Required relationship

The Meta app, system user, Facebook Page, and Instagram professional account must be associated with the same Business Portfolio or explicitly shared to it. Assign the Page, Instagram account, and app to the system user.

## Required publishing permissions

- `business_management`
- `pages_show_list`
- `pages_read_engagement`
- `pages_manage_posts`
- `instagram_basic`
- `instagram_content_publish`

Additional future domains require their own permissions. A permission present on the token does not override missing asset assignment.

## Local credential file

The recommended local setup is `%USERPROFILE%\\.meta-business\\credentials.json`:

```json
{
  "access_token": "<system-user-token>",
  "page_id": "<page-id>",
  "ig_user_id": "<instagram-professional-account-id>",
  "ad_account_id": "act_<ad-account-id>",
  "business_id": "<business-portfolio-id>",
  "catalog_id": "<optional-catalog-id>",
  "threads_user_id": "<optional-threads-user-id>",
  "graph_api_version": "v26.0",
  "media_r2_bucket": "<optional-staging-bucket>",
  "media_r2_prefix": "marketing/meta-publish",
  "media_r2_public_base_url": "<optional-public-bucket-domain>"
}
```

Keep this file outside the skill directory and every workspace or repository. Restrict its filesystem permissions to the current user. Never put the token in a command argument or paste it into chat.

Environment variables `META_BUSINESS_ACCESS_TOKEN`, `META_BUSINESS_PAGE_ID`, `META_BUSINESS_IG_USER_ID`, `META_BUSINESS_AD_ACCOUNT_ID`, `META_BUSINESS_BUSINESS_ID`, `META_BUSINESS_CATALOG_ID`, `META_THREADS_USER_ID`, and `META_GRAPH_API_VERSION` remain supported and take precedence over the corresponding file values. `META_BUSINESS_CONFIG_FILE` can point to a different credential file. Restart Codex after changing user-level environment variables so new processes inherit them. Never use the separate `META_ACCESS_TOKEN` configured for the read-only `meta-ads` MCP as a fallback.

Local media staging additionally supports `META_MEDIA_R2_BUCKET`, `META_MEDIA_R2_PREFIX`, `META_MEDIA_R2_PUBLIC_BASE_URL`, `META_MEDIA_R2_ACCOUNT_ID`, `META_MEDIA_R2_ACCESS_KEY_ID`, and `META_MEDIA_R2_SECRET_ACCESS_KEY`. Keep R2 secrets in the credential file or environment only.

## Verification

Run:

```bash
python3 scripts/assets.py verify
python3 scripts/assets.py permissions
python3 scripts/assets.py list-pages
```

`verify` does not publish. It checks the system-user identity and the configured Page and Instagram account. `list-pages` discovers Page-to-Instagram mappings visible to the token.

## Common failures

- `190`: invalid, expired, or revoked token.
- `10` or `200`: permission or asset assignment is missing.
- Empty `/me/accounts`: the system user cannot see an assigned Page; query the configured Page directly and review Business asset assignment.
- Missing `instagram_business_account`: the Instagram account is not professional, is not linked to that Page, or is not assigned to the system user.
