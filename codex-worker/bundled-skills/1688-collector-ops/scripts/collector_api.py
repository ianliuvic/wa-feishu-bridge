#!/usr/bin/env python3
"""Small authenticated CLI for the remote 1688 collector.

Credentials are read only from the process environment and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# DSH strips credential-shaped variables from the shell environment it gives
# child processes, so pull this skill's credentials from the harness-managed
# file when they are not already present. No-op on a Codex worker.
try:
    from skill_credentials import load as _load_skill_credentials
    _load_skill_credentials("COLLECTOR_API_URL", "COLLECTOR_API_KEY")
except ImportError:
    pass


BASE_URL = os.getenv("COLLECTOR_API_URL", "https://collector.yiswim.cloud").rstrip("/")
API_KEY = os.getenv("COLLECTOR_API_KEY", "").strip()
TERMINAL = {
    "completed",
    "failed",
    "rejected_duplicate",
    "completed_with_errors",
    "requires_auth",
    "cancelled",
}


class ApiError(RuntimeError):
    pass


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def request(method: str, path: str, data: Any | None = None, timeout: int = 90) -> Any:
    if not API_KEY and path != "/health":
        raise ApiError("COLLECTOR_API_KEY is not configured")
    url = path if path.startswith("http://") or path.startswith("https://") else f"{BASE_URL}/{path.lstrip('/')}"
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    # Cloudflare rejects urllib's default signature before API authentication.
    headers = {
        "Accept": "application/json",
        "User-Agent": "Codex-1688-Collector/1.0 curl-compatible",
    }
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return {"httpStatus": response.status}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:1000]
        raise ApiError(f"HTTP {exc.code} {method.upper()} {urllib.parse.urlsplit(url).path}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"Collector request failed: {exc.reason}") from None


def wait_job(path: str, timeout: int, interval: float = 2.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        job = request("GET", path)
        status = str(job.get("status", "")).lower()
        if status in TERMINAL:
            if status in {"failed", "completed_with_errors", "requires_auth", "cancelled"}:
                raise ApiError(f"Job ended with {status}: {job.get('error') or job.get('errors') or 'unknown error'}")
            return job
        if time.monotonic() >= deadline:
            raise ApiError(f"Timed out waiting for {path}; last status={status or 'unknown'}")
        time.sleep(interval)


def job_id(value: Any) -> str:
    result = value.get("id") if isinstance(value, dict) else None
    if not result:
        raise ApiError("Collector response did not include a job id")
    return str(result)


def offer_id(value: str, job: Any | None = None) -> str | None:
    match = re.search(r"(?<!\d)(\d{10,13})(?!\d)", value)
    if match:
        return match.group(1)
    data = (job or {}).get("extracted_data") or (job or {}).get("extractedData") or {}
    for key in ("offerId", "offer_id"):
        candidate = data.get(key)
        if candidate and re.fullmatch(r"\d{10,13}", str(candidate)):
            return str(candidate)
    return None


def product_payload(value: str) -> dict[str, str]:
    if re.fullmatch(r"\d{10,13}", value.strip()):
        return {"offerId": value.strip()}
    return {"url": value.strip()}


def capture_product(value: str, timeout: int) -> tuple[Any, Any | None]:
    created = request("POST", "/api/product-details", product_payload(value))
    completed = wait_job(f"/api/jobs/{job_id(created)}", timeout)
    if str(completed.get("status", "")).lower() == "rejected_duplicate":
        return completed, None
    oid = offer_id(value, completed)
    if not oid:
        raise ApiError("Capture completed but the offer id could not be resolved")
    rows = request("GET", f"/api/product-details?offerId={urllib.parse.quote(oid)}&limit=1")
    detail = rows[0] if isinstance(rows, list) and rows else None
    if not detail:
        raise ApiError("Capture completed but the saved product row was not found")
    return completed, detail


def cmd_status(_: argparse.Namespace) -> Any:
    return {
        "health": request("GET", "/health"),
        "browserMode": request("GET", "/api/browser-mode"),
    }


def cmd_login_check(args: argparse.Namespace) -> Any:
    created = request("POST", "/api/plugin-session/check", {})
    return wait_job(f"/api/jobs/{job_id(created)}", args.timeout)


def cmd_product_capture(args: argparse.Namespace) -> Any:
    completed, detail = capture_product(args.target, args.timeout)
    return {
        "captureStatus": completed.get("status"),
        "jobId": completed.get("id"),
        "productDetailId": detail.get("id") if detail else None,
        "offerId": detail.get("offer_id") if detail else offer_id(args.target, completed),
        "duplicateAnalysis": (completed.get("extracted_data") or {}).get("duplicateAnalysis"),
    }


def cmd_product_pipeline(args: argparse.Namespace) -> Any:
    completed, detail = capture_product(args.target, args.timeout)
    if detail is None:
        return {
            "captureStatus": completed.get("status"),
            "published": False,
            "duplicateAnalysis": (completed.get("extracted_data") or {}).get("duplicateAnalysis"),
        }
    detail_id = str(detail["id"])
    translation = request("POST", f"/api/product-details/{detail_id}/translations", {"targetLanguage": "en"})
    translation = wait_job(f"/api/translation-jobs/{job_id(translation)}", args.timeout)
    publish_body = {
        "status": args.status,
        "categoryMode": args.category_mode,
        "tagMode": args.tag_mode,
    }
    publication = request("POST", f"/api/product-details/{detail_id}/wordpress/publish", publish_body)
    publication = wait_job(f"/api/wordpress-jobs/{job_id(publication)}", args.timeout)
    rag = request("GET", f"/api/product-details/{detail_id}/rag-syncs?limit=5")
    return {
        "captureStatus": completed.get("status"),
        "productDetailId": detail_id,
        "offerId": detail.get("offer_id"),
        "translationStatus": translation.get("status"),
        "publicationStatus": publication.get("status"),
        "wordpress": publication.get("result"),
        "ragSyncs": rag,
    }


def cmd_shop_home(args: argparse.Namespace) -> Any:
    created = request("POST", "/api/jobs", {"url": args.url, "paginate": False})
    completed = wait_job(f"/api/jobs/{job_id(created)}", args.timeout)
    contact = request("POST", "/api/shop-contact-link", {"url": args.url})
    return {"capture": completed, "contact": contact}


def cmd_shop_scan(args: argparse.Namespace) -> Any:
    created = request("POST", "/api/shop-scans/all", {"url": args.url})
    return wait_job(f"/api/jobs/{job_id(created)}", args.timeout)


def cmd_best_sellers_preview(args: argparse.Namespace) -> Any:
    return request("GET", f"/api/wordpress/best-sellers/preview?limit={args.limit}")


def cmd_best_sellers_rebuild(args: argparse.Namespace) -> Any:
    created = request("POST", "/api/wordpress/best-sellers/rebuild", {"limit": args.limit})
    return wait_job(f"/api/wordpress-best-seller-jobs/{job_id(created)}", args.timeout)


def cmd_product_resolve(args: argparse.Namespace) -> Any:
    target = args.target.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{2,40}", target) and not target.isdigit():
        query = {"styleNo": target}
    elif re.fullmatch(r"\d+", target):
        query = {"wpPostId": target}
    elif target.startswith("https://"):
        query = {"url": target}
    else:
        query = {"slug": target}
    return request("GET", f"/api/wordpress/products/resolve?{urllib.parse.urlencode(query)}")


def cmd_product_style_set(args: argparse.Namespace) -> Any:
    return request("POST", f"/api/product-details/{args.product_detail_id}/wordpress/style-number",
                   {"styleNo": args.style_no})


def cmd_mode(args: argparse.Namespace) -> Any:
    return request("POST", f"/api/browser-mode/{args.mode}", {})


def cmd_logout_login(_: argparse.Namespace) -> Any:
    result = request("POST", "/api/browser-mode/logout-login", {})
    logout = result.get("logout") if isinstance(result, dict) else None
    if not isinstance(logout, dict) or logout.get("loggedOut") is not True or logout.get("signinReady") is not True:
        raise ApiError("Collector did not confirm a logged-out 1688 sign-in page")
    return {
        "mode": result.get("mode"),
        "login": result.get("login"),
        "loginUrl": result.get("loginUrl"),
        "logout": logout,
    }


def cmd_request(args: argparse.Namespace) -> Any:
    payload = json.loads(args.data) if args.data else None
    result = request(args.method, args.path, payload, timeout=args.timeout)
    if args.wait_path:
        jid = job_id(result)
        result = wait_job(args.wait_path.replace("{id}", jid), args.timeout)
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Operate the authenticated 1688 collector API")
    root.add_argument("--timeout", type=int, default=1800)
    sub = root.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("login-check")
    p.set_defaults(func=cmd_login_check)
    for name, mode in (("login-mode", "login"), ("collector-mode", "collector")):
        p = sub.add_parser(name)
        p.set_defaults(func=cmd_mode, mode=mode)
    p = sub.add_parser("logout-login")
    p.set_defaults(func=cmd_logout_login)
    p = sub.add_parser("product-capture")
    p.add_argument("target")
    p.set_defaults(func=cmd_product_capture)
    p = sub.add_parser("product-pipeline")
    p.add_argument("target")
    p.add_argument("--status", choices=("draft", "pending", "publish", "private"), default="draft")
    p.add_argument("--category-mode", choices=("auto", "primary_only", "manual"), default="auto")
    p.add_argument("--tag-mode", choices=("auto", "manual"), default="auto")
    p.set_defaults(func=cmd_product_pipeline)
    p = sub.add_parser("product-resolve")
    p.add_argument("target", help="Style number, WordPress post ID, slug, or Wearhongxiu product URL")
    p.set_defaults(func=cmd_product_resolve)
    p = sub.add_parser("product-style-set")
    p.add_argument("product_detail_id", type=int)
    p.add_argument("style_no")
    p.set_defaults(func=cmd_product_style_set)
    p = sub.add_parser("shop-home")
    p.add_argument("url")
    p.set_defaults(func=cmd_shop_home)
    p = sub.add_parser("shop-scan")
    p.add_argument("url")
    p.set_defaults(func=cmd_shop_scan)
    p = sub.add_parser("best-sellers-preview")
    p.add_argument("--limit", type=int, default=36, choices=range(1, 49))
    p.set_defaults(func=cmd_best_sellers_preview)
    p = sub.add_parser("best-sellers-rebuild")
    p.add_argument("--limit", type=int, default=36, choices=range(1, 49))
    p.set_defaults(func=cmd_best_sellers_rebuild)
    p = sub.add_parser("request")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--data")
    p.add_argument("--wait-path")
    p.set_defaults(func=cmd_request)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        emit(args.func(args))
        return 0
    except (ApiError, json.JSONDecodeError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
