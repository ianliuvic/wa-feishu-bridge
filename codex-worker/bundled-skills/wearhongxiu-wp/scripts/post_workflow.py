#!/usr/bin/env python3
"""Validate and publish a complete Wearhongxiu WordPress post package."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import mimetypes
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SITE = "https://wearhongxiu.com"
ALLOWED_HOSTS = {"wearhongxiu.com", "www.wearhongxiu.com"}
FEATURED_IMAGE_MARKER = "<!-- hx:featured-image -->"
CRUN_MODELS = {
    "openai/gpt-image-2",
    "grok-imagine-image-2.0",
    "google/nano-banana-2",
    "google/nano-banana-2-v2",
}
YOAST_KEYS = {
    "title": "_yoast_wpseo_title",
    "description": "_yoast_wpseo_metadesc",
    "focus_keyword": "_yoast_wpseo_focuskw",
    "schema_page_type": "_yoast_wpseo_schema_page_type",
    "schema_article_type": "_yoast_wpseo_schema_article_type",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wp = load_module("wearhongxiu_post_wp", SCRIPT_DIR / "wp.py")


class TextCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def plain_text(value: str) -> str:
    parser = TextCounter()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def words(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", plain_text(value)))


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def read_package(path: str) -> tuple[dict[str, Any], Path]:
    package_path = Path(path).resolve()
    data = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("post package must be a JSON object")
    return data, package_path


def resolve_file(package_path: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (package_path.parent / path).resolve()


def validate_package(data: dict[str, Any], package_path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = ("topic", "title", "slug", "excerpt", "content", "primary_keyword", "seo", "humanizer")
    for key in required:
        if not data.get(key):
            errors.append(f"missing required field: {key}")

    slug = str(data.get("slug") or "")
    if slug and (slug != slug.lower() or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)):
        errors.append("slug must use lowercase ASCII words separated by hyphens")

    title = plain_text(str(data.get("title") or ""))
    excerpt = plain_text(str(data.get("excerpt") or ""))
    content = str(data.get("content") or "")
    word_count = words(content)
    if title and not 25 <= len(title) <= 90:
        warnings.append(f"post title length is {len(title)} characters")
    if excerpt and not 80 <= len(excerpt) <= 240:
        warnings.append(f"excerpt length is {len(excerpt)} characters")
    if word_count < 700:
        warnings.append(f"article body is only {word_count} English-style words; review content depth")
    if re.search(r"<h1\b", content, re.I):
        errors.append("content must not contain H1; the WordPress post template owns the page H1")
    if "<!-- wp:" not in content:
        warnings.append("content does not contain WordPress block comments")
    marker_count = content.count(FEATURED_IMAGE_MARKER)
    if marker_count != 1:
        errors.append(
            f"content must contain exactly one {FEATURED_IMAGE_MARKER} marker at the most relevant image position"
        )

    humanizer = data.get("humanizer") if isinstance(data.get("humanizer"), dict) else {}
    if humanizer.get("skill") != "humanizer":
        errors.append("humanizer.skill must be 'humanizer'")
    if humanizer.get("applied") is not True:
        errors.append("the humanizer skill must be applied after drafting and before validation")
    reviewed_fields = humanizer.get("reviewed_fields") or []
    required_reviews = {"title", "excerpt", "content", "seo", "social"}
    if not isinstance(reviewed_fields, list) or not required_reviews.issubset(set(reviewed_fields)):
        errors.append("humanizer.reviewed_fields must include title, excerpt, content, seo, and social")

    seo = data.get("seo") if isinstance(data.get("seo"), dict) else {}
    for key in ("title", "description", "focus_keyword"):
        if not seo.get(key):
            errors.append(f"missing seo.{key}")
    seo_title = plain_text(str(seo.get("title") or ""))
    seo_description = plain_text(str(seo.get("description") or ""))
    if seo_title and not 30 <= len(seo_title) <= 65:
        warnings.append(f"SEO title length is {len(seo_title)} characters")
    if seo_description and not 120 <= len(seo_description) <= 165:
        warnings.append(f"meta description length is {len(seo_description)} characters")

    social = data.get("social") if isinstance(data.get("social"), dict) else {}
    final_copy = "\n".join([
        title,
        excerpt,
        content,
        seo_title,
        seo_description,
        *[str(value or "") for value in social.values()],
    ])
    if "—" in final_copy or "–" in final_copy:
        errors.append("humanized copy still contains em dash or en dash characters")
    ai_phrase_patterns = {
        "let's dive": r"\blet['’]s dive\b",
        "in today's competitive market": r"\bin today['’]s competitive market\b",
        "it is important to note": r"\bit is important to note\b",
        "not just ... but": r"\bnot just\b.{0,100}\bbut\b",
        "not only ... but also": r"\bnot only\b.{0,100}\bbut also\b",
        "generic conclusion": r"\bthe future (?:looks|is) bright\b",
    }
    for label, pattern in ai_phrase_patterns.items():
        if re.search(pattern, plain_text(final_copy), re.I | re.S):
            warnings.append(f"humanizer review: possible AI-writing pattern remains ({label})")

    category_ids = data.get("category_ids") or []
    category_names = data.get("category_names") or []
    if not category_ids and not category_names:
        errors.append("at least one existing category_id or category_name is required")
    if not isinstance(category_ids, list) or any(not isinstance(value, int) for value in category_ids):
        errors.append("category_ids must be an array of integers")
    if not isinstance(category_names, list) or any(not isinstance(value, str) for value in category_names):
        errors.append("category_names must be an array of strings")
    tag_names = data.get("tag_names") or []
    if not isinstance(tag_names, list) or any(not isinstance(value, str) for value in tag_names):
        errors.append("tag_names must be an array of strings")
    elif len(tag_names) > 8:
        warnings.append("more than 8 tags usually adds little value")

    featured = data.get("featured_image")
    if not isinstance(featured, dict):
        errors.append("featured_image object is required")
    else:
        for key in ("path", "title", "alt_text", "generator"):
            if not featured.get(key):
                errors.append(f"missing featured_image.{key}")
        generator = str(featured.get("generator") or "")
        is_industry_news = data.get("editorial_type") == "industry-news"
        if generator == "crun-imagen":
            for key in ("model", "prompt"):
                if not featured.get(key):
                    errors.append(f"missing featured_image.{key}")
            if featured.get("model") and featured.get("model") not in CRUN_MODELS:
                errors.append(f"unsupported featured_image.model: {featured.get('model')}")
        elif generator == "source-image" and is_industry_news:
            for key in ("source_url", "source_credit"):
                if not featured.get(key):
                    errors.append(f"missing featured_image.{key}")
            source_url = urllib.parse.urlparse(str(featured.get("source_url") or ""))
            if source_url.scheme != "https" or not source_url.hostname:
                errors.append("featured_image.source_url must be a public HTTPS URL")
        elif generator:
            errors.append("featured_image.generator must be 'crun-imagen', or 'source-image' for industry news")
        alt_text = plain_text(str(featured.get("alt_text") or ""))
        if alt_text and len(alt_text) > 160:
            warnings.append("featured image alt text is longer than 160 characters")
        prompt = plain_text(str(featured.get("prompt") or ""))
        if prompt and len(prompt) < 40:
            errors.append("featured_image.prompt is too short to establish a content-specific image brief")
        if featured.get("path"):
            image_path = resolve_file(package_path, str(featured["path"]))
            if not image_path.is_file():
                errors.append(f"featured image does not exist: {image_path}")
            elif image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                errors.append("featured image must be JPG, PNG, or WebP")

    internal_links = data.get("internal_links") or []
    if not isinstance(internal_links, list) or len(internal_links) < 2:
        errors.append("at least two reviewed internal_links are required")
    else:
        for index, item in enumerate(internal_links, 1):
            if not isinstance(item, dict):
                errors.append(f"internal_links[{index}] must be an object")
                continue
            url, anchor = str(item.get("url") or ""), str(item.get("anchor") or "")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
                errors.append(f"internal_links[{index}] is not a canonical Wearhongxiu HTTPS URL")
            if not anchor.strip():
                errors.append(f"internal_links[{index}] is missing anchor text")
            if url and url not in content:
                errors.append(f"internal link is not present in content: {url}")
            if anchor and plain_text(anchor).lower() in {"click here", "read more", "learn more"}:
                warnings.append(f"generic internal-link anchor: {anchor}")
    return errors, warnings


def wp_all(client, route: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
    page, output = 1, []
    while True:
        values = {**(params or {}), "page": str(page), "per_page": "100"}
        items, headers = client.request("GET", route, params=values)
        if not isinstance(items, list):
            raise RuntimeError(f"expected list from {route}")
        output.extend(items)
        if page >= int(headers.get("x-wp-totalpages") or page):
            return output
        page += 1


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
            raise RuntimeError("refusing cross-site redirect while validating internal links")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def live_checks(client, data: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    slug = str(data.get("slug") or "")
    if slug:
        existing, _ = client.request(
            "GET", "/wp/v2/posts", params={"slug": slug, "context": "edit", "status": "any", "per_page": "10"}
        )
        if existing:
            errors.append(f"a WordPress post already uses slug '{slug}' (ID {existing[0].get('id')})")

    categories = wp_all(client, "/wp/v2/categories", {"hide_empty": "false"})
    by_id = {int(item["id"]) for item in categories}
    by_name = {str(item.get("name") or "").casefold() for item in categories}
    for value in data.get("category_ids") or []:
        if value not in by_id:
            errors.append(f"WordPress category ID does not exist: {value}")
    for value in data.get("category_names") or []:
        if value.casefold() not in by_name:
            errors.append(f"WordPress category does not exist: {value}")

    opener = urllib.request.build_opener(PublicRedirectHandler())
    for item in data.get("internal_links") or []:
        url = str(item.get("url") or "")
        if not url:
            continue
        request = urllib.request.Request(url, headers={"User-Agent": "Codex-Wearhongxiu-Post-Validator/1.0"})
        try:
            with opener.open(request, timeout=20) as response:
                if response.status != 200:
                    errors.append(f"internal link returned HTTP {response.status}: {url}")
                if response.geturl().rstrip("/") != url.rstrip("/"):
                    warnings.append(f"internal link redirects to {response.geturl()}: {url}")
        except urllib.error.HTTPError as exc:
            errors.append(f"internal link returned HTTP {exc.code}: {url}")
        except urllib.error.URLError as exc:
            warnings.append(f"could not verify internal link {url}: {exc.reason}")
    return errors, warnings


def resolve_categories(client, data: dict[str, Any]) -> list[int]:
    categories = wp_all(client, "/wp/v2/categories", {"hide_empty": "false"})
    by_name = {str(item.get("name") or "").casefold(): int(item["id"]) for item in categories}
    values = [int(value) for value in data.get("category_ids") or []]
    for name in data.get("category_names") or []:
        category_id = by_name.get(name.casefold())
        if category_id is None:
            raise RuntimeError(f"category does not exist: {name}")
        values.append(category_id)
    return list(dict.fromkeys(values))


def resolve_tags(client, names: list[str]) -> list[int]:
    values: list[int] = []
    for name in dict.fromkeys(value.strip() for value in names if value.strip()):
        matches, _ = client.request(
            "GET", "/wp/v2/tags", params={"search": name, "hide_empty": "false", "per_page": "100"}
        )
        exact = next((item for item in matches if str(item.get("name") or "").casefold() == name.casefold()), None)
        if exact is None:
            exact, _ = client.request("POST", "/wp/v2/tags", data={"name": name})
        values.append(int(exact["id"]))
    return values


def upload_featured_image(client, data: dict[str, Any], package_path: Path) -> dict[str, Any]:
    featured = data["featured_image"]
    path = resolve_file(package_path, str(featured["path"]))
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    media, _ = client.request(
        "POST", "/wp/v2/media", data=path.read_bytes(),
        headers={"Content-Type": mime, "Content-Disposition": f'attachment; filename="{path.name.replace(chr(34), "")}"'},
    )
    return update_featured_media(client, media, featured)


def update_featured_media(client, media: dict[str, Any], featured: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "title": featured["title"],
        "alt_text": featured["alt_text"],
    }
    for key in ("caption", "description"):
        if featured.get(key):
            metadata[key] = featured[key]
    media, _ = client.request("POST", f"/wp/v2/media/{media['id']}", data=metadata)
    return media


def reuse_featured_image(client, media_id: int, data: dict[str, Any]) -> dict[str, Any]:
    media, _ = client.request("GET", f"/wp/v2/media/{media_id}", params={"context": "edit"})
    parsed = urllib.parse.urlparse(str(media.get("source_url") or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise RuntimeError(f"media {media_id} is not hosted by Wearhongxiu")
    return update_featured_media(client, media, data["featured_image"])


def inline_featured_image(content: str, media: dict[str, Any], featured: dict[str, Any]) -> str:
    """Replace the authored placement marker with a Gutenberg image block."""
    media_id = int(media["id"])
    sizes = ((media.get("media_details") or {}).get("sizes") or {})
    selected = sizes.get("large") or sizes.get("full") or {}
    source_url = selected.get("source_url") or media.get("source_url")
    if not source_url:
        raise RuntimeError("uploaded featured media did not return a usable source URL")
    attributes = [
        f'src="{html.escape(str(source_url), quote=True)}"',
        f'alt="{html.escape(str(featured["alt_text"]), quote=True)}"',
        f'class="wp-image-{media_id}"',
    ]
    if selected.get("width"):
        attributes.append(f'width="{int(selected["width"])}"')
    if selected.get("height"):
        attributes.append(f'height="{int(selected["height"])}"')
    caption = str(featured.get("caption") or "").strip()
    caption_html = (
        f'<figcaption class="wp-element-caption">{html.escape(caption)}</figcaption>' if caption else ""
    )
    block = (
        f'<!-- wp:image {{"id":{media_id},"sizeSlug":"large","linkDestination":"none"}} -->\n'
        f'<figure class="wp-block-image size-large"><img {" ".join(attributes)}/>{caption_html}</figure>\n'
        '<!-- /wp:image -->'
    )
    if content.count(FEATURED_IMAGE_MARKER) != 1:
        raise RuntimeError("cannot insert featured image: placement marker is missing or duplicated")
    return content.replace(FEATURED_IMAGE_MARKER, block)


def crun_script_path() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    script = codex_home / "skills" / "crun-imagen" / "scripts" / "crun_imagen.py"
    if not script.is_file():
        raise RuntimeError(f"crun-imagen skill script is not installed: {script}")
    return script


def yoast_meta(data: dict[str, Any]) -> dict[str, str]:
    seo = data["seo"]
    defaults = {"schema_page_type": "WebPage", "schema_article_type": "BlogPosting"}
    return {
        field: str(seo.get(key) or defaults.get(key) or "")
        for key, field in YOAST_KEYS.items()
        if seo.get(key) or defaults.get(key)
    }


def verify_post(
    client, post_id: int, expected_status: str, data: dict[str, Any], media_id: int, expected_content: str
) -> dict[str, Any]:
    post, _ = client.request("GET", f"/wp/v2/posts/{post_id}", params={"context": "edit"})
    returned_content = str((post.get("content") or {}).get("raw") or "")
    checks = {
        "status": post.get("status") == expected_status,
        "slug": post.get("slug") == data["slug"],
        "featured_media": int(post.get("featured_media") or 0) == media_id,
        "title": plain_text(str((post.get("title") or {}).get("raw") or (post.get("title") or {}).get("rendered") or "")) == plain_text(data["title"]),
        "content_present": len(returned_content) > 100,
        "inline_featured_image": f"wp-image-{media_id}" in returned_content,
        "image_marker_resolved": FEATURED_IMAGE_MARKER not in returned_content,
        "content_length": len(returned_content) >= len(expected_content) * 0.95,
    }
    returned_meta = post.get("meta") or {}
    for field, expected in yoast_meta(data).items():
        checks[f"meta:{field}"] = str(returned_meta.get(field) or "") == expected
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise RuntimeError(f"WordPress readback verification failed: {', '.join(failed)}")
    return {"id": post_id, "status": post.get("status"), "link": post.get("link"), "checks": checks}


def command_template(args) -> None:
    output = Path(args.output).resolve()
    if output.exists() and not args.force:
        raise RuntimeError(f"refusing to overwrite existing file: {output}")
    data = {
        "version": 1,
        "topic": args.topic,
        "title": args.topic,
        "slug": slugify(args.topic),
        "excerpt": "",
        "content": FEATURED_IMAGE_MARKER,
        "primary_keyword": "",
        "search_intent": "informational",
        "seo": {
            "title": "",
            "description": "",
            "focus_keyword": "",
            "schema_page_type": "WebPage",
            "schema_article_type": "BlogPosting",
        },
        "humanizer": {
            "skill": "humanizer",
            "applied": False,
            "reviewed_fields": [],
            "remaining_tells": [],
        },
        "category_names": ["Blog"],
        "category_ids": [],
        "tag_names": [],
        "featured_image": {
            "generator": "crun-imagen",
            "model": "openai/gpt-image-2",
            "prompt": "",
            "path": "",
            "title": "",
            "alt_text": "",
            "caption": "",
        },
        "internal_links": [],
        "source_urls": [],
        "social": {"facebook": "", "instagram": "", "linkedin": ""},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"created": str(output), "topic": args.topic}, ensure_ascii=False, indent=2))


def command_validate(args) -> None:
    data, package_path = read_package(args.package)
    errors, warnings = validate_package(data, package_path)
    if args.live and not errors:
        live_errors, live_warnings = live_checks(wp.Client(wp.config(args.config)), data)
        errors.extend(live_errors)
        warnings.extend(live_warnings)
    result = {
        "valid": not errors,
        "package": str(package_path),
        "word_count": words(str(data.get("content") or "")),
        "errors": errors,
        "warnings": warnings,
        "live_checked": bool(args.live and not errors),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise RuntimeError(f"post package validation failed with {len(errors)} error(s)")


def command_generate_image(args) -> None:
    if not args.yes:
        raise RuntimeError("Crun image generation consumes credits; re-run with --yes")
    data, package_path = read_package(args.package)
    featured = data.get("featured_image") if isinstance(data.get("featured_image"), dict) else {}
    if (data.get("humanizer") or {}).get("applied") is not True:
        raise RuntimeError("finish and humanize the article before generating its image")
    if str(data.get("content") or "").count(FEATURED_IMAGE_MARKER) != 1:
        raise RuntimeError(f"place exactly one {FEATURED_IMAGE_MARKER} marker in the article first")
    for key in ("title", "alt_text", "prompt"):
        if not str(featured.get(key) or "").strip():
            raise RuntimeError(f"featured_image.{key} is required before image generation")
    if featured.get("generator") != "crun-imagen":
        raise RuntimeError("featured_image.generator must be 'crun-imagen'")
    model = str(featured.get("model") or "openai/gpt-image-2")
    if model not in CRUN_MODELS:
        raise RuntimeError(f"unsupported Crun model: {model}")

    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else package_path.parent / "output" / "posts" / str(data.get("slug") or "post")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = list(output_dir.glob("crun-image-*"))
    if existing and not args.force:
        raise RuntimeError(f"Crun output already exists in {output_dir}; use --force to replace it")

    command = [
        sys.executable,
        str(crun_script_path()),
        "create",
        "--prompt",
        str(featured["prompt"]),
        "--model",
        model,
        "--aspect-ratio",
        "16:9",
        "--wait",
        "--output-dir",
        str(output_dir),
    ]
    if model != "grok-imagine-image-2.0":
        command.extend(["--resolution", "2K"])
    completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"Crun image generation failed: {(completed.stderr or completed.stdout).strip()}")
    result = json.loads(completed.stdout)
    saved_files = result.get("saved_files") or []
    if len(saved_files) != 1:
        raise RuntimeError(f"expected one generated image, received {len(saved_files)}")
    generated = Path(saved_files[0]).resolve()
    target = output_dir / f"{data.get('slug') or 'post'}-featured{generated.suffix.lower()}"
    if target.exists() and target != generated:
        if not args.force:
            raise RuntimeError(f"refusing to overwrite existing image: {target}")
        target.unlink()
    if generated != target:
        generated.replace(target)
    try:
        featured["path"] = str(target.relative_to(package_path.parent))
    except ValueError:
        featured["path"] = str(target)
    featured["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["featured_image"] = featured
    package_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "generated": str(target),
        "package_updated": str(package_path),
        "generator": "crun-imagen",
        "model": model,
        "alt_text": featured["alt_text"],
    }, ensure_ascii=False, indent=2))


def command_apply(args) -> None:
    if not args.yes:
        raise RuntimeError("apply changes live WordPress; re-run with --yes")
    data, package_path = read_package(args.package)
    errors, warnings = validate_package(data, package_path)
    client = wp.Client(wp.config(args.config))
    live_errors, live_warnings = live_checks(client, data) if not errors else ([], [])
    errors.extend(live_errors)
    warnings.extend(live_warnings)
    if errors:
        raise RuntimeError("post package validation failed: " + "; ".join(errors))

    categories = resolve_categories(client, data)
    tags = resolve_tags(client, data.get("tag_names") or [])
    media = (
        reuse_featured_image(client, args.media_id, data)
        if args.media_id
        else upload_featured_image(client, data, package_path)
    )
    post_content = inline_featured_image(data["content"], media, data["featured_image"])
    payload = {
        "title": data["title"],
        "slug": data["slug"],
        "excerpt": data["excerpt"],
        "content": post_content,
        "status": "draft",
        "categories": categories,
        "tags": tags,
        "featured_media": int(media["id"]),
        "meta": yoast_meta(data),
    }
    post, _ = client.request("POST", "/wp/v2/posts", data=payload)
    post_id = int(post["id"])
    status = "draft"
    if args.publish:
        post, _ = client.request("POST", f"/wp/v2/posts/{post_id}", data={"status": "publish"})
        status = "publish"
    verified = verify_post(client, post_id, status, data, int(media["id"]), post_content)

    rag_result: dict[str, Any] | None = None
    if args.sync_rag:
        if status != "publish":
            raise RuntimeError("RAG sync requires --publish because drafts are not public knowledge")
        command = [sys.executable, str(SCRIPT_DIR / "rag_sync.py")]
        if args.config:
            command.extend(["--config", args.config])
        command.extend(["sync-post", str(post_id)])
        completed = subprocess.run(command, text=True, capture_output=True, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(
                f"post {post_id} was published but RAG sync failed: {(completed.stderr or completed.stdout).strip()}"
            )
        rag_result = json.loads(completed.stdout)

    print(json.dumps({
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "post": verified,
        "featured_media": {"id": media.get("id"), "url": media.get("source_url")},
        "categories": categories,
        "tags": tags,
        "warnings": warnings,
        "rag": rag_result,
        "social_copy_prepared": bool(any((data.get("social") or {}).values())),
        "social_published": False,
    }, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Wearhongxiu end-to-end post publishing workflow")
    root.add_argument("--config", help="Wearhongxiu WordPress config.env")
    sub = root.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="create a post-package scaffold from a topic")
    template.add_argument("topic")
    template.add_argument("--output", required=True)
    template.add_argument("--force", action="store_true")
    template.set_defaults(func=command_template)

    validate = sub.add_parser("validate", help="validate a completed post package")
    validate.add_argument("package")
    validate.add_argument("--live", action="store_true", help="check slug, categories, and internal URLs against WordPress")
    validate.set_defaults(func=command_validate)

    generate = sub.add_parser("generate-image", help="generate the package image through the crun-imagen skill")
    generate.add_argument("package")
    generate.add_argument("--output-dir")
    generate.add_argument("--yes", action="store_true")
    generate.add_argument("--force", action="store_true")
    generate.set_defaults(func=command_generate_image)

    apply = sub.add_parser("apply", help="create the verified package as a WordPress draft or published post")
    apply.add_argument("package")
    apply.add_argument("--yes", action="store_true")
    apply.add_argument("--publish", action="store_true", help="promote the newly created draft to publish")
    apply.add_argument("--sync-rag", action="store_true", help="ingest the published post into website RAG")
    apply.add_argument("--media-id", type=int, help="resume with an already uploaded Wearhongxiu media item")
    apply.set_defaults(func=command_apply)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.func(args)
    except (RuntimeError, wp.ApiError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
