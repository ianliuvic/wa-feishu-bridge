# Business assets, catalogs, subscriptions, and Threads

## Business portfolio

```bash
python3 scripts/business.py info
python3 scripts/business.py assets owned-ad-accounts
python3 scripts/business.py assets catalogs
python3 scripts/business.py assigned-users ASSET_ID
python3 scripts/business.py assign-user ASSET_ID --system-user-id USER_ID --tasks MANAGE,CREATE_CONTENT --dry-run
```

Task names depend on the asset type. Read current assignments first. Removing or changing an assignment can immediately break production access.

## Catalogs

```bash
python3 scripts/catalogs.py catalogs
python3 scripts/catalogs.py products
python3 scripts/catalogs.py product-sets
python3 scripts/catalogs.py batch-items --spec-file item-batch.json --dry-run
```

`batch-items` accepts the Meta `items_batch` request object with a non-empty `requests` array. Validate retailer IDs, prices, availability, URLs, and image URLs before confirmation. Catalog commands require an assigned catalog and a configured ID.

## Page subscriptions

```bash
python3 scripts/metadata.py subscriptions
python3 scripts/metadata.py subscribe --fields feed,messages,messaging_postbacks --dry-run
python3 scripts/metadata.py unsubscribe --dry-run
```

This controls whether the app is subscribed to Page events. Configure callback URL, verify token, subscribed objects, and webhook verification separately in the Meta app dashboard.

## Threads

```bash
python3 scripts/threads.py profile
python3 scripts/threads.py list --limit 25
python3 scripts/threads.py thread THREAD_ID
```

`threads_business_basic` alone supports basic reads, not publishing. A Threads-compatible user identity and access token may still be required even when the permission appears on another Meta token.
