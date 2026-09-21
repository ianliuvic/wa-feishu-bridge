#!/usr/bin/env python3
"""Audit, plan, validate, and safely apply Wearhongxiu SEO changes."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# DSH strips credential-shaped variables from the shell environment it gives
# child processes, so pull this skill's credentials from the harness-managed
# file when they are not already present. No-op on a Codex worker.
try:
    from skill_credentials import load as _load_skill_credentials
    _load_skill_credentials("WEARHONGXIU_PAGESPEED_API_KEY")
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SITE = "https://wearhongxiu.com"
ALLOWED_HOSTS = {"wearhongxiu.com", "www.wearhongxiu.com"}
YOAST_KEYS = {
    "title": "_yoast_wpseo_title",
    "description": "_yoast_wpseo_metadesc",
    "focus_keyword": "_yoast_wpseo_focuskw",
    "canonical": "_yoast_wpseo_canonical",
    "noindex": "_yoast_wpseo_meta-robots-noindex",
    "nofollow": "_yoast_wpseo_meta-robots-nofollow",
    "schema_page_type": "_yoast_wpseo_schema_page_type",
    "schema_article_type": "_yoast_wpseo_schema_article_type",
}
SCHEMA_PAGE_TYPES = {
    "WebPage", "AboutPage", "ContactPage", "FAQPage", "ItemPage",
    "CheckoutPage", "SearchResultsPage", "RealEstateListing",
}
SCHEMA_ARTICLE_TYPES = {
    "Article", "BlogPosting", "NewsArticle", "AdvertiserContentArticle",
    "SatiricalArticle", "ScholarlyArticle", "TechArticle", "Report",
    "SocialMediaPosting",
}
SUPPORTED_SCHEMA = {"Organization", "Article", "BlogPosting", "Product", "FAQPage", "BreadcrumbList"}
OPERATIONAL_SLUGS = {"cart", "checkout", "my-account", "wishlist", "order-received", "sample-checkout", "sample-order-success"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wp = load_module("wearhongxiu_seo_wp", SCRIPT_DIR / "wp.py")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def emit(value: Any, output: str | None = None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"saved": str(target.resolve())}, ensure_ascii=False, indent=2))
    else:
        print(text)


def safe_url(value: str, allow_path=True) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise RuntimeError(f"refusing non-Wearhongxiu URL: {value}")
    if not allow_path and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise RuntimeError("expected the canonical Wearhongxiu site root")
    return urllib.parse.urlunparse(parsed)


def normalize_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        if host == "www.wearhongxiu.com":
            host = "wearhongxiu.com"
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urllib.parse.urlunsplit(("https", host, path, parsed.query, ""))
    except Exception:
        return value


def public_get(value: str, accept="text/html,*/*", timeout=45) -> tuple[str, int, dict[str, str]]:
    url = safe_url(value)
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "Codex-Wearhongxiu-SEO/1.0"})
    opener = urllib.request.build_opener(wp.LockedRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            final = safe_url(response.geturl())
            if normalize_url(final) != normalize_url(url) and response.status == 200:
                pass
            body = response.read().decode("utf-8", "replace")
            return body, int(response.status), {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        return error.read().decode("utf-8", "replace"), int(error.code), {}


def wp_all(client, route: str, params: dict[str, str] | None = None, limit=0) -> list[dict[str, Any]]:
    page, output = 1, []
    while True:
        data, headers = client.request("GET", route, params={**(params or {}), "page": str(page), "per_page": "100"})
        if not isinstance(data, list):
            raise RuntimeError(f"expected list from {route}")
        output.extend(data)
        if limit and len(output) >= limit:
            return output[:limit]
        total_pages = int(headers.get("x-wp-totalpages") or page)
        if page >= total_pages:
            return output
        page += 1


def rendered(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("rendered") or value.get("raw") or "")
    return str(value or "")


def plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value or ""))).strip()


class PageParser(HTMLParser):
    SKIP = {"script", "style", "svg", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonicals: list[str] = []
        self.headings: list[dict[str, Any]] = []
        self.current_heading: dict[str, Any] | None = None
        self.images: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.current_link: dict[str, str] | None = None
        self.text_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.current_paragraph: list[str] | None = None
        self.jsonld_parts: list[list[str]] = []
        self.current_jsonld: list[str] | None = None
        self.skip_depth = 0
        self.semantic = Counter()
        self.list_count = 0
        self.table_count = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (values.get("name") or values.get("property") or values.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = values.get("content", "")
        if tag == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonicals.append(values.get("href", ""))
        if re.fullmatch(r"h[1-6]", tag):
            self.current_heading = {"level": int(tag[1]), "text": ""}
        if tag == "img":
            self.images.append({
                "src": values.get("src") or values.get("data-src") or "",
                "alt_present": "alt" in values,
                "alt": values.get("alt", ""),
                "title": values.get("title", ""),
                "width": values.get("width", ""),
                "height": values.get("height", ""),
                "loading": values.get("loading", ""),
            })
        if tag == "a":
            self.current_link = {"href": values.get("href", ""), "anchor": ""}
        if tag == "p":
            self.current_paragraph = []
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.current_jsonld = []
            self.jsonld_parts.append(self.current_jsonld)
        if tag in {"main", "article", "nav", "header", "footer", "section", "aside"}:
            self.semantic[tag] += 1
        if tag in {"ul", "ol"}:
            self.list_count += 1
        if tag == "table":
            self.table_count += 1

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        if tag == "title":
            self.in_title = False
            self.title = re.sub(r"\s+", " ", "".join(self.title_parts)).strip()
        if re.fullmatch(r"h[1-6]", tag) and self.current_heading:
            self.current_heading["text"] = re.sub(r"\s+", " ", self.current_heading["text"]).strip()
            self.headings.append(self.current_heading)
            self.current_heading = None
        if tag == "a" and self.current_link:
            self.current_link["anchor"] = re.sub(r"\s+", " ", self.current_link["anchor"]).strip()
            self.links.append(self.current_link)
            self.current_link = None
        if tag == "p" and self.current_paragraph is not None:
            value = re.sub(r"\s+", " ", "".join(self.current_paragraph)).strip()
            if value:
                self.paragraphs.append(value)
            self.current_paragraph = None
        if tag == "script":
            self.current_jsonld = None

    def handle_data(self, data: str):
        if self.in_title:
            self.title_parts.append(data)
        if self.current_jsonld is not None:
            self.current_jsonld.append(data)
        if self.current_heading is not None:
            self.current_heading["text"] += data
        if self.current_link is not None:
            self.current_link["anchor"] += data
        if self.current_paragraph is not None:
            self.current_paragraph.append(data)
        if not self.skip_depth:
            self.text_parts.append(data)


def schema_types(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, dict):
        item_type = value.get("@type")
        if isinstance(item_type, str):
            output.append(item_type)
        elif isinstance(item_type, list):
            output.extend(str(item) for item in item_type)
        for child in value.values():
            output.extend(schema_types(child))
    elif isinstance(value, list):
        for child in value:
            output.extend(schema_types(child))
    return output


def analyze_html(url: str, source: str, status=200) -> dict[str, Any]:
    parser = PageParser()
    parser.feed(source)
    parser.close()
    scripts, parse_errors = [], 0
    for parts in parser.jsonld_parts:
        raw = "".join(parts).strip()
        if not raw:
            continue
        try:
            scripts.append(json.loads(raw))
        except json.JSONDecodeError:
            parse_errors += 1
    types = sorted(set(schema_types(scripts)))
    body_text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    words = re.findall(r"\b[\w'-]+\b", body_text, flags=re.UNICODE)
    internal, external = [], []
    for link in parser.links:
        href = link["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(url, href)
        host = (urllib.parse.urlparse(absolute).hostname or "").lower()
        (internal if host in ALLOWED_HOSTS else external).append({**link, "url": absolute})
    issues: list[dict[str, Any]] = []

    def issue(code: str, severity: str, message: str, evidence: Any = None):
        row = {"code": code, "severity": severity, "message": message}
        if evidence not in (None, "", [], {}):
            row["evidence"] = evidence
        issues.append(row)

    description = parser.meta.get("description", "").strip()
    robots = parser.meta.get("robots", "").lower()
    canonical = parser.canonicals[0] if parser.canonicals else ""
    if status != 200:
        issue("http_status", "critical", f"Page returned HTTP {status}.")
    if not parser.title:
        issue("title_missing", "high", "The HTML title is missing.")
    elif len(parser.title) < 30 or len(parser.title) > 65:
        issue("title_length", "medium", "The title is outside the usual 30-65 character review range.", len(parser.title))
    if not description:
        issue("meta_description_missing", "high", "Meta description is missing.")
    elif len(description) < 100 or len(description) > 170:
        issue("meta_description_length", "medium", "Meta description is outside the usual 100-170 character review range.", len(description))
    if not canonical:
        issue("canonical_missing", "high", "Canonical link is missing.")
    elif normalize_url(canonical) != normalize_url(url):
        issue("canonical_not_self", "high", "Canonical does not match the audited URL.", canonical)
    if "noindex" in robots:
        issue("noindex", "high", "The page contains a noindex directive.", robots)
    h1 = [item for item in parser.headings if item["level"] == 1]
    if len(h1) != 1:
        issue("h1_count", "high", "An indexable content page should normally have exactly one H1.", len(h1))
    jumps = []
    for previous, current in zip(parser.headings, parser.headings[1:]):
        if current["level"] > previous["level"] + 1:
            jumps.append([previous["level"], current["level"], current["text"]])
    if jumps:
        issue("heading_level_jump", "medium", "Heading hierarchy skips one or more levels.", jumps[:10])
    missing_alt = [item for item in parser.images if not item["alt_present"]]
    empty_alt = [item for item in parser.images if item["alt_present"] and not item["alt"].strip()]
    if missing_alt:
        issue("image_alt_missing", "high", "Images without an alt attribute were found.", len(missing_alt))
    if empty_alt:
        issue("image_alt_empty_review", "low", "Images with empty alt text require a decorative-image review.", len(empty_alt))
    missing_dimensions = [item for item in parser.images if not item["width"] or not item["height"]]
    if missing_dimensions:
        issue("image_dimensions_missing", "medium", "Images without explicit width/height may contribute to layout shift.", len(missing_dimensions))
    filename_review = []
    for item in parser.images:
        name = Path(urllib.parse.urlparse(item["src"]).path).stem.lower()
        if name and (re.search(r"(^|[-_])(img|image|dsc|photo|screenshot)[-_]?\d*($|[-_])", name) or re.fullmatch(r"[a-f0-9-]{16,}", name)):
            filename_review.append(item["src"])
    if filename_review:
        issue("image_filename_review", "low", "Generic or opaque image filenames were detected; rename only when a safe media/redirect workflow exists.", len(filename_review))
    if parser.semantic["main"] == 0:
        issue("semantic_main_missing", "medium", "No semantic <main> element was detected in raw HTML.")
    if not types:
        issue("schema_not_detected", "review", "No JSON-LD was detected in raw HTML; verify with a rendered Rich Results test before concluding it is absent.")
    if len(words) < 250:
        issue("thin_content_review", "medium", "The page contains fewer than 250 visible words; review against its search intent.", len(words))
    if not internal:
        issue("internal_links_missing", "medium", "No crawlable internal links were detected.")
    question_headings = [item["text"] for item in parser.headings if item["text"].strip().endswith("?")]
    return {
        "url": url,
        "http_status": status,
        "title": parser.title,
        "title_length": len(parser.title),
        "meta_description": description,
        "meta_description_length": len(description),
        "canonical": canonical,
        "robots": robots,
        "headings": parser.headings,
        "word_count": len(words),
        "images": {"count": len(parser.images), "missing_alt": len(missing_alt), "empty_alt": len(empty_alt), "missing_dimensions": len(missing_dimensions), "items": parser.images},
        "links": {"internal_count": len(internal), "external_count": len(external), "internal": internal, "external": external},
        "semantic_html": dict(parser.semantic),
        "extractability": {"paragraphs": len(parser.paragraphs), "lists": parser.list_count, "tables": parser.table_count, "question_headings": question_headings},
        "schema": {"types": types, "jsonld_blocks": len(scripts), "parse_errors": parse_errors, "detection_mode": "raw_html"},
        "issues": issues,
    }


def robots_report(text: str) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or current["rules"]:
                current = {"agents": [], "rules": []}
                groups.append(current)
            current["agents"].append(value.lower())
        elif key in {"allow", "disallow"} and current is not None:
            current["rules"].append({"directive": key, "path": value})
    bots = ["googlebot", "bingbot", "gptbot", "chatgpt-user", "perplexitybot", "claudebot", "anthropic-ai", "google-extended"]
    access = {}
    for bot in bots:
        exact = [group for group in groups if bot in group["agents"]]
        selected = exact or [group for group in groups if "*" in group["agents"]]
        rules = [rule for group in selected for rule in group["rules"]]
        access[bot] = {
            "root_blocked": any(rule["directive"] == "disallow" and rule["path"].strip() == "/" for rule in rules),
            "rules": rules,
            "note": "Path-specific rules require URL-level evaluation." if rules else "No matching rules found.",
        }
    return {"groups": groups, "crawler_access": access}


def content_inventory(client, limit=0, status="publish") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    remaining = limit
    for wp_type, route in (("post", "/wp/v2/posts"), ("page", "/wp/v2/pages")):
        type_limit = remaining if limit else 0
        items = wp_all(client, route, {"status": status, "context": "edit"}, limit=type_limit)
        for item in items:
            if wp_type == "page" and item.get("slug") in OPERATIONAL_SLUGS:
                continue
            meta = item.get("meta") or {}
            yoast = item.get("yoast_head_json") or {}
            rows.append({
                "wp_id": item.get("id"), "wp_type": wp_type, "status": item.get("status"),
                "slug": item.get("slug"), "url": item.get("link"), "modified": item.get("modified_gmt") or item.get("modified"),
                "title": plain(rendered(item.get("title"))), "excerpt": plain(rendered(item.get("excerpt"))),
                "featured_media": item.get("featured_media") or 0,
                "yoast": {
                    "title": meta.get(YOAST_KEYS["title"], ""),
                    "description": meta.get(YOAST_KEYS["description"], ""),
                    "focus_keyword": meta.get(YOAST_KEYS["focus_keyword"], ""),
                    "canonical": meta.get(YOAST_KEYS["canonical"], ""),
                    "noindex": meta.get(YOAST_KEYS["noindex"], ""),
                    "nofollow": meta.get(YOAST_KEYS["nofollow"], ""),
                    "schema_page_type": meta.get(YOAST_KEYS["schema_page_type"], ""),
                    "schema_article_type": meta.get(YOAST_KEYS["schema_article_type"], ""),
                    "rendered_title": yoast.get("title", ""),
                    "rendered_description": yoast.get("description", ""),
                    "rendered_canonical": yoast.get("canonical", ""),
                },
            })
        if limit:
            remaining = max(0, limit - len(rows))
            if remaining == 0:
                break
    return rows


def cmd_capabilities(client, args) -> None:
    samples = {}
    for wp_type, route in (("post", "/wp/v2/posts"), ("page", "/wp/v2/pages")):
        items = wp_all(client, route, {"context": "edit", "status": "publish"}, limit=1)
        if not items:
            continue
        item = items[0]
        meta = item.get("meta") or {}
        samples[wp_type] = {
            "sample_id": item.get("id"), "has_yoast_rendered_head": bool(item.get("yoast_head_json")),
            "editable_yoast_fields": sorted(key for key in YOAST_KEYS.values() if key in meta),
            "content_raw_available": isinstance(item.get("content"), dict) and "raw" in item["content"],
        }
    media = wp_all(client, "/wp/v2/media", {"context": "edit"}, limit=1)
    emit({
        "site": SITE, "mode": "capability_discovery", "mutated": False,
        "content": samples,
        "media_editable_fields": ["title", "alt_text", "caption", "description"] if media else [],
        "gsc_cli": str(SCRIPT_DIR / "gsc.mjs"),
        "supported_schema_generation": sorted(SUPPORTED_SCHEMA),
        "supports_plan_apply": True,
    }, args.output)


def cmd_inventory(client, args) -> None:
    pages = content_inventory(client, args.limit)
    media = wp_all(client, "/wp/v2/media", {"context": "edit"}, limit=args.media_limit)
    media_rows = [{
        "id": item.get("id"), "url": item.get("source_url"), "mime_type": item.get("mime_type"),
        "title": plain(rendered(item.get("title"))), "alt_text": item.get("alt_text") or "",
        "caption": plain(rendered(item.get("caption"))), "uploaded": item.get("date_gmt") or item.get("date"),
    } for item in media]
    emit({
        "generated_at": now_iso(), "site": SITE, "mutated": False,
        "content_count": len(pages), "post_count": sum(row["wp_type"] == "post" for row in pages),
        "page_count": sum(row["wp_type"] == "page" for row in pages), "content": pages,
        "media_count": len(media_rows), "media_missing_alt": sum(not row["alt_text"].strip() for row in media_rows), "media": media_rows,
    }, args.output)


def audit_one(row: dict[str, Any]) -> dict[str, Any]:
    source, status, _ = public_get(row["url"])
    result = analyze_html(row["url"], source, status)
    result["wordpress"] = row
    return result


def site_level_findings(results: list[dict[str, Any]], keyword_map: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for field, code in (("title", "duplicate_title"), ("meta_description", "duplicate_meta_description")):
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in results:
            value = re.sub(r"\s+", " ", item.get(field, "")).strip().casefold()
            if value:
                grouped[value].append(item["url"])
        for urls in grouped.values():
            if len(urls) > 1:
                findings.append({"code": code, "severity": "high", "message": f"{len(urls)} URLs share the same {field.replace('_', ' ')}.", "urls": urls})
    inbound = Counter()
    known = {normalize_url(item["url"]): item["url"] for item in results}
    for item in results:
        for link in item["links"]["internal"]:
            target = normalize_url(link["url"])
            if target in known and target != normalize_url(item["url"]):
                inbound[target] += 1
    for key, url in known.items():
        if urllib.parse.urlparse(url).path not in {"", "/"} and inbound[key] == 0:
            findings.append({"code": "orphan_page_candidate", "severity": "medium", "message": "No inbound link was found from the audited content set.", "urls": [url]})
    if keyword_map:
        grouped_keywords: dict[str, list[str]] = defaultdict(list)
        for item in keyword_map.get("pages") or []:
            keyword = str(item.get("primary_keyword") or "").strip().casefold()
            if keyword:
                grouped_keywords[keyword].append(item.get("url") or f"{item.get('wp_type')}:{item.get('wp_id')}")
        for keyword, urls in grouped_keywords.items():
            if len(urls) > 1:
                findings.append({"code": "keyword_cannibalization", "severity": "high", "message": f"Primary keyword assigned to multiple pages: {keyword}", "urls": urls})
    return findings


def cmd_audit(client, args) -> None:
    if args.url:
        url = safe_url(args.url)
        source, status, _ = public_get(url)
        emit({"generated_at": now_iso(), "site": SITE, "mutated": False, "result": analyze_html(url, source, status)}, args.output)
        return
    rows = content_inventory(client, args.limit)
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = {executor.submit(audit_one, row): row for row in rows}
        for future in concurrent.futures.as_completed(futures):
            row = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                results.append({"url": row["url"], "wordpress": row, "issues": [{"code": "audit_failed", "severity": "critical", "message": str(error)}]})
    results.sort(key=lambda item: item["url"])
    keyword_map = read_json(args.keyword_map) if args.keyword_map else None
    robots_text, robots_status, _ = public_get(f"{SITE}/robots.txt", accept="text/plain,*/*")
    sitemap_text, sitemap_status, _ = public_get(f"{SITE}/sitemap_index.xml", accept="application/xml,text/xml,*/*")
    counts = Counter(issue["severity"] for item in results for issue in item.get("issues", []))
    robots_analysis = robots_report(robots_text) if robots_status == 200 else {"groups": [], "crawler_access": {}}
    emit({
        "generated_at": now_iso(), "site": SITE, "mutated": False, "audited_count": len(results),
        "issue_counts": dict(counts),
        "crawl_controls": {
            "robots_status": robots_status, "robots_has_sitemap": bool(re.search(r"^\s*Sitemap:", robots_text, flags=re.I | re.M)),
            "crawler_access": robots_analysis["crawler_access"],
            "sitemap_status": sitemap_status, "sitemap_loc_count": len(re.findall(r"<loc>", sitemap_text, flags=re.I)),
        },
        "site_findings": site_level_findings(results, keyword_map), "results": results,
        "schema_detection_note": "Raw HTML was inspected. Validate missing or changed schema with a rendered Rich Results test before acting.",
    }, args.output)


def keyword_map_template(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1, "site": "wearhongxiu.com", "generated_at": now_iso(),
        "rules": {"one_primary_keyword_per_page": True, "one_owner_page_per_primary_keyword": True},
        "pillars": [],
        "pages": [{
            "wp_id": row["wp_id"], "wp_type": row["wp_type"], "url": row["url"], "current_title": row["title"],
            "modified": row["modified"], "level": "", "parent_url": "", "pillar": "", "cluster": "",
            "page_role": "", "search_intent": "", "buyer_stage": "", "primary_keyword": "",
            "secondary_keywords": [], "priority": "", "status": "unassigned",
        } for row in rows],
    }


def validate_keyword_map(data: dict[str, Any]) -> dict[str, Any]:
    errors, warnings = [], []
    if data.get("site") != "wearhongxiu.com":
        errors.append("site must be wearhongxiu.com")
    pages = data.get("pages")
    if not isinstance(pages, list):
        return {"valid": False, "errors": ["pages must be an array"], "warnings": []}
    urls, keyword_owners = set(), defaultdict(list)
    for index, item in enumerate(pages):
        label = f"pages[{index}]"
        try:
            url = normalize_url(safe_url(str(item.get("url") or "")))
        except Exception as error:
            errors.append(f"{label}: {error}")
            continue
        if url in urls:
            errors.append(f"{label}: duplicate URL {url}")
        urls.add(url)
        keyword = str(item.get("primary_keyword") or "").strip().casefold()
        if keyword:
            keyword_owners[keyword].append(url)
        elif item.get("status") not in {"excluded", "noindex", "supporting-only"}:
            warnings.append(f"{label}: primary keyword is unassigned")
        if not item.get("page_role"):
            warnings.append(f"{label}: page_role is unassigned")
    for keyword, owners in keyword_owners.items():
        if len(owners) > 1:
            errors.append(f"primary keyword '{keyword}' has multiple owner pages: {owners}")
    return {"valid": not errors, "page_count": len(pages), "assigned_primary_keywords": len(keyword_owners), "errors": errors, "warnings": warnings}


def cmd_keyword_template(client, args) -> None:
    emit(keyword_map_template(content_inventory(client, args.limit)), args.output)


def cmd_keyword_validate(client, args) -> None:
    emit(validate_keyword_map(read_json(args.file)), args.output)


def plan_template(data: dict[str, Any]) -> dict[str, Any]:
    def seo_seed(item: dict[str, Any]) -> dict[str, Any]:
        keyword = str(item.get("primary_keyword") or "").strip()
        return {"focus_keyword": keyword} if keyword else {}

    return {
        "version": 1, "site": "wearhongxiu.com", "created_at": now_iso(), "description": "SEO change plan; empty sections make no change.",
        "pages": [{
            "wp_id": item.get("wp_id"), "wp_type": item.get("wp_type"), "url": item.get("url"),
            "expected_modified": item.get("modified"), "primary_keyword": item.get("primary_keyword", ""),
            "seo": seo_seed(item), "wp": {}, "media_updates": [], "schema_jsonld": [],
        } for item in data.get("pages") or [] if item.get("status") not in {"excluded", "noindex"}],
    }


def validate_schema_value(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["schema must be an object"]
    if value.get("@context") != "https://schema.org":
        errors.append("@context must be https://schema.org")
    schema_type = value.get("@type")
    if schema_type not in SUPPORTED_SCHEMA:
        errors.append(f"unsupported @type: {schema_type}")
        return errors
    required = {
        "Organization": ["name", "url"], "Article": ["headline", "image", "datePublished", "author"],
        "BlogPosting": ["headline", "image", "datePublished", "author"], "Product": ["name", "image", "description"],
        "FAQPage": ["mainEntity"], "BreadcrumbList": ["itemListElement"],
    }[schema_type]
    for key in required:
        if value.get(key) in (None, "", []):
            errors.append(f"{schema_type} requires {key}")
    if schema_type == "Product" and not any(value.get(key) for key in ("offers", "review", "aggregateRating")):
        errors.append("Product requires offers, review, or aggregateRating for Google product eligibility")
    if schema_type == "FAQPage":
        entities = value.get("mainEntity") or []
        if not isinstance(entities, list) or any(item.get("@type") != "Question" or not item.get("acceptedAnswer") for item in entities if isinstance(item, dict)):
            errors.append("FAQPage mainEntity must contain Question objects with acceptedAnswer")
    return errors


def validate_plan(data: dict[str, Any]) -> dict[str, Any]:
    errors, warnings, requirements = [], [], set()
    if data.get("site") != "wearhongxiu.com":
        errors.append("site must be wearhongxiu.com")
    pages = data.get("pages")
    if not isinstance(pages, list):
        return {"valid": False, "errors": ["pages must be an array"], "warnings": [], "required_apply_flags": []}
    seen, keyword_owners = set(), defaultdict(list)
    for index, item in enumerate(pages):
        label = f"pages[{index}]"
        wp_type, wp_id = item.get("wp_type"), item.get("wp_id")
        if wp_type not in {"post", "page"} or not isinstance(wp_id, int):
            errors.append(f"{label}: wp_type must be post/page and wp_id must be an integer")
        identity = (wp_type, wp_id)
        if identity in seen:
            errors.append(f"{label}: duplicate WordPress identity {identity}")
        seen.add(identity)
        if not item.get("expected_modified"):
            errors.append(f"{label}: expected_modified is required for concurrency protection")
        try:
            safe_url(str(item.get("url") or ""))
        except Exception as error:
            errors.append(f"{label}: {error}")
        keyword = str(item.get("primary_keyword") or "").strip().casefold()
        if keyword:
            keyword_owners[keyword].append(item.get("url"))
        seo = item.get("seo") or {}
        unknown_seo = set(seo) - set(YOAST_KEYS)
        if unknown_seo:
            errors.append(f"{label}: unsupported seo fields {sorted(unknown_seo)}")
        title = str(seo.get("title") or "")
        description = str(seo.get("description") or "")
        if title and not 30 <= len(title) <= 65:
            warnings.append(f"{label}: SEO title length is {len(title)}")
        if description and not 100 <= len(description) <= 170:
            warnings.append(f"{label}: meta description length is {len(description)}")
        if seo.get("canonical"):
            try:
                safe_url(str(seo["canonical"]))
            except Exception as error:
                errors.append(f"{label}: {error}")
        if any(key in seo for key in ("canonical", "noindex", "nofollow")):
            requirements.add("--allow-index-control")
        if seo.get("schema_page_type") and seo["schema_page_type"] not in SCHEMA_PAGE_TYPES:
            errors.append(f"{label}: invalid Yoast schema_page_type")
        if seo.get("schema_article_type") and seo["schema_article_type"] not in SCHEMA_ARTICLE_TYPES:
            errors.append(f"{label}: invalid Yoast schema_article_type")
        if any(key in seo for key in ("schema_page_type", "schema_article_type")):
            requirements.add("--allow-schema")
        wp_changes = item.get("wp") or {}
        if set(wp_changes) - {"title", "excerpt", "content"}:
            errors.append(f"{label}: wp only supports title, excerpt, content")
        if wp_changes:
            requirements.add("--allow-content")
        if item.get("media_updates"):
            requirements.add("--allow-media")
            for media in item["media_updates"]:
                if not isinstance(media.get("id"), int) or set(media) - {"id", "title", "alt_text", "caption", "description"}:
                    errors.append(f"{label}: invalid media update")
        for schema in item.get("schema_jsonld") or []:
            errors.extend(f"{label}: schema: {error}" for error in validate_schema_value(schema))
        if item.get("schema_jsonld"):
            errors.append(f"{label}: schema_jsonld is validation-only; choose an authorized Yoast block, WPCode, or template placement before apply-plan")
    for keyword, owners in keyword_owners.items():
        if len(owners) > 1:
            errors.append(f"primary keyword '{keyword}' has multiple pages: {owners}")
    return {"valid": not errors, "page_count": len(pages), "errors": errors, "warnings": warnings, "required_apply_flags": sorted(requirements)}


def cmd_plan_template(client, args) -> None:
    data = read_json(args.keyword_map)
    result = validate_keyword_map(data)
    if not result["valid"]:
        raise RuntimeError("keyword map is invalid: " + "; ".join(result["errors"]))
    emit(plan_template(data), args.output)


def cmd_plan_validate(client, args) -> None:
    emit(validate_plan(read_json(args.file)), args.output)


def schema_template(schema_type: str, data: dict[str, Any]) -> dict[str, Any]:
    if schema_type not in SUPPORTED_SCHEMA:
        raise RuntimeError(f"unsupported schema type: {schema_type}")
    return {"@context": "https://schema.org", "@type": schema_type, **data}


def cmd_schema_build(client, args) -> None:
    schema = schema_template(args.type, read_json(args.data))
    errors = validate_schema_value(schema)
    if errors:
        raise RuntimeError("schema validation failed: " + "; ".join(errors))
    emit(schema, args.output)


def cmd_schema_validate(client, args) -> None:
    schema = read_json(args.file)
    errors = validate_schema_value(schema)
    emit({"valid": not errors, "errors": errors, "schema_types": sorted(set(schema_types(schema)))}, args.output)


def cmd_cwv(client, args) -> None:
    url = safe_url(args.url)
    query = {"url": url, "strategy": args.strategy, "category": ["performance", "seo", "accessibility", "best-practices"]}
    key = os.environ.get("WEARHONGXIU_PAGESPEED_API_KEY", "")
    if key:
        query["key"] = key
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + urllib.parse.urlencode(query, doseq=True)
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json", "User-Agent": "Codex-Wearhongxiu-SEO/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"PageSpeed API HTTP {error.code}: {detail[:500]}") from None
    lighthouse = data.get("lighthouseResult") or {}
    categories = lighthouse.get("categories") or {}
    audits = lighthouse.get("audits") or {}
    field = (data.get("loadingExperience") or {}).get("metrics") or {}
    emit({
        "url": url, "strategy": args.strategy, "generated_at": now_iso(),
        "field_data": {key: {"percentile": value.get("percentile"), "category": value.get("category")} for key, value in field.items()},
        "lab_scores": {key: value.get("score") for key, value in categories.items()},
        "lab_metrics": {key: {"display_value": audits.get(key, {}).get("displayValue"), "numeric_value": audits.get(key, {}).get("numericValue")} for key in ("largest-contentful-paint", "cumulative-layout-shift", "total-blocking-time", "speed-index", "server-response-time")},
        "note": "Field data is origin/page CrUX data when available; lab data is a single Lighthouse run.",
    }, args.output)


def route_for(wp_type: str, wp_id: int) -> str:
    return f"/wp/v2/{'posts' if wp_type == 'post' else 'pages'}/{wp_id}"


def cmd_apply(client, args) -> None:
    if not args.yes:
        raise RuntimeError("apply-plan changes live WordPress data; re-run with --yes only after explicit authorization")
    data = read_json(args.file)
    validation = validate_plan(data)
    if not validation["valid"]:
        raise RuntimeError("plan validation failed: " + "; ".join(validation["errors"]))
    flag_state = {"--allow-content": args.allow_content, "--allow-index-control": args.allow_index_control, "--allow-media": args.allow_media, "--allow-schema": args.allow_schema}
    missing = [flag for flag in validation["required_apply_flags"] if not flag_state[flag]]
    if missing:
        raise RuntimeError("plan requires explicit apply flags: " + ", ".join(missing))
    pages = data.get("pages") or []
    current: dict[tuple[str, int], dict[str, Any]] = {}
    backup_pages = []
    for item in pages:
        identity = (item["wp_type"], item["wp_id"])
        value, _ = client.request("GET", route_for(*identity), params={"context": "edit"})
        modified = value.get("modified_gmt") or value.get("modified")
        if str(modified) != str(item["expected_modified"]):
            raise RuntimeError(f"concurrency conflict for {identity}: expected {item['expected_modified']}, found {modified}")
        current[identity] = value
        meta = value.get("meta") or {}
        backup_pages.append({
            "identity": identity, "url": value.get("link"), "modified": modified, "status": value.get("status"),
            "title": (value.get("title") or {}).get("raw", ""), "excerpt": (value.get("excerpt") or {}).get("raw", ""), "content": (value.get("content") or {}).get("raw", ""),
            "yoast_meta": {key: meta.get(key, "") for key in YOAST_KEYS.values()},
        })
    media_ids = sorted({media["id"] for item in pages for media in item.get("media_updates") or []})
    backup_media = []
    for media_id in media_ids:
        value, _ = client.request("GET", f"/wp/v2/media/{media_id}", params={"context": "edit"})
        backup_media.append({"id": media_id, "title": (value.get("title") or {}).get("raw", ""), "alt_text": value.get("alt_text", ""), "caption": (value.get("caption") or {}).get("raw", ""), "description": (value.get("description") or {}).get("raw", "")})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = SKILL_DIR / "backups" / f"seo-before-{stamp}.json"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps({"created_at": now_iso(), "plan": str(Path(args.file).resolve()), "pages": backup_pages, "media": backup_media}, ensure_ascii=False, indent=2), encoding="utf-8")
    changed = []
    for item in pages:
        payload: dict[str, Any] = {}
        if item.get("wp"):
            payload.update(item["wp"])
        seo = item.get("seo") or {}
        if seo:
            payload["meta"] = {YOAST_KEYS[key]: value for key, value in seo.items() if key in YOAST_KEYS}
        if payload:
            updated, _ = client.request("POST", route_for(item["wp_type"], item["wp_id"]), data=payload)
            changed.append({"type": item["wp_type"], "id": item["wp_id"], "modified": updated.get("modified_gmt") or updated.get("modified")})
        for media in item.get("media_updates") or []:
            media_payload = {key: value for key, value in media.items() if key != "id"}
            updated, _ = client.request("POST", f"/wp/v2/media/{media['id']}", data=media_payload)
            changed.append({"type": "media", "id": media["id"], "modified": updated.get("modified_gmt") or updated.get("modified")})
    verification = []
    for item in pages:
        if not (item.get("wp") or item.get("seo")):
            continue
        value, _ = client.request("GET", route_for(item["wp_type"], item["wp_id"]), params={"context": "edit"})
        meta = value.get("meta") or {}
        checks = {}
        for key, expected in (item.get("wp") or {}).items():
            actual = (value.get(key) or {}).get("raw", "") if isinstance(value.get(key), dict) else value.get(key)
            checks[f"wp.{key}"] = actual == expected
        for key, expected in (item.get("seo") or {}).items():
            checks[f"seo.{key}"] = meta.get(YOAST_KEYS[key], "") == expected
        verification.append({"type": item["wp_type"], "id": item["wp_id"], "passed": all(checks.values()), "checks": checks})
    for item in pages:
        for media in item.get("media_updates") or []:
            value, _ = client.request("GET", f"/wp/v2/media/{media['id']}", params={"context": "edit"})
            checks = {}
            for key, expected in media.items():
                if key == "id":
                    continue
                actual = value.get(key)
                if isinstance(actual, dict):
                    actual = actual.get("raw", "")
                checks[key] = actual == expected
            verification.append({"type": "media", "id": media["id"], "passed": all(checks.values()), "checks": checks})
    history_dir = SKILL_DIR / "seo"
    history_dir.mkdir(parents=True, exist_ok=True)
    record = {"applied_at": now_iso(), "plan": str(Path(args.file).resolve()), "backup": str(backup_path), "changed": changed, "verification": verification}
    with (history_dir / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    emit({"applied": True, "backup": str(backup_path), "changed": changed, "verification": verification, "verified": all(item["passed"] for item in verification), "cache_purged": False, "note": "REST values were read back. Run a rendered audit before separately authorizing a cache purge."}, args.output)


def cmd_history(client, args) -> None:
    path = SKILL_DIR / "seo" / "history.jsonl"
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    emit({"history_file": str(path), "count": len(records), "records": records[-args.limit:]}, args.output)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Wearhongxiu SEO audit, planning, validation, and controlled apply")
    root.add_argument("--config", help="Wearhongxiu WordPress config.env")
    sub = root.add_subparsers(dest="command", required=True)

    item = sub.add_parser("capabilities")
    item.add_argument("--output")
    item.set_defaults(func=cmd_capabilities)

    item = sub.add_parser("inventory")
    item.add_argument("--limit", type=int, default=0)
    item.add_argument("--media-limit", type=int, default=100)
    item.add_argument("--output")
    item.set_defaults(func=cmd_inventory)

    item = sub.add_parser("audit")
    item.add_argument("--url")
    item.add_argument("--limit", type=int, default=0)
    item.add_argument("--workers", type=int, default=4)
    item.add_argument("--keyword-map")
    item.add_argument("--output")
    item.set_defaults(func=cmd_audit)

    item = sub.add_parser("keyword-map-template")
    item.add_argument("--limit", type=int, default=0)
    item.add_argument("--output", required=True)
    item.set_defaults(func=cmd_keyword_template)

    item = sub.add_parser("keyword-map-validate")
    item.add_argument("file")
    item.add_argument("--output")
    item.set_defaults(func=cmd_keyword_validate)

    item = sub.add_parser("plan-template")
    item.add_argument("--keyword-map", required=True)
    item.add_argument("--output", required=True)
    item.set_defaults(func=cmd_plan_template)

    item = sub.add_parser("plan-validate")
    item.add_argument("file")
    item.add_argument("--output")
    item.set_defaults(func=cmd_plan_validate)

    item = sub.add_parser("schema-build")
    item.add_argument("--type", required=True, choices=sorted(SUPPORTED_SCHEMA))
    item.add_argument("--data", required=True)
    item.add_argument("--output")
    item.set_defaults(func=cmd_schema_build)

    item = sub.add_parser("schema-validate")
    item.add_argument("file")
    item.add_argument("--output")
    item.set_defaults(func=cmd_schema_validate)

    item = sub.add_parser("cwv")
    item.add_argument("url")
    item.add_argument("--strategy", choices=("mobile", "desktop"), default="mobile")
    item.add_argument("--output")
    item.set_defaults(func=cmd_cwv)

    item = sub.add_parser("apply-plan")
    item.add_argument("file")
    item.add_argument("--yes", action="store_true")
    item.add_argument("--allow-content", action="store_true")
    item.add_argument("--allow-index-control", action="store_true")
    item.add_argument("--allow-media", action="store_true")
    item.add_argument("--allow-schema", action="store_true")
    item.add_argument("--output")
    item.set_defaults(func=cmd_apply)

    item = sub.add_parser("history")
    item.add_argument("--limit", type=int, default=20)
    item.add_argument("--output")
    item.set_defaults(func=cmd_history)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        client = wp.Client(wp.config(args.config))
        args.func(client, args)
    except (RuntimeError, wp.ApiError, OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
