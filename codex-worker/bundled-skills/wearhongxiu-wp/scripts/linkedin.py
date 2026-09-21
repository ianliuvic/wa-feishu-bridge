#!/usr/bin/env python3
"""Publish Wearhongxiu article links to an authorized LinkedIn member account."""

from __future__ import annotations

import argparse
from io import BytesIO
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


TOKEN_PATH = Path(
    os.getenv("LINKEDIN_TOKEN_PATH", str(Path.home() / ".codex/linkedin/oauth.json"))
).expanduser()
CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
API_VERSION = os.getenv("LINKEDIN_API_VERSION", datetime.now(timezone.utc).strftime("%Y%m"))
POSTS_URL = "https://api.linkedin.com/rest/posts"
IMAGES_URL = "https://api.linkedin.com/rest/images?action=initializeUpload"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


class LinkedInError(RuntimeError):
    pass


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def load_token() -> dict[str, Any]:
    if not TOKEN_PATH.is_file():
        raise LinkedInError("LinkedIn is not authorized; complete OAuth authorization first")
    try:
        value = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LinkedInError("LinkedIn token storage is unreadable") from exc
    if not isinstance(value, dict) or not value.get("access_token") or not value.get("person_urn"):
        raise LinkedInError("LinkedIn authorization is incomplete")
    return value


def save_token(value: dict[str, Any]) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = TOKEN_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(TOKEN_PATH)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    access_token: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": "HongxiuLinkedInPublisher/1.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "LinkedIn-Version": API_VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
            }
        )
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            response_headers = {key.lower(): val for key, val in response.headers.items()}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise LinkedInError(f"LinkedIn API HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LinkedInError(f"LinkedIn API unavailable: {type(exc).__name__}") from exc
    if not raw:
        return {}, response_headers
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LinkedInError("LinkedIn returned invalid JSON") from exc
    return value if isinstance(value, dict) else {}, response_headers


def upload_binary(
    url: str,
    data: bytes,
    *,
    content_type: str,
    access_token: str,
) -> None:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "LinkedIn-Version": API_VERSION,
        "User-Agent": "HongxiuLinkedInPublisher/1.0",
    }
    request = Request(url, data=data, headers=headers, method="PUT")
    try:
        with urlopen(request, timeout=90) as response:
            response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise LinkedInError(f"LinkedIn image upload HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LinkedInError(f"LinkedIn image upload unavailable: {type(exc).__name__}") from exc


def refresh_if_needed(token: dict[str, Any]) -> dict[str, Any]:
    expires_at = int(token.get("expires_at") or 0)
    if not expires_at or expires_at > int(time.time()) + 600:
        return token
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        raise LinkedInError("LinkedIn access token has expired and no refresh token is available")
    if not CLIENT_ID or not CLIENT_SECRET:
        raise LinkedInError("LinkedIn client credentials are unavailable for token refresh")
    refreshed, _ = request_json(
        TOKEN_URL,
        method="POST",
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    access_token = str(refreshed.get("access_token") or "")
    if not access_token:
        raise LinkedInError("LinkedIn token refresh returned no access token")
    token["access_token"] = access_token
    token["expires_at"] = int(time.time()) + int(refreshed.get("expires_in") or 0)
    if refreshed.get("refresh_token"):
        token["refresh_token"] = refreshed["refresh_token"]
    save_token(token)
    return token


def validate_article_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"wearhongxiu.com", "www.wearhongxiu.com"}:
        raise LinkedInError("article URL must be an HTTPS Wearhongxiu URL")
    return value.strip()


def tracked_article_url(article_url: str) -> str:
    parsed = urlparse(article_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": "linkedin",
            "utm_medium": "organic_social",
            "utm_campaign": "blog_distribution",
            "utm_content": parsed.path.strip("/").split("/")[-1] or "homepage",
        }
    )
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def clean_commentary(text: str) -> str:
    copy = text.replace("\\r\\n", "\n").replace("\\n", "\n").strip()
    if not copy:
        raise LinkedInError("LinkedIn copy is empty")
    copy = re.sub(r"https?://(?:www\.)?wearhongxiu\.com/\S*", "", copy, flags=re.I)
    copy = re.sub(r"[ \t]+\n", "\n", copy)
    copy = re.sub(r"\n{3,}", "\n\n", copy).strip()
    if len(copy) > 900:
        raise LinkedInError(f"LinkedIn copy is {len(copy)} characters before the URL; maximum is 900")
    if "\\n" in copy:
        raise LinkedInError("LinkedIn copy contains a literal escaped newline")
    return copy


def build_commentary(text: str, article_url: str) -> str:
    return f"{clean_commentary(text)}\n\n{tracked_article_url(article_url)}"


def prepare_image_upload(path_value: str) -> tuple[bytes, str]:
    path = Path(path_value)
    if not path.is_file():
        raise LinkedInError(f"article thumbnail does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        data, content_type = path.read_bytes(), "image/jpeg"
    elif suffix == ".png":
        data, content_type = path.read_bytes(), "image/png"
    elif suffix == ".gif":
        data, content_type = path.read_bytes(), "image/gif"
    elif suffix == ".webp":
        try:
            from PIL import Image
        except ImportError as exc:
            raise LinkedInError("Pillow is required to convert a WebP thumbnail for LinkedIn") from exc
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                output = BytesIO()
                image.save(output, format="JPEG", quality=92, optimize=True)
                data, content_type = output.getvalue(), "image/jpeg"
        except OSError as exc:
            raise LinkedInError("article thumbnail could not be converted from WebP") from exc
    else:
        raise LinkedInError("article thumbnail must be JPEG, PNG, GIF, or WebP")
    if not data:
        raise LinkedInError("article thumbnail is empty")
    if len(data) > 36 * 1024 * 1024:
        raise LinkedInError("article thumbnail exceeds LinkedIn's 36 MB image limit")
    return data, content_type


def upload_article_thumbnail(path_value: str, *, owner: str, access_token: str) -> str:
    initialized, _ = request_json(
        IMAGES_URL,
        method="POST",
        payload={"initializeUploadRequest": {"owner": owner}},
        access_token=access_token,
    )
    value = initialized.get("value")
    if not isinstance(value, dict):
        raise LinkedInError("LinkedIn image initialization returned no value")
    upload_url = str(value.get("uploadUrl") or "")
    image_urn = str(value.get("image") or "")
    if not upload_url or not image_urn.startswith("urn:li:image:"):
        raise LinkedInError("LinkedIn image initialization returned incomplete upload data")
    data, content_type = prepare_image_upload(path_value)
    upload_binary(upload_url, data, content_type=content_type, access_token=access_token)
    return image_urn


def command_status(_: argparse.Namespace) -> None:
    try:
        token = load_token()
    except LinkedInError as exc:
        print_json({"configured": bool(CLIENT_ID and CLIENT_SECRET), "authorized": False, "reason": str(exc)})
        return
    expires_at = int(token.get("expires_at") or 0)
    print_json(
        {
            "configured": bool(CLIENT_ID and CLIENT_SECRET),
            "authorized": True,
            "name": token.get("name") or "",
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at <= int(time.time())),
            "refresh_available": bool(token.get("refresh_token")),
        }
    )


def command_publish(args: argparse.Namespace) -> None:
    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    article_url = validate_article_url(args.url)
    linkedin_url = tracked_article_url(article_url)
    use_article_preview = bool(args.thumbnail_file or args.article_title or args.article_description)
    if args.plain_link and use_article_preview:
        raise LinkedInError("--plain-link cannot be combined with article-preview options")
    if use_article_preview and not all(
        (args.thumbnail_file, args.article_title, args.article_description)
    ):
        raise LinkedInError(
            "--thumbnail-file, --article-title, and --article-description are all required for article preview"
        )
    if not use_article_preview and not args.plain_link:
        raise LinkedInError(
            "article preview is required; provide --thumbnail-file, --article-title, and "
            "--article-description (or explicitly request --plain-link)"
        )
    commentary = clean_commentary(text or "") if use_article_preview else build_commentary(text or "", article_url)
    if use_article_preview:
        prepare_image_upload(args.thumbnail_file)
    if not args.yes:
        print_json(
            {
                "dry_run": True,
                "action": "publish_linkedin_member_post",
                "article_url": article_url,
                "article_preview": use_article_preview,
                "commentary_characters": len(commentary),
            }
        )
        return
    token = refresh_if_needed(load_token())
    payload = {
        "author": token["person_urn"],
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if use_article_preview:
        thumbnail = upload_article_thumbnail(
            args.thumbnail_file,
            owner=str(token["person_urn"]),
            access_token=str(token["access_token"]),
        )
        payload["content"] = {
            "article": {
                "source": linkedin_url,
                "title": args.article_title.strip(),
                "description": args.article_description.strip(),
                "thumbnail": thumbnail,
            }
        }
    response, headers = request_json(
        POSTS_URL,
        method="POST",
        payload=payload,
        access_token=str(token["access_token"]),
    )
    post_id = headers.get("x-restli-id") or response.get("id") or ""
    print_json(
        {
            "success": True,
            "platform": "linkedin",
            "account_type": "member",
            "post_id": post_id,
            "article_url": article_url,
            "linkedin_url": linkedin_url,
            "article_preview": use_article_preview,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Show authorization status without exposing tokens")
    status.set_defaults(func=command_status)
    publish = commands.add_parser("publish", help="Publish one member post containing a Wearhongxiu URL")
    text = publish.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--text-file")
    publish.add_argument("--url", required=True)
    publish.add_argument("--thumbnail-file")
    publish.add_argument("--article-title")
    publish.add_argument("--article-description")
    publish.add_argument(
        "--plain-link",
        action="store_true",
        help="Explicitly publish without an article preview card",
    )
    publish.add_argument("--yes", action="store_true")
    publish.set_defaults(func=command_publish)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (LinkedInError, OSError) as exc:
        print_json({"success": False, "error": str(exc)})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
