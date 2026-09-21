# Instagram professional account operations

All commands use `META_BUSINESS_IG_USER_ID` unless `--ig-user-id` is provided.

## Read

```bash
python3 scripts/instagram.py info
python3 scripts/instagram.py list --limit 10
python3 scripts/instagram.py publishing-limit
python3 scripts/instagram.py status CREATION_ID
python3 scripts/instagram.py media MEDIA_ID
```

## Dry run

```bash
python3 scripts/instagram.py publish-image --image-url "https://example.com/image.jpg" --caption "Caption" --dry-run
python3 scripts/instagram.py publish-carousel --image-url "https://example.com/1.jpg" --image-url "https://example.com/2.jpg" --caption "Caption" --dry-run
python3 scripts/instagram.py publish-reel --video-url "https://example.com/video.mp4" --caption "Caption" --share-to-feed --dry-run
python3 scripts/instagram.py publish-reel --video-url "https://example.com/video.mp4" --cover-url "https://example.com/cover.jpg" --caption "Caption" --share-to-feed --dry-run
python3 scripts/instagram.py publish-reel --video-file "reel.mp4" --cover-file "cover.jpg" --caption "Caption" --share-to-feed --dry-run
```

## Publish

Run only after explicit user authorization:

```bash
python3 scripts/instagram.py publish-image --image-url "https://example.com/image.jpg" --caption "Caption" --confirm-publish
python3 scripts/instagram.py publish-carousel --image-url "https://example.com/1.jpg" --image-url "https://example.com/2.jpg" --caption "Caption" --confirm-publish
python3 scripts/instagram.py publish-reel --video-url "https://example.com/video.mp4" --caption "Caption" --share-to-feed --confirm-publish
python3 scripts/instagram.py publish-reel --video-file "reel.mp4" --cover-file "cover.jpg" --caption "Caption" --share-to-feed --confirm-publish
```

Instagram fetches media from public URLs. Local Reel videos and covers are staged to configured R2 automatically. Use stable public HTTPS URLs without authentication, redirects requiring cookies, or rapidly expiring signatures. The script waits for containers to finish and returns the verified media object, product type, thumbnail, and permalink.

Do not include URLs in Instagram captions; they are not clickable. Use “Link in bio” or equivalent wording. Publishing commands reject captions containing `http://`, `https://`, or `www.`.

For Reel covers, use `--cover-url` with a public HTTPS JPEG/PNG. For user tags, location, product tags, shopping, or supported branded-content fields, provide an `--options-file` JSON object. Allowed keys are `user_tags`, `location_id`, `product_tags`, `is_branded_content`, `branded_content_sponsor_page_id`, `thumb_offset`, `cover_url`, and `copyright`. Preview it with `--dry-run`; Meta still validates whether the account, media type, product catalog, sponsor, and permission combination is eligible.
