#!/usr/bin/env python3
"""Build, inspect, and maintain the public Wearhongxiu website knowledge base.

The product knowledge base is intentionally out of scope. Sources are limited to
published WordPress posts/pages plus llms.txt on wearhongxiu.com.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import importlib.util
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CODEX_HOME = Path.home() / ".codex"
RAG_SCRIPT = SCRIPT_DIR / "hongxiu_rag.py"
SITE = "https://wearhongxiu.com"
RAG_API = "https://rag-api.yiswim.cloud/api"
MANAGED_PREFIX = "wearhongxiu:"
EXCLUDED_PAGE_SLUGS = {
    "cart", "checkout", "my-account", "wishlist", "shop", "order-received",
    "sample-checkout", "sample-order-success",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_module(name: str, path: Path):
    if not path.is_file():
        raise RuntimeError(f"required skill script not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wp = _load_module("wearhongxiu_wp_client", SCRIPT_DIR / "wp.py")
rag = _load_module("hongxiu_rag_client", RAG_SCRIPT)


class TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "section", "article", "blockquote", "br", "tr"}
    SKIP = {"script", "style", "svg", "noscript", "form", "nav", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if not self.skip_depth and (tag in self.BLOCKS or tag.startswith("h") or tag == "li"):
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\xa0", " ")
        value = re.sub(r"\[(?:/?)[A-Za-z][^\]\n]{0,300}\]", " ", value)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def clean_html(value: str) -> str:
    parser = TextExtractor()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def clean_title(value: Any) -> str:
    return clean_html(str(value or "")).replace("\n", " ").strip()


def digest(title: str, content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in f"{title}\n{content}".splitlines()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class Source:
    source_key: str
    title: str
    content: str
    category: str
    tags: list[str]
    metadata: dict[str, Any]

    @property
    def content_hash(self) -> str:
        return digest(self.title, self.content)

    def rag_content(self) -> str:
        return (
            f"# {self.title}\n\n"
            f"Canonical source: {self.metadata['canonical_url']}\n"
            f"Source: Wearhongxiu official website\n\n"
            f"{self.content.strip()}"
        )

    def source_metadata(self) -> dict[str, Any]:
        return {
            **self.metadata,
            "source_key": self.source_key,
            "content_hash": self.content_hash,
            "site": "wearhongxiu.com",
            "knowledge_scope": "website",
        }


def wp_all(client, route: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
    page = 1
    output: list[dict[str, Any]] = []
    while True:
        values = {**(params or {}), "page": str(page), "per_page": "100"}
        data, headers = client.request("GET", route, params=values)
        if not isinstance(data, list):
            raise RuntimeError(f"expected a list from {route}")
        output.extend(data)
        total_pages = int(headers.get("x-wp-totalpages") or page)
        if page >= total_pages:
            return output
        page += 1


def public_text(path: str) -> str:
    request = urllib.request.Request(
        SITE + path,
        headers={"User-Agent": "Codex-Wearhongxiu-RAG-Sync/1.0", "Accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_host = urllib.parse.urlparse(response.geturl()).hostname
            if final_host not in {"wearhongxiu.com", "www.wearhongxiu.com"}:
                raise RuntimeError(f"refusing cross-site redirect for {path}")
            return response.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path} returned HTTP {exc.code}") from exc


def collect_sources(client) -> tuple[list[Source], list[dict[str, Any]]]:
    categories = {
        int(item["id"]): clean_title(item.get("name"))
        for item in wp_all(client, "/wp/v2/categories", {"hide_empty": "false"})
    }
    sources: list[Source] = []
    rows: list[dict[str, Any]] = []
    for post_type, route in (("post", "/wp/v2/posts"), ("page", "/wp/v2/pages")):
        for item in wp_all(client, route, {"status": "publish", "context": "view"}):
            slug = str(item.get("slug") or "").strip()
            body = clean_html(((item.get("content") or {}).get("rendered") or ""))
            title = clean_title((item.get("title") or {}).get("rendered"))
            reason = ""
            if post_type == "page" and slug in EXCLUDED_PAGE_SLUGS:
                reason = "operational page"
            elif not title:
                reason = "empty title"
            elif len(body) < 120:
                reason = "insufficient body text"
            if reason:
                rows.append({"type": post_type, "id": item.get("id"), "slug": slug, "included": False, "reason": reason})
                continue

            category_names = [categories.get(int(value), "") for value in item.get("categories") or []]
            category_names = [value for value in category_names if value]
            category = category_names[0] if category_names else ("Website Pages" if post_type == "page" else "Website Articles")
            source_key = f"{MANAGED_PREFIX}wordpress:{post_type}:{item['id']}"
            source = Source(
                source_key=source_key,
                title=title,
                content=body,
                category=category,
                tags=list(dict.fromkeys(["wearhongxiu-site", "wordpress", post_type, *category_names])),
                metadata={
                    "canonical_url": str(item.get("link") or f"{SITE}/{slug}/"),
                    "source_kind": "wordpress",
                    "wp_id": int(item["id"]),
                    "wp_type": post_type,
                    "slug": slug,
                    "published_at": str(item.get("date_gmt") or item.get("date") or ""),
                    "modified_at": str(item.get("modified_gmt") or item.get("modified") or ""),
                },
            )
            rows.append({"type": post_type, "id": item.get("id"), "slug": slug, "included": True, "characters": len(body)})
            # Keep collection ordering deterministic: posts, pages, then site files.
            sources.append(source)

    for filename, title, category in (
        ("llms.txt", "Hongxiu Clothing Company and Website Information", "Company Information"),
    ):
        raw = public_text("/" + filename)
        sources.append(Source(
            source_key=f"{MANAGED_PREFIX}file:{filename}",
            title=title,
            content=raw,
            category=category,
            tags=["wearhongxiu-site", "site-file", filename],
            metadata={
                "canonical_url": f"{SITE}/{filename}",
                "source_kind": "site-file",
                "filename": filename,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        ))
        rows.append({"type": "site-file", "slug": filename, "included": True, "characters": len(raw)})
    return sources, rows


def rag_client():
    auth = rag.load_token()
    if not auth:
        raise RuntimeError("Hongxiu RAG token is not configured")
    client = rag.RagMcp(rag.DEFAULT_URL, auth)
    client.initialize()
    return client


def fail_if_error(result: Any, operation: str) -> None:
    if isinstance(result, dict) and (result.get("mcp_error") or result.get("is_error")):
        raise RuntimeError(f"{operation} failed: {result}")


def inventory(args) -> None:
    sources, rows = collect_sources(wp.Client(wp.config(args.config)))
    included = [row for row in rows if row["included"]]
    skipped = [row for row in rows if not row["included"]]
    print(json.dumps({
        "source_count": len(sources),
        "wordpress_posts": sum(1 for row in included if row["type"] == "post"),
        "wordpress_pages": sum(1 for row in included if row["type"] == "page"),
        "site_files": sum(1 for row in included if row["type"] == "site-file"),
        "skipped": skipped,
    }, ensure_ascii=False, indent=2))


def query(args) -> None:
    """Query only Wearhongxiu website documents through the RAG MCP."""
    client = rag_client()
    arguments = {
        "query": args.question,
        "top_k": max(1, min(args.top_k, 30)),
        "visibility": "external",
    }
    if args.debug:
        arguments["debug_retrieval"] = True
    result = client.call("search_knowledge", arguments, timeout=240)
    fail_if_error(result, "website knowledge query")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def homepage_check(args) -> None:
    """Exercise the same public WordPress route used by Ask Hongxiu."""
    endpoint = f"{SITE}/wp-json/hongxiu-rag/v1/chat"
    payload = json.dumps({"message": args.question}, ensure_ascii=False).encode("utf-8")
    runs = []
    for index in range(max(1, min(args.repeat, 3))):
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Codex-Wearhongxiu-RAG-Check/1.0",
            },
        )
        started_at = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"homepage RAG route returned non-JSON HTTP {response.status}: {body[:300]}"
                    ) from exc
                runs.append({
                    "run": index + 1,
                    "http_status": response.status,
                    "elapsed_ms": elapsed_ms,
                    "response": parsed,
                })
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"homepage RAG route returned HTTP {exc.code}: {detail}") from exc
    print(json.dumps({"endpoint": endpoint, "runs": runs}, ensure_ascii=False, indent=2))


def backup_existing(client, output_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    summaries = client.call("list_documents", {"limit": 1000})
    fail_if_error(summaries, "list documents")
    if not isinstance(summaries, list):
        raise RuntimeError("RAG list_documents did not return a list")
    def fetch_detail(summary: dict[str, Any]) -> dict[str, Any]:
        document_id = str(summary["id"])
        last_error: Exception | None = None
        for attempt in range(1, 5):
            request = urllib.request.Request(
                f"{RAG_API}/documents/{urllib.parse.quote(document_id)}",
                headers={"Accept": "application/json", "User-Agent": "Codex-Wearhongxiu-RAG-Sync/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(attempt * 2)
        raise RuntimeError(f"failed to back up document {document_id} after 4 attempts: {last_error}")

    details: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(fetch_detail, summary) for summary in summaries]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            details.append(future.result())
            if index % 10 == 0 or index == len(summaries):
                print(f"backup {index}/{len(summaries)}", flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"hongxiu-rag-before-website-rebuild-{stamp}.json"
    path.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document_count": len(details),
        "documents": details,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, summaries


def ingest_source(client, source: Source, allow_duplicates: bool) -> dict[str, Any]:
    result = client.call("ingest_text_knowledge", {
        "content": source.rag_content(),
        "title": source.title,
        "description": f"Official Wearhongxiu website content from {source.metadata['canonical_url']}",
        "category": source.category,
        "tags": source.tags,
        "visibility": "external",
        "chunk_strategy": "heading_paragraph",
        "chunk_size": 900,
        "chunk_overlap": 100,
        "commit": True,
        "allow_duplicates": allow_duplicates,
        "allow_review": True,
        "source_metadata": source.source_metadata(),
    }, timeout=300)
    fail_if_error(result, f"ingest {source.source_key}")
    if not result.get("committed"):
        raise RuntimeError(f"ingest blocked for {source.source_key}: {result.get('blocked_reasons')}")
    return result


def rebuild(args) -> None:
    if not args.yes:
        raise RuntimeError("rebuild deletes the current Hongxiu RAG; re-run with --yes")
    sources, rows = collect_sources(wp.Client(wp.config(args.config)))
    if not sources:
        raise RuntimeError("source inventory is empty; refusing to clear the RAG")
    client = rag_client()
    backup_path, old_documents = backup_existing(client, Path(args.backup_dir))
    print(f"backup saved: {backup_path}", flush=True)
    for index, document in enumerate(old_documents, 1):
        document_id = str(document["id"])
        result = client.call("delete_document", {"document_id": document_id})
        fail_if_error(result, f"delete {document_id}")
        print(f"delete {index}/{len(old_documents)}", flush=True)

    manifest = []
    local = threading.local()

    def ingest_one(source: Source) -> tuple[Source, dict[str, Any]]:
        if not getattr(local, "client", None):
            local.client = rag_client()
        return source, ingest_source(local.client, source, allow_duplicates=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as executor:
        futures = [executor.submit(ingest_one, source) for source in sources]
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            source = sources[futures.index(future)]
            try:
                source, result = future.result()
                manifest.append({"source_key": source.source_key, "document_id": result.get("document_id"), "status": "committed"})
                print(f"ingest {completed}/{len(sources)}: {source.source_key}", flush=True)
            except Exception as exc:
                manifest.append({"source_key": source.source_key, "status": "failed", "error": str(exc)})
                print(f"FAILED {completed}/{len(sources)}: {source.source_key}: {exc}", file=sys.stderr, flush=True)
    failures = [item for item in manifest if item["status"] == "failed"]
    print(json.dumps({
        "backup": str(backup_path),
        "source_count": len(sources),
        "committed": len(manifest) - len(failures),
        "failed": failures,
        "skipped_sources": [row for row in rows if not row["included"]],
    }, ensure_ascii=False, indent=2))
    if failures:
        raise RuntimeError(f"rebuild completed with {len(failures)} failed sources")


def sync(args) -> None:
    sources, _ = collect_sources(wp.Client(wp.config(args.config)))
    client = rag_client()
    existing = client.call("list_documents", {"limit": 1000})
    fail_if_error(existing, "list documents")
    managed = {item.get("source_key"): item for item in existing if str(item.get("source_key") or "").startswith(MANAGED_PREFIX)}
    desired = {source.source_key: source for source in sources}
    summary = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    pending: list[tuple[Source, dict[str, Any] | None]] = []
    for source in sources:
        old = managed.get(source.source_key)
        if old and old.get("content_hash") == source.content_hash:
            summary["unchanged"] += 1
            continue
        pending.append((source, old))

    local = threading.local()

    def sync_one(item: tuple[Source, dict[str, Any] | None]):
        source, old = item
        if not getattr(local, "client", None):
            local.client = rag_client()
        # WordPress identity, not semantic similarity, defines uniqueness. Two
        # official pages can legitimately discuss the same policy or service.
        result = ingest_source(local.client, source, allow_duplicates=True)
        if old:
            deletion = local.client.call("delete_document", {"document_id": str(old["id"])})
            fail_if_error(deletion, f"delete replaced {old['id']}")
        return source, old, result

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = [executor.submit(sync_one, item) for item in pending]
        failures = []
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                source, old, result = future.result()
                summary["updated" if old else "created"] += 1
                print(f"synced {completed}/{len(pending)} {source.source_key} -> {result.get('document_id')}", flush=True)
            except Exception as exc:
                failures.append(str(exc))
                print(f"sync failed {completed}/{len(pending)}: {exc}", file=sys.stderr, flush=True)
    for source_key in sorted(set(managed) - set(desired)):
        deletion = client.call("delete_document", {"document_id": str(managed[source_key]["id"])})
        fail_if_error(deletion, f"delete stale {managed[source_key]['id']}")
        summary["deleted"] += 1
        print(f"deleted stale {source_key}", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        raise RuntimeError(f"sync completed with {len(failures)} failures")


def sync_post(args) -> None:
    """Synchronize one published WordPress post without scanning mutations site-wide."""
    wordpress = wp.Client(wp.config(args.config))
    item, _ = wordpress.request(
        "GET",
        f"/wp/v2/posts/{args.post_id}",
        params={"context": "view"},
    )
    if str(item.get("status") or "") != "publish":
        raise RuntimeError(f"post {args.post_id} is not published; refusing RAG ingestion")

    categories = {
        int(value["id"]): clean_title(value.get("name"))
        for value in wp_all(wordpress, "/wp/v2/categories", {"hide_empty": "false"})
    }
    body = clean_html(((item.get("content") or {}).get("rendered") or ""))
    title = clean_title((item.get("title") or {}).get("rendered"))
    if not title or len(body) < 120:
        raise RuntimeError("published post has insufficient title/body content for RAG ingestion")

    category_names = [categories.get(int(value), "") for value in item.get("categories") or []]
    category_names = [value for value in category_names if value]
    source = Source(
        source_key=f"{MANAGED_PREFIX}wordpress:post:{int(item['id'])}",
        title=title,
        content=body,
        category=category_names[0] if category_names else "Website Articles",
        tags=list(dict.fromkeys(["wearhongxiu-site", "wordpress", "post", *category_names])),
        metadata={
            "canonical_url": str(item.get("link") or f"{SITE}/{item.get('slug')}/"),
            "source_kind": "wordpress",
            "wp_id": int(item["id"]),
            "wp_type": "post",
            "slug": str(item.get("slug") or ""),
            "published_at": str(item.get("date_gmt") or item.get("date") or ""),
            "modified_at": str(item.get("modified_gmt") or item.get("modified") or ""),
        },
    )

    client = rag_client()
    existing = client.call("list_documents", {"limit": 1000})
    fail_if_error(existing, "list documents")
    old = next(
        (value for value in existing if value.get("source_key") == source.source_key),
        None,
    )
    if old and old.get("content_hash") == source.content_hash:
        print(json.dumps({
            "post_id": args.post_id,
            "source_key": source.source_key,
            "status": "unchanged",
            "document_id": old.get("id"),
        }, ensure_ascii=False, indent=2))
        return

    result = ingest_source(client, source, allow_duplicates=True)
    if old:
        deletion = client.call("delete_document", {"document_id": str(old["id"])})
        fail_if_error(deletion, f"delete replaced {old['id']}")
    print(json.dumps({
        "post_id": args.post_id,
        "source_key": source.source_key,
        "status": "updated" if old else "created",
        "document_id": result.get("document_id"),
        "content_hash": source.content_hash,
    }, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Sync public Wearhongxiu website content to Hongxiu RAG")
    root.add_argument("--config", help="Wearhongxiu WordPress config.env")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory").set_defaults(func=inventory)
    query_parser = sub.add_parser("query", help="query only Wearhongxiu website knowledge")
    query_parser.add_argument("question")
    query_parser.add_argument("--top-k", type=int, default=6)
    query_parser.add_argument("--debug", action="store_true")
    query_parser.set_defaults(func=query)
    check_parser = sub.add_parser("homepage-check", help="check the public Ask Hongxiu WordPress route")
    check_parser.add_argument("question", nargs="?", default="what is your moq")
    check_parser.add_argument("--repeat", type=int, default=1)
    check_parser.add_argument("--timeout", type=int, default=60)
    check_parser.set_defaults(func=homepage_check)
    rebuild_parser = sub.add_parser("rebuild")
    rebuild_parser.add_argument("--yes", action="store_true")
    rebuild_parser.add_argument("--backup-dir", default=str(SKILL_DIR / "backups"))
    rebuild_parser.add_argument("--workers", type=int, default=3)
    rebuild_parser.set_defaults(func=rebuild)
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--workers", type=int, default=4)
    sync_parser.set_defaults(func=sync)
    sync_post_parser = sub.add_parser("sync-post", help="sync one published WordPress post")
    sync_post_parser.add_argument("post_id", type=int)
    sync_post_parser.set_defaults(func=sync_post)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
