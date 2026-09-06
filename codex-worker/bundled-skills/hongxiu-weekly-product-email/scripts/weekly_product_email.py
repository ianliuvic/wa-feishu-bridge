#!/usr/bin/env python3
"""Create one hosted Hongxiu weekly-product email and a Zoho draft."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
SECRET_ENV = (
    "COLLECTOR_API_KEY", "GITHUB_TOKEN", "COOLIFY_BASE_URL", "COOLIFY_API_TOKEN",
    "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_MARKETING_CHAT_ID",
)


class WorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class Week:
    start: datetime
    end: datetime
    slug: str
    label: str


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def request(method: str, url: str, *, headers: dict[str, str] | None = None,
            json_body: Any = None, form: dict[str, Any] | None = None,
            timeout: int = 60, allow_404: bool = False) -> tuple[int, bytes, dict[str, str]]:
    req_headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; HongxiuWeeklyEmail/1.0; +https://wearhongxiu.com)",
        **(headers or {}),
    }
    body = None
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if allow_404 and exc.code == 404:
            return exc.code, raw, dict(exc.headers)
        message = raw.decode("utf-8", "replace")[:1200]
        raise WorkflowError(f"HTTP {exc.code} for {url}: {message}") from None
    except Exception as exc:
        raise WorkflowError(f"Request failed for {url}: {exc}") from None


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    status, raw, _ = request(method, url, **kwargs)
    if status == 404 and kwargs.get("allow_404"):
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Non-JSON response from {url}: {exc}") from None


def resolve_week(window_start: str | None, *, now: datetime | None = None) -> Week:
    """Resolve a complete Sunday 09:00-to-Sunday 09:00 Shanghai window."""
    if window_start:
        try:
            start_date = date.fromisoformat(window_start)
        except ValueError:
            raise WorkflowError("--window-start must use YYYY-MM-DD") from None
        if start_date.weekday() != 6:
            raise WorkflowError("--window-start must be a Sunday in Asia/Shanghai")
        start = datetime.combine(start_date, datetime_time(hour=9), tzinfo=SHANGHAI)
    else:
        current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        days_since_sunday = (current.weekday() + 1) % 7
        end_date = current.date() - timedelta(days=days_since_sunday)
        end = datetime.combine(end_date, datetime_time(hour=9), tzinfo=SHANGHAI)
        if current < end:
            end -= timedelta(days=7)
        start = end - timedelta(days=7)
    end = start + timedelta(days=7)
    iso_year, iso_week, _ = (end - timedelta(seconds=1)).date().isocalendar()
    return Week(start=start, end=end, slug=f"{iso_year}-W{iso_week:02d}-new-arrivals",
                label=f"{iso_year}-W{iso_week:02d}")


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def check_config() -> dict[str, Any]:
    configured = {name: bool(env(name)) for name in SECRET_ENV}
    return {
        "ok": all(configured.values()),
        "configured": configured,
        "routing": {
            "collector_url": bool(env("COLLECTOR_API_URL", "https://collector.yiswim.cloud")),
            "github_repo": bool(env("EMAIL_CAMPAIGN_REPO", "ianliuvic/email-campaign")),
            "coolify_base_url": bool(env("COOLIFY_BASE_URL")),
            "zoho_region": env("ZOHO_REGION", "cn") in {"cn", "com"},
        },
    }


def require_config(names: tuple[str, ...] = SECRET_ENV) -> None:
    missing = [name for name in names if not env(name)]
    if missing:
        raise WorkflowError("Missing required environment variables: " + ", ".join(missing))


def discover_products(week: Week, limit: int) -> list[dict[str, Any]]:
    require_config(("COLLECTOR_API_KEY",))
    base = env("COLLECTOR_API_URL", "https://collector.yiswim.cloud").rstrip("/")
    query = urllib.parse.urlencode({"from": iso(week.start), "to": iso(week.end), "limit": limit})
    url = f"{base}/api/marketing/weekly-new-products?{query}"
    completed = subprocess.run([
        "curl", "--fail-with-body", "--silent", "--show-error", "--max-time", "60",
        "--user-agent", "HongxiuWeeklyEmail/1.0", "--header",
        f"Authorization: Bearer {env('COLLECTOR_API_KEY')}", url,
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise WorkflowError("Collector request failed: " + (completed.stderr or completed.stdout)[:1200])
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Collector returned invalid JSON: {exc}") from None
    products = payload.get("products", []) if isinstance(payload, dict) else []
    required = {"title", "image_url", "wp_url", "listing_time"}
    clean = [product for product in products if required.issubset(product) and
             all(product.get(key) for key in required)]
    if len(clean) != len(products):
        raise WorkflowError("Collector returned a product missing a public title, image, URL, or listing time")
    return clean


def sentence(text: str, maximum: int = 210) -> str:
    value = " ".join((text or "").split())
    match = re.match(r"^(.+?[.!?])(?:\s|$)", value)
    value = match.group(1) if match else value
    if len(value) > maximum:
        value = value[: maximum - 1].rsplit(" ", 1)[0] + "…"
    return value


def render_products(products: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for offset in range(0, len(products), 2):
        cells: list[str] = []
        for product in products[offset:offset + 2]:
            title = html.escape(str(product["title"]))
            description = html.escape(sentence(str(product.get("description") or "")))
            style = html.escape(str(product.get("style_no") or "New style"))
            image_url = html.escape(str(product["image_url"]), quote=True)
            product_url = html.escape(str(product["wp_url"]), quote=True)
            cells.append(f'''<td class="stack" width="50%" valign="top" style="width:50%;padding:0 8px 28px;">
<a href="{product_url}" target="_blank"><img src="{image_url}" alt="{title}" width="250" style="display:block;width:100%;max-width:250px;height:auto;border:0;border-radius:8px;"></a>
<p style="margin:13px 0 5px;font:700 11px/16px Arial,sans-serif;letter-spacing:1.2px;text-transform:uppercase;color:#9B1B2E;">{style}</p>
<h2 style="margin:0 0 8px;font:normal 21px/27px Georgia,serif;color:#1F1D1A;">{title}</h2>
<p style="margin:0 0 14px;font:14px/22px Arial,sans-serif;color:#625D55;">{description}</p>
<a href="{product_url}" target="_blank" style="font:700 13px/20px Arial,sans-serif;color:#9B1B2E;text-decoration:underline;">View product →</a>
</td>''')
        if len(cells) == 1:
            cells.append('<td class="stack" width="50%" style="width:50%;padding:0 8px 28px;"></td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    return "\n".join(rows)


def products_fingerprint(products: list[dict[str, Any]]) -> str:
    return hashlib.sha256("\n".join(
        str(product.get("wp_url", "")) for product in products
    ).encode("utf-8")).hexdigest()[:16]


def render_footer() -> str:
    """Return the canonical footer shared with the wholesale swimwear campaign."""
    return '''<!-- ============ FOOTER ============ -->
<tr>
<td class="hm-card hm-px" style="background-color:#f4f4f5;padding:32px 40px;border:1px solid #dedee2;">
<p style="margin:0 0 6px 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#171719;">Hongxiu Clothing Co., Ltd.</p>
<p class="hm-muted" style="margin:0 0 4px 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;line-height:1.7;color:#5f6065;">10-8A Tiexi Rd, Xingcheng, Liaoning, China &nbsp;&middot;&nbsp; <a href="https://wearhongxiu.com" target="_blank" style="color:#a02025;font-weight:700;text-decoration:underline;text-underline-offset:3px;">wearhongxiu.com</a></p>
<p class="hm-muted" style="margin:0 0 16px 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;color:#5f6065;"><a href="mailto:service@wearhongxiu.com" style="color:#5f6065;text-decoration:underline;text-underline-offset:3px;">service@wearhongxiu.com</a> &nbsp;&middot;&nbsp; <a href="https://wa.me/8617711014152" target="_blank" style="color:#5f6065;text-decoration:underline;text-underline-offset:3px;">WhatsApp +86 177 1101 4152</a></p>
<p class="hm-faint" style="margin:0 0 14px 0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:11px;line-height:2;color:#8a8b90;"><a href="https://wearhongxiu.com/privacy-policy-2/" target="_blank" style="color:#8a8b90;text-decoration:underline;text-underline-offset:3px;">Privacy</a> &nbsp;&middot;&nbsp; <a href="https://wearhongxiu.com/shipping-policy/" target="_blank" style="color:#8a8b90;text-decoration:underline;text-underline-offset:3px;">Shipping</a> &nbsp;&middot;&nbsp; <a href="https://wearhongxiu.com/refund-policy/" target="_blank" style="color:#8a8b90;text-decoration:underline;text-underline-offset:3px;">Refund</a></p>
<p class="hm-faint" style="margin:0;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:11px;line-height:1.8;color:#8a8b90;">You are receiving this email because you contacted Hongxiu Clothing. If you&rsquo;d rather not hear from us, you can <a href="$[LI:UNSUBSCRIBE]$" style="color:#8a8b90;text-decoration:underline;text-underline-offset:3px;">unsubscribe here</a>.</p>
</td>
</tr>'''


def render_email(week: Week, products: list[dict[str, Any]]) -> str:
    count = len(products)
    product_rows = render_products(products)
    footer = render_footer()
    product_hash = products_fingerprint(products)
    rendered = f'''<!DOCTYPE html>
<!-- WEEKLY:{week.slug} -->
<!-- PRODUCTS:{product_hash} -->
<!-- CONTENT-VERSION:__CONTENT_VERSION__ -->
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hongxiu Weekly New Arrivals — {week.label}</title>
<style>body,table,td,a{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}}table,td{{mso-table-lspace:0;mso-table-rspace:0}}img{{-ms-interpolation-mode:bicubic}}body{{margin:0;padding:0}}@media(max-width:620px){{.container{{width:100%!important}}.stack{{display:block!important;width:100%!important}}.px{{padding-left:24px!important;padding-right:24px!important}}}}</style></head>
<body style="margin:0;padding:0;background:#F7F5F0;">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;opacity:0;overflow:hidden;">{count} newly listed swimwear styles, ready for wholesale sampling and customization.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F7F5F0;"><tr><td align="center" style="padding:28px 16px 48px;">
<table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;background:#FFFFFF;border-radius:12px;overflow:hidden;">
<tr><td align="center" style="padding:32px 40px 26px;"><a href="https://wearhongxiu.com" target="_blank"><img src="https://email.wearhongxiu.com/images/logo-hor.png" alt="Hongxiu Swim" width="180" style="display:block;width:180px;height:auto;border:0;"></a></td></tr>
<tr><td style="height:3px;background:#9B1B2E;"></td></tr>
<tr><td align="center" class="px" style="padding:50px 48px 18px;"><p style="margin:0 0 12px;font:700 12px/18px Arial,sans-serif;letter-spacing:2px;text-transform:uppercase;color:#9B1B2E;">Weekly New Arrivals · {week.label}</p><h1 style="margin:0 0 18px;font:normal 39px/45px Georgia,serif;color:#1F1D1A;">Fresh silhouettes for your next collection.</h1><p style="margin:0;font:15px/24px Arial,sans-serif;color:#625D55;">This week, we added {count} new swimwear styles to the Hongxiu catalog. Explore production-ready options for wholesale sampling, private labeling, and collection development.</p></td></tr>
<tr><td class="px" style="padding:30px 32px 8px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{product_rows}</table></td></tr>
<tr><td align="center" class="px" style="padding:14px 40px 50px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#9B1B2E" style="border-radius:6px;"><a href="https://wearhongxiu.com/new-arrivals/" target="_blank" style="display:inline-block;padding:15px 30px;font:700 14px/20px Arial,sans-serif;color:#FFF;">Explore New Arrivals</a></td></tr></table></td></tr>
{footer}
</table></td></tr></table></body></html>'''
    content_version = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]
    return rendered.replace("__CONTENT_VERSION__", content_version)


def rendered_content_version(rendered: str) -> str:
    match = re.search(r"<!-- CONTENT-VERSION:([a-f0-9]{16}) -->", rendered)
    if not match:
        raise WorkflowError("Rendered email is missing its content version marker")
    return match.group(1)


def versioned_content_url(content_url: str, content_version: str) -> str:
    separator = "&" if "?" in content_url else "?"
    return f"{content_url}{separator}{urllib.parse.urlencode({'v': content_version})}"


def github_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {env('GITHUB_TOKEN')}",
            "X-GitHub-Api-Version": "2022-11-28", "Accept": "application/vnd.github+json"}


def github_file(path: str) -> tuple[str | None, str | None]:
    base = env("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    repo = env("EMAIL_CAMPAIGN_REPO", "ianliuvic/email-campaign")
    branch = env("EMAIL_CAMPAIGN_BRANCH", "main")
    encoded = urllib.parse.quote(path, safe="/")
    payload = request_json("GET", f"{base}/repos/{repo}/contents/{encoded}?ref={urllib.parse.quote(branch)}",
                           headers=github_headers(), allow_404=True)
    if payload is None:
        return None, None
    return base64.b64decode(payload["content"]).decode("utf-8"), payload["sha"]


def put_github_file(path: str, content: str, message: str) -> str:
    base = env("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    repo = env("EMAIL_CAMPAIGN_REPO", "ianliuvic/email-campaign")
    branch = env("EMAIL_CAMPAIGN_BRANCH", "main")
    _, sha = github_file(path)
    body: dict[str, Any] = {"message": message, "content": base64.b64encode(content.encode()).decode(),
                            "branch": branch}
    if sha:
        body["sha"] = sha
    encoded = urllib.parse.quote(path, safe="/")
    payload = request_json("PUT", f"{base}/repos/{repo}/contents/{encoded}",
                           headers=github_headers(), json_body=body)
    return payload.get("commit", {}).get("sha", "")


def campaign_marker(week: Week, zoho: str = "PENDING") -> str:
    return f"<!-- WEEKLY:{week.slug}:ZOHO={zoho} -->"


def existing_zoho_key(readme: str, week: Week) -> str | None:
    match = re.search(rf"<!-- WEEKLY:{re.escape(week.slug)}:ZOHO=([^ ]+) -->", readme)
    if match and match.group(1) != "PENDING":
        return match.group(1)
    return None


def update_index(index: str, week: Week, products: list[dict[str, Any]]) -> str:
    marker = f"<!-- WEEKLY:{week.slug} -->"
    first = products[0]
    card = f'''      {marker}
      <div class="card">
        <img class="thumb" src="{html.escape(str(first['image_url']), quote=True)}" alt="Hongxiu Weekly New Arrivals {week.label}">
        <div class="body"><div class="badge">Draft</div><div class="name">Weekly New Arrivals — {week.label}</div>
          <div class="desc">{len(products)} newly listed and publicly available swimwear styles.</div>
          <div class="meta">{week.start:%Y-%m-%d %H:%M} to {week.end:%Y-%m-%d %H:%M} Asia/Shanghai · campaigns/{week.slug}/</div>
          <a class="open" href="/campaigns/{week.slug}/" target="_blank">Open email page ↗</a></div>
      </div>
'''
    if marker in index:
        pattern = (rf"      {re.escape(marker)}\r?\n      <div class=\"card\">.*?"
                   rf"      </div>\r?\n(?=      <!-- WEEKLY:|    </div>)")
        updated, count = re.subn(pattern, card, index, count=1, flags=re.DOTALL)
        if count != 1:
            raise WorkflowError("email-campaign index structure changed; campaign card update failed")
        return updated
    needle = '    </div>\n\n    <div class="divider"></div>\n    <h2>How to add a new campaign</h2>'
    if needle not in index:
        raise WorkflowError("email-campaign index structure changed; campaign card insertion point not found")
    return index.replace(needle, card + needle, 1)


def update_readme(readme: str, week: Week, zoho: str = "PENDING") -> str:
    marker_pattern = rf"<!-- WEEKLY:{re.escape(week.slug)}:ZOHO=[^ ]+ -->"
    marker = campaign_marker(week, zoho)
    if re.search(marker_pattern, readme):
        updated = re.sub(marker_pattern, marker, readme, count=1)
        if zoho != "PENDING":
            row_pattern = rf"(\| Weekly New Arrivals — {re.escape(week.label)} \| `campaigns/{re.escape(week.slug)}/` \| <https://email\.wearhongxiu\.com/campaigns/{re.escape(week.slug)}/> \| )[^|]+(\| Draft（未发送） \|)"
            updated = re.sub(row_pattern, rf"\g<1>{zoho} \g<2>", updated, count=1)
        return updated
    row = (f"| Weekly New Arrivals — {week.label} | `campaigns/{week.slug}/` | "
           f"<https://email.wearhongxiu.com/campaigns/{week.slug}/> | {zoho} | Draft（未发送） |\n"
           f"{marker}\n")
    needle = "\n## 新建邮件的流程"
    if needle not in readme:
        raise WorkflowError("email-campaign README structure changed; campaign table insertion point not found")
    return readme.replace(needle, "\n" + row + needle, 1)


def trigger_deploy() -> None:
    base = env("COOLIFY_BASE_URL").rstrip("/")
    uuid = env("EMAIL_CAMPAIGN_COOLIFY_UUID", "e1ps8v0988ns004bqyz330ct")
    url = f"{base}/deploy?{urllib.parse.urlencode({'uuid': uuid})}"
    request_json("GET", url, headers={"Authorization": f"Bearer {env('COOLIFY_API_TOKEN')}"})


def wait_for_page(url: str, marker: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            cache_bust = urllib.parse.urlencode({"verify": int(time.time())})
            _, raw, _ = request("GET", f"{url}?{cache_bust}", headers={"Accept": "text/html"}, timeout=20)
            if marker.encode() in raw:
                return
            last_error = "page is reachable but the new marker is not deployed"
        except WorkflowError as exc:
            last_error = str(exc)
        time.sleep(5)
    raise WorkflowError(f"Hosted email verification timed out: {last_error}")


def zoho_access_token() -> str:
    region = env("ZOHO_REGION", "cn")
    accounts = "https://accounts.zoho.com.cn" if region == "cn" else "https://accounts.zoho.com"
    payload = request_json("POST", f"{accounts}/oauth/v2/token", form={
        "refresh_token": env("ZOHO_REFRESH_TOKEN"), "client_id": env("ZOHO_CLIENT_ID"),
        "client_secret": env("ZOHO_CLIENT_SECRET"), "grant_type": "refresh_token",
    })
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise WorkflowError("Zoho token refresh did not return an access token")
    return token


def create_zoho_draft(week: Week, products: list[dict[str, Any]], content_url: str,
                      content_version: str) -> str:
    region = env("ZOHO_REGION", "cn")
    base = "https://campaigns.zoho.com.cn/api/v1.1" if region == "cn" else "https://campaigns.zoho.com/api/v1.1"
    token = zoho_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    lists = request_json("GET", f"{base}/getmailinglists?resfmt=JSON", headers=headers)
    list_name = env("ZOHO_CAMPAIGNS_LIST_NAME", "CAM-03")
    matches = [item for item in lists.get("list_of_details", []) if item.get("listname") == list_name]
    if len(matches) != 1:
        raise WorkflowError(f"Expected exactly one Zoho mailing list named {list_name!r}, found {len(matches)}")
    topics = request_json("GET", f"{base}/topics?resfmt=JSON", headers=headers)
    topic_name = env("ZOHO_CAMPAIGNS_TOPIC_NAME", "Marketing")
    topic_matches = [item for item in topics.get("topicDetails", []) if item.get("topicName") == topic_name]
    if len(topic_matches) != 1:
        raise WorkflowError(f"Expected exactly one Zoho topic named {topic_name!r}, found {len(topic_matches)}")
    count = len(products)
    payload = request_json("POST", f"{base}/createCampaign", headers=headers, form={
        "resfmt": "JSON",
        "campaignname": f"Hongxiu Weekly New Arrivals — {week.label}",
        "from_email": env("ZOHO_CAMPAIGNS_FROM_EMAIL", "service@wearhongxiu.com"),
        "from_name": env("ZOHO_CAMPAIGNS_FROM_NAME", "Hongxiu Swim"),
        "subject": f"New This Week: {count} Swimwear Styles for Your Next Collection",
        "content_url": versioned_content_url(content_url, content_version),
        "list_details": json.dumps({matches[0]["listkey"]: []}, separators=(",", ":")),
        "topicId": topic_matches[0]["topicId"],
    }, timeout=120)
    if str(payload.get("code")) != "200" or not payload.get("campaignKey"):
        raise WorkflowError("Zoho did not confirm draft creation: " + json.dumps(payload, ensure_ascii=False)[:1200])
    return str(payload["campaignKey"])


def zoho_campaign_status(campaign_key: str) -> str:
    region = env("ZOHO_REGION", "cn")
    base = "https://campaigns.zoho.com.cn/api/v1.1" if region == "cn" else "https://campaigns.zoho.com/api/v1.1"
    payload = request_json("GET", f"{base}/getcampaigndetails?{urllib.parse.urlencode({'resfmt': 'JSON', 'campaignkey': campaign_key})}",
                           headers={"Authorization": f"Zoho-oauthtoken {zoho_access_token()}"})
    if str(payload.get("code")) in {"2206", "6301"} and payload.get("status") == "error":
        return "Missing"
    if str(payload.get("code")) != "0" or payload.get("status") != "success":
        raise WorkflowError("Zoho did not return campaign details")
    status = str(payload.get("campaign_status") or "").strip()
    if not status:
        raise WorkflowError("Zoho campaign details did not include campaign_status")
    return status


def delete_zoho_draft(campaign_key: str) -> None:
    if zoho_campaign_status(campaign_key).casefold() != "draft":
        raise WorkflowError("Refusing to delete a Zoho campaign that is no longer a draft")
    region = env("ZOHO_REGION", "cn")
    base = "https://campaigns.zoho.com.cn/api/v1.1" if region == "cn" else "https://campaigns.zoho.com/api/v1.1"
    query = urllib.parse.urlencode({"resfmt": "JSON", "campaignkey": campaign_key})
    payload = request_json("GET", f"{base}/deletecampaign?{query}",
                           headers={"Authorization": f"Zoho-oauthtoken {zoho_access_token()}"})
    if str(payload.get("code")) not in {"0", "200"} and payload.get("status") != "success":
        raise WorkflowError("Zoho did not confirm draft deletion")


def send_feishu(text: str) -> str:
    token_payload = request_json("POST", "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                                 json_body={"app_id": env("FEISHU_APP_ID"),
                                            "app_secret": env("FEISHU_APP_SECRET")})
    token = token_payload.get("tenant_access_token")
    if not token:
        raise WorkflowError("Feishu tenant token request failed")
    query = urllib.parse.urlencode({"receive_id_type": "chat_id"})
    payload = request_json("POST", f"https://open.feishu.cn/open-apis/im/v1/messages?{query}",
                           headers={"Authorization": f"Bearer {token}"}, json_body={
                               "receive_id": env("FEISHU_MARKETING_CHAT_ID"), "msg_type": "text",
                               "content": json.dumps({"text": text}, ensure_ascii=False),
                           })
    if payload.get("code") != 0:
        raise WorkflowError("Feishu did not confirm notification delivery")
    return str(payload.get("data", {}).get("message_id", ""))


def run_workflow(args: argparse.Namespace) -> dict[str, Any]:
    week = resolve_week(args.window_start)
    products = discover_products(week, args.limit)
    if not products:
        if args.dry_run:
            return {"status": "no_products", "week": week.label, "product_count": 0, "writes": False}
        require_config(("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_MARKETING_CHAT_ID"))
        message_id = send_feishu(f"Hongxiu 每周新品邮件：{week.label} 没有符合条件的新上架且已发布商品，本周未创建 HTML 或 Zoho 草稿。")
        return {"status": "no_products", "week": week.label, "product_count": 0,
                "writes": False, "feishu_message_id": message_id}

    rendered = render_email(week, products)
    content_version = rendered_content_version(rendered)
    if args.dry_run:
        output = Path(args.output or f"/workspace/codex-artifacts/{week.slug}/index.html")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        return {"status": "dry_run", "week": week.label, "product_count": len(products),
                "artifact": str(output)}

    require_config()
    readme, _ = github_file("README.md")
    index, _ = github_file("index.html")
    if readme is None or index is None:
        raise WorkflowError("Could not load README.md and index.html from email-campaign")
    prior_key = existing_zoho_key(readme, week)
    content_url = f"{env('EMAIL_CAMPAIGN_BASE_URL', 'https://email.wearhongxiu.com').rstrip('/')}/campaigns/{week.slug}/"
    path = f"campaigns/{week.slug}/index.html"
    campaign_status = zoho_campaign_status(prior_key) if prior_key else ""
    if campaign_status.casefold() == "missing":
        prior_key = None
    if prior_key:
        existing_content, _ = github_file(path)
        if existing_content == rendered:
            message_id = send_feishu(
                f"Hongxiu 每周新品邮件：{week.label} 内容未变化，继续复用现有 Zoho {campaign_status}。\n"
                f"统计窗口：{week.start:%Y-%m-%d %H:%M} 至 {week.end:%Y-%m-%d %H:%M}（Asia/Shanghai）\n"
                f"符合条件商品：{len(products)} 件\n预览：{content_url}")
            return {"status": "unchanged", "week": week.label, "window_start": iso(week.start),
                    "window_end": iso(week.end), "product_count": len(products),
                    "content_url": content_url, "zoho_campaign_key": prior_key,
                    "zoho_campaign_status": campaign_status, "feishu_message_id": message_id}
        if campaign_status.casefold() != "draft":
            message_id = send_feishu(
                f"Hongxiu 每周新品邮件：{week.label} 商品清单已有变化，但 Zoho Campaign 状态为 {campaign_status}，"
                f"为避免修改已发送或已排期内容，本次已锁定且未改写。\n预览：{content_url}")
            return {"status": "locked", "week": week.label, "window_start": iso(week.start),
                    "window_end": iso(week.end), "product_count": len(products),
                    "content_url": content_url, "zoho_campaign_key": prior_key,
                    "zoho_campaign_status": campaign_status, "feishu_message_id": message_id}

        updated_index = update_index(index, week, products)
        put_github_file(path, rendered, f"Refresh {week.label} weekly new-arrivals email")
        if updated_index != index:
            put_github_file("index.html", updated_index, f"Refresh {week.label} weekly email index")
        trigger_deploy()
        wait_for_page(content_url, f"CONTENT-VERSION:{content_version}", args.deploy_timeout)
        replacement_key = create_zoho_draft(week, products, content_url, content_version)
        if zoho_campaign_status(prior_key).casefold() != "draft":
            delete_zoho_draft(replacement_key)
            raise WorkflowError("Existing Zoho campaign changed state during refresh; replacement draft was removed")
        latest_readme, _ = github_file("README.md")
        if latest_readme is None:
            delete_zoho_draft(replacement_key)
            raise WorkflowError("Could not reload README.md while refreshing Zoho draft")
        put_github_file("README.md", update_readme(latest_readme, week, replacement_key),
                        f"Replace Zoho draft for {week.label}")
        cleanup_warning = ""
        try:
            delete_zoho_draft(prior_key)
        except WorkflowError as exc:
            cleanup_warning = f"；旧草稿未能自动清理：{exc}"
        message_id = send_feishu(
            f"Hongxiu 每周新品邮件已刷新：{week.label}\n"
            f"统计窗口：{week.start:%Y-%m-%d %H:%M} 至 {week.end:%Y-%m-%d %H:%M}（Asia/Shanghai）\n"
            f"符合条件商品：{len(products)} 件\n预览：{content_url}\n"
            f"新 Zoho Campaigns 草稿：{replacement_key}{cleanup_warning}\n邮件尚未发送。")
        return {"status": "refreshed", "week": week.label, "window_start": iso(week.start),
                "window_end": iso(week.end), "product_count": len(products),
                "content_url": content_url, "zoho_campaign_key": replacement_key,
                "old_zoho_campaign_key": prior_key, "cleanup_warning": cleanup_warning,
                "feishu_message_id": message_id}

    updated_index = update_index(index, week, products)
    updated_readme = update_readme(readme, week, "PENDING")
    put_github_file(path, rendered, f"Add {week.label} weekly new-arrivals email")
    if updated_index != index:
        put_github_file("index.html", updated_index, f"Index {week.label} weekly email")
    if updated_readme != readme:
        put_github_file("README.md", updated_readme, f"Register {week.label} weekly email")
    trigger_deploy()
    wait_for_page(content_url, f"CONTENT-VERSION:{content_version}", args.deploy_timeout)
    zoho_key = create_zoho_draft(week, products, content_url, content_version)
    latest_readme, _ = github_file("README.md")
    if latest_readme is None:
        raise WorkflowError("Could not reload README.md after Zoho draft creation")
    final_readme = update_readme(latest_readme, week, zoho_key)
    put_github_file("README.md", final_readme, f"Record Zoho draft for {week.label}")
    message = (f"Hongxiu 每周新品邮件已完成：{week.label}\n"
               f"统计窗口：{week.start:%Y-%m-%d %H:%M} 至 {week.end:%Y-%m-%d %H:%M}（Asia/Shanghai）\n"
               f"符合条件商品：{len(products)} 件\n预览：{content_url}\n"
               f"Zoho Campaigns 草稿：{zoho_key}\n邮件尚未发送。")
    message_id = send_feishu(message)
    return {"status": "completed", "week": week.label, "window_start": iso(week.start),
            "window_end": iso(week.end), "product_count": len(products),
            "content_url": content_url, "zoho_campaign_key": zoho_key,
            "feishu_message_id": message_id}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    discover = sub.add_parser("discover")
    discover.add_argument("--window-start", "--week-start", dest="window_start")
    discover.add_argument("--limit", type=int, default=24)
    run = sub.add_parser("run")
    run.add_argument("--window-start", "--week-start", dest="window_start")
    run.add_argument("--limit", type=int, default=24)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--output")
    run.add_argument("--deploy-timeout", type=int, default=240)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "check":
            result = check_config()
        elif args.command == "discover":
            week = resolve_week(args.window_start)
            products = discover_products(week, args.limit)
            result = {"week": week.label, "from": iso(week.start), "to": iso(week.end),
                      "count": len(products), "products": products}
        else:
            result = run_workflow(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except WorkflowError as exc:
        if args.command == "run" and not getattr(args, "dry_run", False) and all(env(name) for name in
                ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_MARKETING_CHAT_ID")):
            try:
                send_feishu(f"Hongxiu 每周新品邮件执行失败：{exc}")
            except Exception:
                pass
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
