# Facebook Page operations

All commands use `META_BUSINESS_PAGE_ID` unless `--page-id` is provided.

## Read

```bash
python3 scripts/pages.py info
python3 scripts/pages.py list --limit 10
python3 scripts/pages.py scheduled --limit 25
python3 scripts/pages.py visitor-posts --limit 25
python3 scripts/pages.py post POST_ID
```

## Dry run

```bash
python3 scripts/pages.py publish-text --message "Post text" --dry-run
python3 scripts/pages.py publish-photo --url "https://example.com/image.jpg" --caption "Caption" --dry-run
python3 scripts/pages.py publish-video --url "https://example.com/video.mp4" --description "Description" --dry-run
python3 scripts/pages.py publish-reel --url "https://example.com/video.mp4" --cover-file "cover.jpg" --description "Description" --dry-run
python3 scripts/pages.py publish-reel --file "reel.mp4" --cover-url "https://example.com/cover.jpg" --description "Description" --dry-run
python3 scripts/pages.py publish-text --message "Scheduled" --scheduled-publish-time "2026-09-01T10:00:00+08:00" --dry-run
python3 scripts/pages.py update-post POST_ID --message "Corrected text" --dry-run
python3 scripts/pages.py delete-post POST_ID --dry-run
```

## Publish

Run only after explicit user authorization:

```bash
python3 scripts/pages.py publish-text --message "Post text" --confirm-publish
python3 scripts/pages.py publish-text --message "Post text" --link "https://example.com" --confirm-publish
python3 scripts/pages.py publish-photo --url "https://example.com/image.jpg" --caption "Caption" --confirm-publish
python3 scripts/pages.py publish-video --url "https://example.com/video.mp4" --description "Description" --title "Title" --confirm-publish
python3 scripts/pages.py publish-reel --file "reel.mp4" --cover-file "cover.jpg" --description "Description" --confirm-publish
```

Page photo and video publishing accepts public HTTPS media URLs. Meta may process video asynchronously; use the returned ID with `post` to inspect it later.

Use `publish-reel` for the Page Reels resumable-upload workflow. Local videos are staged to configured R2 automatically. Covers accept either a local JPEG/PNG or a public HTTPS URL. The command starts a Reel container, asks Meta to fetch the video, uploads the cover as the preferred thumbnail, submits the finish request, waits for processing, and verifies the permalink and final state. Do not retry an ambiguous failed finish request automatically.

Use `--scheduled-publish-time` to create a scheduled unpublished post or `--unpublished` to create a draft. Updates and deletions use `--confirm-write`, not `--confirm-publish`.
