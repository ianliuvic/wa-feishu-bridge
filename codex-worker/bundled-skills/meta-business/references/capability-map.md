# Capability map

Maintain one top-level `meta-business` skill and add domain modules as access expands.

| Domain | Intended module | Status |
|---|---|---|
| Business assets and permissions | `assets.py` | Implemented |
| Facebook Page read and publish | `pages.py` | Implemented |
| Instagram read and publish | `instagram.py` | Implemented |
| Media preflight and R2 staging | `media_stage.py` | Implemented |
| Idempotent Page + Instagram Reel cross-post | `crosspost.py` | Implemented |
| Ads, campaigns, ad sets, creatives, media uploads, ad insights | `ads.py` | Implemented |
| Page/Instagram organic insights | `insights.py` | Implemented |
| Page/Instagram comments and moderation | `comments.py` | Implemented |
| Messenger and Instagram messaging | `messaging.py` | Implemented |
| Lead forms and lead retrieval | `leads.py` | Implemented |
| Catalogs, products, product sets | `catalogs.py` | Implemented; requires an assigned catalog |
| Business portfolios and assignments | `business.py` | Implemented |
| Page app/webhook subscriptions | `metadata.py` | Implemented; callback configuration remains app-side |
| Threads basic reads | `threads.py` | Implemented; requires a compatible Threads identity/token |

## Granted-permission routing

- `ads_read`, `ads_management`, `pages_manage_ads`: `ads.py`
- `read_insights`, `instagram_manage_insights`: `insights.py`, `ads.py`
- `pages_manage_posts`, `pages_read_engagement`, `pages_read_user_content`: `pages.py`
- `instagram_basic`, `instagram_content_publish`, `instagram_manage_contents`: `instagram.py`
- `pages_manage_engagement`, `instagram_manage_comments`: `comments.py`
- `pages_messaging`, `pages_utility_messaging`, `paid_marketing_messages`, `instagram_manage_messages`: `messaging.py`
- `leads_retrieval`: `leads.py`
- `catalog_management`, `instagram_shopping_tag_products`: `catalogs.py`, Instagram publish options
- `business_management`, `pages_show_list`: `business.py`, `assets.py`
- `pages_manage_metadata`: `metadata.py`
- `threads_business_basic`: `threads.py`
- branded-content brand permissions: partnership-compatible Instagram publish options and ad creative specs

Reuse `meta_graph.py` for authentication, transport, pagination, validation, and error handling. Add explicit domain commands rather than a generic write endpoint. Require a distinct confirmation flag for every external write or deletion operation.
