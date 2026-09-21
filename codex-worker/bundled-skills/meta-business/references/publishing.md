# Media staging and Reel cross-posting

Use Python 3. Never put access tokens or R2 secrets in command arguments.

## Preflight

```bash
python3 scripts/media_stage.py preflight "reel.mp4" --kind video
python3 scripts/media_stage.py preflight "cover.jpg" --kind cover
```

When `ffprobe` is installed, video preflight reports codec, dimensions, frame rate, duration, and Reels quality warnings. Prefer H.264, AAC, `yuv420p`, 1080x1920, 9:16, and a stable frame rate to reduce Meta recompression. A missing `ffprobe` produces a warning rather than silently claiming compatibility.

## Local-media staging

Meta fetches Instagram and Page Reel videos from public HTTPS URLs. Configure R2 with environment variables or matching keys in `~/.meta-business/credentials.json`:

- `META_MEDIA_R2_BUCKET`
- `META_MEDIA_R2_PREFIX` (optional; defaults to `marketing/meta-publish`)
- `META_MEDIA_R2_PUBLIC_BASE_URL` (optional; otherwise use a presigned URL)
- `META_MEDIA_R2_ACCOUNT_ID`, `META_MEDIA_R2_ACCESS_KEY_ID`, `META_MEDIA_R2_SECRET_ACCESS_KEY`

If the three R2 credential variables are absent, the helper can derive temporary R2 credentials in memory from the existing `~/.cloudflare/config.json`; the bucket name is still required. Never print or persist derived credentials. Set an R2 lifecycle rule separately if staged objects should expire automatically.

Verify bucket access without writing an object:

```bash
python3 scripts/media_stage.py check-staging
```

Standalone staging is an external write:

```bash
python3 scripts/media_stage.py stage "reel.mp4" --kind video --dry-run
python3 scripts/media_stage.py stage "reel.mp4" --kind video --confirm-write
```

## One Reel to both platforms

Always inspect first:

```bash
python3 scripts/crosspost.py --video "reel.mp4" --cover "cover.jpg" --instagram-caption "Link in bio." --page-caption "Shop: https://example.com" --share-to-feed --dry-run
```

After explicit authorization:

```bash
python3 scripts/crosspost.py --video "reel.mp4" --cover "cover.jpg" --instagram-caption "Link in bio." --page-caption "Shop: https://example.com" --share-to-feed --confirm-publish
```

The command stages each local file once, publishes to Instagram and the Page, and writes a non-secret operation JSON under `~/.codex/state/meta-business/operations/` by default. Override it with `META_BUSINESS_OPERATION_DIR`. Keep this path on persistent storage. It records per-platform results so one successful platform is not published twice when the other fails.

Always use separate copy. Instagram captions must not contain URLs because they are not clickable; use “Link in bio”. Facebook Page captions may contain the clickable destination URL. `--caption` remains for backward compatibility but should not be used for a two-platform post.

Captions and Page descriptions must contain real newline characters for paragraph breaks. Do not intentionally send visible literal `\\n` or `\\r\\n` text. The publishing scripts normalize those escaped sequences before computing the operation ID, persisting the operation, showing the dry-run payload, and calling Meta. Confirm the dry-run payload displays separate lines before live publishing.

Resume with the exact operation file:

```bash
python3 scripts/crosspost.py --resume ~/.codex/state/meta-business/operations/reel-OPERATION_ID.json --confirm-publish
```

If a process stopped during an external request, its state is ambiguous. The command refuses to retry it automatically. Inspect stored IDs and current Meta state first. Use `--retry-unknown` only after explicitly accepting the duplicate-publication risk.

## Result verification

Successful results include platform IDs, processing/container state, media product type, permalink, thumbnail metadata, and Page preferred-cover confirmation when Meta returns them. A Page Reel uses this fixed sequence:

`start → upload/fetch video → upload preferred cover → finish → wait → verify`
