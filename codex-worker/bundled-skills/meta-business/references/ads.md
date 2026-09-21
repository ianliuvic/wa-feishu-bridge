# Meta advertising operations

Use `META_BUSINESS_AD_ACCOUNT_ID` unless `--ad-account-id` is supplied.

## Read

```bash
python3 scripts/ads.py accounts
python3 scripts/ads.py account
python3 scripts/ads.py campaigns --limit 25
python3 scripts/ads.py adsets --limit 25
python3 scripts/ads.py ads --limit 25
python3 scripts/ads.py creatives --limit 25
python3 scripts/ads.py images --limit 25
python3 scripts/ads.py videos --limit 25
python3 scripts/ads.py video-status VIDEO_ID
python3 scripts/ads.py audiences --limit 25
python3 scripts/ads.py pixels --limit 25
python3 scripts/ads.py targeting-search --type adinterest --query swimwear
python3 scripts/ads.py delivery-estimate --spec-file delivery-estimate.json
python3 scripts/ads.py insights --level campaign --date-preset last_30d
python3 scripts/ads.py insights --level ad --since 2026-08-01 --until 2026-08-25
python3 scripts/ads.py previews CREATIVE_ID --format INSTAGRAM_STANDARD
```

## Write

Put a single typed payload in a UTF-8 JSON file. Never include a token. Supported object types are `campaign`, `adset`, `creative`, and `ad`.

```bash
python3 scripts/ads.py create campaign --spec-file campaign.json --dry-run
python3 scripts/ads.py create campaign --spec-file campaign.json --confirm-write
python3 scripts/ads.py update adset ADSET_ID --spec-file adset-update.json --dry-run
python3 scripts/ads.py archive ad AD_ID --dry-run
```

## Ad media

Inspect the resolved file and upload request first:

```bash
python3 scripts/ads.py upload-image --file "creative.jpg" --name "Summer creative" --dry-run
python3 scripts/ads.py upload-video --file "creative.mp4" --title "Summer reel" --dry-run
python3 scripts/ads.py upload-video --url "https://example.com/creative.mp4" --title "Summer reel" --dry-run
```

After explicit authorization, repeat with `--confirm-write`. Image uploads accept local JPEG or PNG files and return an `image_hash`. Local videos are streamed to the video upload host and return a `video_id`; use `video-status` until processing finishes. Remote video URLs must be public HTTPS URLs. Do not retry an ambiguous upload automatically—list images/videos first to avoid duplicates.

Use the returned material in a creative spec. An image creative normally references `object_story_spec.link_data.image_hash`; a video creative references `object_story_spec.video_data.video_id`. Also set the exact Page, Instagram identity, destination link, copy, CTA, and thumbnail required by the creative. Preview the creative and keep the campaign, ad set, and ad paused until the user explicitly requests activation.

Deletion is also guarded:

```bash
python3 scripts/ads.py delete-image IMAGE_HASH --dry-run
python3 scripts/ads.py delete-video VIDEO_ID --dry-run
```

Create objects paused unless the user explicitly requests activation. Verify campaign objective, special-ad category, budgets in minor currency units, schedule, targeting, optimization, placements, promoted object, tracking, and creative before a live call. Creation is multi-step: campaign, then ad set, then creative, then ad. Never retry an ambiguous create automatically.

The permission does not grant billing-method management, policy bypasses, unrestricted custom-audience use, or access to unassigned assets. Meta may reject combinations that are invalid for the account, objective, region, or API version.
