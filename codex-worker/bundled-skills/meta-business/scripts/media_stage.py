#!/usr/bin/env python3
"""Preflight local/remote media and stage local files for Meta publishing."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from meta_graph import MetaGraphError, _configured_value, print_json, require_public_https_url, require_write_confirmation


MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024
MAX_COVER_BYTES = 30 * 1024 * 1024
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-m4v"}
COVER_MIMES = {"image/jpeg", "image/png"}


def _is_url(value: str) -> bool:
    return urlparse(value).scheme.lower() in {"http", "https"}


def _local_path(value: str) -> Path | None:
    if _is_url(value):
        return None
    return Path(value).expanduser().resolve()


def _probe_with_ffprobe(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if not executable:
        return {"available": False}
    command = [
        executable, "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt",
        "-of", "json", str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "error": str(exc)}
    if completed.returncode:
        return {"available": True, "error": completed.stderr.strip()[-1000:]}
    try:
        return {"available": True, **json.loads(completed.stdout)}
    except json.JSONDecodeError:
        return {"available": True, "error": "ffprobe returned invalid JSON"}


def _video_warnings(probe: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    codec = str(video.get("codec_name") or "").lower()
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if codec and codec != "h264":
        warnings.append(f"codec is {codec}; H.264 is recommended to reduce Meta recompression")
    if width and height:
        ratio = width / height
        if abs(ratio - 9 / 16) > 0.02:
            warnings.append(f"aspect ratio is {width}:{height}; 9:16 (1080x1920) is recommended for Reels")
        if width < 720 or height < 1280:
            warnings.append("resolution is below 720x1280 and may look soft after Meta processing")
    if video.get("pix_fmt") and str(video["pix_fmt"]) not in {"yuv420p", "yuvj420p"}:
        warnings.append(f"pixel format is {video['pix_fmt']}; yuv420p is the safest delivery format")
    return warnings


def preflight_local(value: str, kind: str) -> dict[str, Any]:
    path = _local_path(value)
    if path is None or not path.is_file():
        raise MetaGraphError(f"local {kind} is not a readable file: {value}")
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    maximum = MAX_VIDEO_BYTES if kind == "video" else MAX_COVER_BYTES
    allowed = VIDEO_MIMES if kind == "video" else COVER_MIMES
    if not 0 < size <= maximum:
        raise MetaGraphError(f"{kind} size must be between 1 byte and {maximum} bytes")
    if mime not in allowed:
        raise MetaGraphError(f"unsupported {kind} MIME type: {mime}")
    probe = _probe_with_ffprobe(path)
    warnings = _video_warnings(probe) if kind == "video" else []
    if not probe.get("available"):
        warnings.append("ffprobe is unavailable; codec, dimensions, frame rate, and duration were not checked")
    elif probe.get("error"):
        warnings.append("ffprobe could not fully inspect this file")
    return {
        "source": "local", "path": str(path), "size_bytes": size, "mime_type": mime,
        "sha256": _sha256_file(path), "probe": probe, "warnings": warnings,
    }


def preflight_url(value: str, kind: str) -> dict[str, Any]:
    url = require_public_https_url(value, f"{kind} URL")
    request = Request(url, method="HEAD", headers={"User-Agent": "meta-business-skill/1.1"})
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            headers = response.headers
    except HTTPError as exc:
        if exc.code not in {403, 405}:
            raise MetaGraphError(f"{kind} URL returned HTTP {exc.code}") from exc
        request = Request(url, method="GET", headers={"Range": "bytes=0-0", "User-Agent": "meta-business-skill/1.1"})
        try:
            with urlopen(request, timeout=30) as response:
                status = response.status
                headers = response.headers
                response.read(1)
        except (HTTPError, URLError, TimeoutError) as nested:
            raise MetaGraphError(f"could not fetch public {kind} URL: {nested}") from nested
    except (URLError, TimeoutError) as exc:
        raise MetaGraphError(f"could not fetch public {kind} URL: {exc}") from exc
    content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].lower()
    content_length = headers.get("Content-Length")
    return {
        "source": "url", "url": url, "http_status": status,
        "content_type": content_type or None,
        "content_length": int(content_length) if content_length and content_length.isdigit() else None,
        "warnings": [] if content_type else ["remote server did not provide Content-Type"],
    }


def preflight(value: str, kind: str) -> dict[str, Any]:
    return preflight_url(value, kind) if _is_url(value) else preflight_local(value, kind)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hmac(key: bytes | str, message: str) -> bytes:
    raw = key.encode() if isinstance(key, str) else key
    return hmac.new(raw, message.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str) -> bytes:
    key = _hmac(("AWS4" + secret).encode(), date)
    key = _hmac(key, "auto")
    key = _hmac(key, "s3")
    return _hmac(key, "aws4_request")


def _r2_config() -> dict[str, str]:
    values = {
        "account_id": _configured_value("META_MEDIA_R2_ACCOUNT_ID", "media_r2_account_id"),
        "access_key": _configured_value("META_MEDIA_R2_ACCESS_KEY_ID", "media_r2_access_key_id"),
        "secret": _configured_value("META_MEDIA_R2_SECRET_ACCESS_KEY", "media_r2_secret_access_key"),
        "bucket": _configured_value("META_MEDIA_R2_BUCKET", "media_r2_bucket"),
        "public_base": _configured_value("META_MEDIA_R2_PUBLIC_BASE_URL", "media_r2_public_base_url"),
        "prefix": _configured_value("META_MEDIA_R2_PREFIX", "media_r2_prefix", "marketing/meta-publish"),
    }
    if not all(values[key] for key in ("account_id", "access_key", "secret")):
        derived = _derive_r2_from_cloudflare_token()
        if derived:
            values.update(derived)
    missing = [key for key in ("account_id", "access_key", "secret", "bucket") if not values[key]]
    if missing:
        raise MetaGraphError("local media staging requires R2 configuration: " + ", ".join(missing))
    if values["public_base"]:
        require_public_https_url(values["public_base"], "R2 public base URL")
    return values


def _derive_r2_from_cloudflare_token() -> dict[str, str]:
    """Use the existing Cloudflare skill credential without persisting or printing it."""
    candidates = [Path.home() / ".cloudflare" / "config.json", Path.home() / ".config" / "cloudflare" / "config.json"]
    config_path = next((path for path in candidates if path.is_file()), None)
    if config_path is None:
        return {}
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        token = str(value.get("token") or "").strip()
        base = str(value.get("base_url") or "https://api.cloudflare.com/client/v4").rstrip("/")
    except (OSError, json.JSONDecodeError):
        return {}
    if not token:
        return {}

    def api(path: str) -> dict[str, Any]:
        request = Request(base + path, headers={"Authorization": f"Bearer {token}", "User-Agent": "meta-business-skill/1.1"})
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        if not isinstance(result, dict) or not result.get("success"):
            raise MetaGraphError("Cloudflare credential could not be used for R2 staging")
        return result

    try:
        verified = api("/user/tokens/verify")
        accounts = api("/accounts")
        token_id = str((verified.get("result") or {}).get("id") or "")
        account_rows = accounts.get("result") or []
        account_id = str(account_rows[0].get("id") or "") if account_rows else ""
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise MetaGraphError(f"could not derive R2 staging credentials from Cloudflare config: {exc}") from exc
    if not token_id or not account_id:
        raise MetaGraphError("Cloudflare config did not resolve an account and token ID for R2 staging")
    return {"account_id": account_id, "access_key": token_id, "secret": hashlib.sha256(token.encode()).hexdigest()}


def _object_key(path: Path, digest: str, prefix: str) -> str:
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", path.stem).strip("-.") or "media"
    suffix = path.suffix.lower()
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return "/".join(part.strip("/") for part in (prefix, day, f"{digest[:16]}-{safe_stem}{suffix}") if part)


def _r2_put(path: Path, mime: str, key: str, config: dict[str, str]) -> None:
    host = f"{config['account_id']}.r2.cloudflarestorage.com"
    canonical_uri = "/" + quote(config["bucket"], safe="") + "/" + quote(key, safe="/-_.~")
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    payload_hash = _sha256_file(path)
    canonical_headers = (
        f"content-type:{mime}\n" f"host:{host}\n" f"x-amz-content-sha256:{payload_hash}\n" f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash])
    scope = f"{date}/auto/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(_signing_key(config["secret"], date), string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={config['access_key']}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    connection = http.client.HTTPSConnection(host, timeout=180)
    try:
        connection.putrequest("PUT", canonical_uri)
        connection.putheader("Authorization", authorization)
        connection.putheader("Content-Type", mime)
        connection.putheader("Content-Length", str(path.stat().st_size))
        connection.putheader("x-amz-content-sha256", payload_hash)
        connection.putheader("x-amz-date", amz_date)
        connection.endheaders()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        raw = response.read().decode("utf-8", "replace")
        if response.status not in {200, 201}:
            raise MetaGraphError(f"R2 staging upload returned HTTP {response.status}: {raw[-500:]}")
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        raise MetaGraphError(f"R2 staging upload failed: {exc}") from exc
    finally:
        connection.close()


def check_r2_access() -> dict[str, Any]:
    """Verify configured bucket access with a signed, read-only ListObjects request."""
    config = _r2_config()
    host = f"{config['account_id']}.r2.cloudflarestorage.com"
    uri = "/" + quote(config["bucket"], safe="")
    query = urlencode(sorted({"list-type": "2", "max-keys": "1", "prefix": config["prefix"]}.items()), quote_via=quote, safe="-_.~")
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(["GET", uri, query, canonical_headers, signed_headers, payload_hash])
    scope = f"{date}/auto/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(_signing_key(config["secret"], date), string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={config['access_key']}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    request = Request(
        f"https://{host}{uri}?{query}", method="GET",
        headers={"Authorization": authorization, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date},
    )
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
            status = response.status
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise MetaGraphError(f"R2 bucket access returned HTTP {exc.code}: {raw[-500:]}") from exc
    except (URLError, TimeoutError) as exc:
        raise MetaGraphError(f"R2 bucket access check failed: {exc}") from exc
    return {"accessible": True, "http_status": status, "bucket": config["bucket"], "prefix": config["prefix"]}


def _presigned_get(config: dict[str, str], key: str, expires: int = 86400) -> str:
    expires = max(900, min(expires, 604800))
    host = f"{config['account_id']}.r2.cloudflarestorage.com"
    uri = "/" + quote(config["bucket"], safe="") + "/" + quote(key, safe="/-_.~")
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    scope = f"{date}/auto/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{config['access_key']}/{scope}",
        "X-Amz-Date": amz_date, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host",
    }
    canonical_query = urlencode(sorted(query.items()), quote_via=quote, safe="-_.~")
    canonical_request = "\n".join(["GET", uri, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    query["X-Amz-Signature"] = hmac.new(
        _signing_key(config["secret"], date), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return f"https://{host}{uri}?" + urlencode(query, quote_via=quote, safe="-_.~")


def stage_local(value: str, kind: str, expires: int = 86400) -> dict[str, Any]:
    info = preflight_local(value, kind)
    path = Path(info["path"])
    config = _r2_config()
    key = _object_key(path, info["sha256"], config["prefix"])
    _r2_put(path, info["mime_type"], key, config)
    if config["public_base"]:
        url = config["public_base"].rstrip("/") + "/" + quote(key, safe="/-_.~")
    else:
        url = _presigned_get(config, key, expires)
    accessibility = preflight_url(url, kind)
    return {"staged": True, "bucket": config["bucket"], "key": key, "url": url, "preflight": info, "accessibility": accessibility}


def resolve_public_media(value: str, kind: str, live: bool) -> tuple[str | None, dict[str, Any]]:
    if _is_url(value):
        info = preflight_url(value, kind)
        return value, info
    info = preflight_local(value, kind)
    if not live:
        return None, info
    staged = stage_local(value, kind)
    return str(staged["url"]), staged


def download_cover(value: str) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    path = _local_path(value)
    if path is not None:
        preflight_local(value, "cover")
        return path, None
    info = preflight_url(value, "cover")
    temporary = tempfile.TemporaryDirectory(prefix="meta-cover-")
    suffix = ".png" if info.get("content_type") == "image/png" else ".jpg"
    destination = Path(temporary.name) / f"cover{suffix}"
    request = Request(value, headers={"User-Agent": "meta-business-skill/1.1"})
    try:
        with urlopen(request, timeout=60) as response, destination.open("wb") as handle:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_COVER_BYTES:
                    raise MetaGraphError("remote cover exceeds 30 MB")
                handle.write(chunk)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        temporary.cleanup()
        raise MetaGraphError(f"could not download Page Reel cover: {exc}") from exc
    preflight_local(str(destination), "cover")
    return destination, temporary


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight and stage Meta publishing media")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-staging", help="verify configured R2 bucket access without writing")
    check = commands.add_parser("preflight")
    check.add_argument("source")
    check.add_argument("--kind", choices=["video", "cover"], required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("source")
    stage.add_argument("--kind", choices=["video", "cover"], required=True)
    stage.add_argument("--expires", type=int, default=86400)
    stage.add_argument("--confirm-write", action="store_true")
    stage.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "check-staging":
            print_json(check_r2_access())
        elif args.command == "preflight":
            print_json(preflight(args.source, args.kind))
        else:
            require_write_confirmation(args.confirm_write, args.dry_run, "media staging")
            if args.dry_run:
                print_json({"dry_run": True, "preflight": preflight_local(args.source, args.kind)})
            else:
                print_json(stage_local(args.source, args.kind, args.expires))
    except MetaGraphError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
